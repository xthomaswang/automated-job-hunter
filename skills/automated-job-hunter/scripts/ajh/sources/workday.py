"""Workday career sites, read through the JSON their own public pages load. No login, no key.

A site lists postings 20 at a time without their text (`POST /wday/cxs/<tenant>/<site>/jobs`), and
each posting's text is one more request. So a site is read through searches (`queries`, by default
the wanted roles), every matching posting is listed, and text is fetched only for the postings the
rank function asks for, at most `max_details` per update, one request a second. docs/spikes.md (E8)
records what the endpoints returned when this was written; this is not a versioned API, so any
change of shape fails the source and keeps its last good snapshot.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from ..http import Client, HttpError
from ..model import Job, SourceError
from ..textutil import html_to_text

KINDS = ("workday",)
HOST = re.compile(r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]+\Z")
_PATH = re.compile(r"/job/[^?#\\\s]+\Z")
_ID = re.compile(r"_([A-Za-z0-9-]+)\Z")
PAGE = 20  # the most a site returns at once
MAX_PER_QUERY = 1000  # a search matching more is too broad to read every update
DETAILS = 100  # texts fetched per source per update, unless the source sets max_details
DELAY = 1.0  # seconds between requests to one site


def site(source: Mapping[str, Any]) -> tuple[str, str, str]:
    """(host, site, cxs root) of a source, checked so that no request goes anywhere else."""
    host, name = source.get("host") or "", source.get("site") or ""
    found = HOST.match(host)
    tenant = source.get("tenant") or (found.group(1) if found else "")
    if not found or not _TOKEN.match(name) or not _TOKEN.match(tenant):
        raise SourceError(f"Workday needs host <tenant>.wd<N>.myworkdayjobs.com and a site name, not {host!r}/{name!r}")
    return host, name, f"https://{host}/wday/cxs/{tenant}/{name}"


def search(client: Client, root: str, query: str, limit: int = PAGE, offset: int = 0) -> dict[str, Any]:
    reply = client.post(
        root + "/jobs", json_body={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": query}
    )
    data = reply.raise_for_status().json()
    if (
        not isinstance(data, dict)
        or type(data.get("total")) is not int
        or not isinstance(data.get("jobPostings"), list)
    ):
        raise SourceError("Workday listing is not in the expected shape")
    return data


def _listing(client: Client, root: str, query: str) -> list[dict[str, Any]]:
    """Every posting one search matches. Anything short of its total aborts the snapshot."""
    rows: dict[str, dict[str, Any]] = {}
    total: int | None = None
    offset = 0
    while True:
        data = search(client, root, query, offset=offset)
        if total is None:
            total = data["total"]
            if total > MAX_PER_QUERY:
                raise SourceError(
                    f"Workday search {query!r} matches {total} postings, more than {MAX_PER_QUERY}: "
                    "use narrower queries"
                )
        elif data["total"] not in (0, total):  # later pages report 0; any other number means it moved
            raise SourceError("Workday listing changed while it was read; the next update retries")
        batch = data["jobPostings"]
        for row in batch:
            path = row.get("externalPath") if isinstance(row, dict) else None
            if not isinstance(path, str) or not _PATH.match(path) or ".." in path:
                raise SourceError("Workday posting path is missing or unsafe")
            rows.setdefault(path, row)
        offset += len(batch)
        if not batch or offset >= total:
            break
    if len(rows) != total:
        raise SourceError("Workday listing ended short of its total; the next update retries")
    return list(rows.values())


def _listed(source: Mapping[str, Any], host: str, name: str, row: Mapping[str, Any]) -> Job:
    """A posting as the listing shows it. Its title and place are the ones change is measured on."""
    path = row["externalPath"]
    found = _ID.search(path)
    jid = found.group(1) if found else path.rstrip("/").rsplit("/", 1)[-1]
    if not _TOKEN.match(jid):
        raise SourceError(f"Workday posting id {jid!r} is not a plain token")
    return Job(
        key=f"{source['id']}:{jid}",
        source=source["id"],
        source_id=jid,
        company=source["company"],
        title=row.get("title") or "",
        location=row.get("locationsText") or "unknown",
        url=f"https://{host}/{name}{path}",
    )


def _detail(client: Client, root: str, job: Job, path: str) -> Job | None:
    """The posting with its text, or None when the site would not give it this time."""
    try:
        data = client.get(root + path).raise_for_status().json()
    except (HttpError, OSError, ValueError):
        return None
    info = data.get("jobPostingInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict) or info.get("posted") is False:
        return None
    text = html_to_text(info.get("jobDescription"))
    if not text:
        return None
    # The employment type stays in the text, not in `employment_type`: stage 0 must see the same facts
    # every update, and most updates have only the listing. Otherwise a posting it drops for being
    # part-time would look new again next time, and its text would be fetched again and again.
    places = [p for p in [info.get("location"), *(info.get("additionalLocations") or [])] if isinstance(p, str) and p]
    kind = info.get("timeType")
    head = [
        *(["Locations: " + " / ".join(dict.fromkeys(places))] if len(places) > 1 else []),  # the listing: "2 Locations"
        *([f"Time type: {kind}"] if isinstance(kind, str) and kind else []),
    ]
    started = info.get("startDate")
    return replace(
        job,
        description="\n".join(head) + "\n\n" + text if head else text,
        posted_at=started if isinstance(started, str) else None,
    )


def fetch(client: Client, source: Mapping[str, Any], rank: Callable[[Job], int | None] | None, new: int) -> list[Job]:
    host, name, root = site(source)
    client.space(host, DELAY)
    queries = source.get("queries")
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise SourceError("Workday is read through searches: set queries, or wanted roles in the criteria")
    listed: dict[str, tuple[Job, str]] = {}
    for query in dict.fromkeys(q.strip() for q in queries):
        for row in _listing(client, root, query):
            job = _listed(source, host, name, row)
            listed.setdefault(job.source_id, (job, row["externalPath"]))

    ranks = {jid: rank(job) for jid, (job, _) in listed.items()} if rank else {}
    wanted = sorted((r, jid) for jid, r in ranks.items() if r is not None)
    budget = source.get("max_details", DETAILS)
    detailed: dict[str, Job] = {}
    for _, jid in wanted[: budget if isinstance(budget, int) and budget >= 0 else DETAILS]:
        job, path = listed[jid]
        found = _detail(client, root, job, path)
        if found is not None:
            detailed[jid] = found
    jobs = []
    for jid, (job, _) in listed.items():
        if jid in detailed:
            jobs.append(detailed[jid])
        elif ranks.get(jid) != new:  # a new posting waits for its text; nothing knew it, so nothing closes
            jobs.append(replace(job, detail_skipped=True))
    return jobs
