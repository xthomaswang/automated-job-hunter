"""Workday career sites, read through the JSON their own public pages load. No login, no key.

A site lists postings 20 at a time without their text (`POST /wday/cxs/<tenant>/<site>/jobs`), and
each posting's text is one more request. So a site is read through searches (`queries`, by default
the wanted roles), every matching posting is listed, and text is fetched only for the postings the
rank function asks for, at most `max_details` per update, one request a second. docs/spikes.md (E8)
records what the endpoints returned when this was written; this is not a versioned API, so any
change of shape fails the source and keeps its last good snapshot. The exception is a row with
neither a link nor a title, as a site may list a half-published posting: it is skipped and counted
(`skips`) while such rows are few.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from ..http import Client, HttpError
from ..model import Job, SourceError
from ..textutil import html_to_text
from . import lazy, skips

KINDS = ("workday",)
HOST = re.compile(r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com\Z")  # <tenant>.wd5.myworkdayjobs.com/<site>
SHARED_HOST = re.compile(r"wd\d+\.myworkdaysite\.com\Z")  # wd1.myworkdaysite.com/recruiting/<tenant>/<site>
_TOKEN = re.compile(r"[A-Za-z0-9_-]+\Z")
_PATH = re.compile(r"/job/[^?#\\\s]+\Z")
_ID = re.compile(r"_([A-Za-z0-9-]+)\Z")
_LATER = re.compile(r"(.+)-[1-9][0-9]?\Z")  # a requisition's later posting: its id and `-<n>`
PAGE = 20  # the most a site returns at once
MAX_PER_QUERY = 1000  # a search matching more is too broad to read every update
DETAILS = 100  # texts fetched per source per update, unless the source sets max_details
DELAY = 1.0  # seconds between requests to one site


def site(source: Mapping[str, Any]) -> tuple[str, str, str]:
    """(host, public base, cxs root) of a source, checked so that no request goes anywhere else.

    A site is on its tenant's own host, or on a shared host with the tenant in the path. Both
    answer the same cxs requests on the host they are on.
    """
    host, name = source.get("host") or "", source.get("site") or ""
    own = HOST.match(host)
    tenant = source.get("tenant") or (own.group(1) if own else "")
    if not (own or SHARED_HOST.match(host)) or not _TOKEN.match(name) or not _TOKEN.match(tenant):
        raise SourceError(
            "Workday needs a host such as <tenant>.wd5.myworkdayjobs.com (or wd1.myworkdaysite.com with a tenant) "
            f"and a site name, not {host!r}/{name!r}"
        )
    base = f"https://{host}/{name}" if own else f"https://{host}/recruiting/{tenant}/{name}"
    return host, base, f"https://{host}/wday/cxs/{tenant}/{name}"


def requisition(posting_id: str) -> str:
    """The requisition a posting is for, from the posting's id (the one after the last `_`).

    A requisition's first posting has the requisition's id. Another posting of it, on the same site
    or another site of the tenant, has that id with `-<n>` added. The ending is dropped only where
    it is plainly one: a number from 1 to 99 with no leading zero, after at least 5 digits. A wrong
    merge would hide an opening, while a missed one only judges it twice. So `1234567-2` is
    `1234567` and `JR-0012345-1` is `JR-0012345`, while `R-12` and `R-13` stay two requisitions,
    and so do `R-2024-1` and `R-2024-2`, which may be a year and a count.
    """
    later = _LATER.match(posting_id)
    if later and sum(c.isdigit() for c in later.group(1)) >= 5:
        return later.group(1)
    return posting_id


def search(
    client: Client, root: str, query: str, limit: int = PAGE, offset: int = 0, applied: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    reply = client.post(
        root + "/jobs",
        json_body={"appliedFacets": dict(applied or {}), "limit": limit, "offset": offset, "searchText": query},
    )
    data = reply.raise_for_status().json()
    if (
        not isinstance(data, dict)
        or type(data.get("total")) is not int
        or not isinstance(data.get("jobPostings"), list)
    ):
        raise SourceError("Workday listing is not in the expected shape")
    return data


FAMILIES = ("jobFamilyGroup", "jobFamily")  # the facets sites file their job categories under


def categories(data: Mapping[str, Any]) -> dict[str, tuple[str, str, int, str]]:
    """A listing's job categories, as {folded name: (facet, id, postings, the site's own name)}.

    Most sites have them as a top-level `jobFamilyGroup` facet; some nest it in a group of facets or
    call it `jobFamily`; some have none.
    """
    found: dict[str, tuple[str, str, int, str]] = {}
    pending = [facet for facet in data.get("facets") or [] if isinstance(facet, dict)]
    while pending:
        facet = pending.pop(0)
        values = [value for value in facet.get("values") or [] if isinstance(value, dict)]
        if facet.get("facetParameter") in FAMILIES:
            for value in values:
                name = value.get("descriptor")
                if isinstance(name, str) and value.get("id") and name.casefold() not in found:
                    found[name.casefold()] = (facet["facetParameter"], str(value["id"]), value.get("count") or 0, name)
        else:
            pending += [value for value in values if "facetParameter" in value]  # a group of facets
    return found


def _narrowed(client: Client, root: str, names: list[str]) -> dict[str, Any]:
    """The facets that keep a search to these job categories. An unknown name fails the source."""
    known = categories(search(client, root, "", limit=1))
    missing = [name for name in names if name.casefold() not in known]
    if missing:
        raise SourceError(
            f"This Workday site has no job category {missing[0]!r}; `sources probe` lists the ones it has"
        )
    applied: dict[str, list[str]] = {}
    for name in names:
        facet, value, _, _ = known[name.casefold()]
        applied.setdefault(facet, []).append(value)
    return applied


def _bare(row: Any) -> bool:
    """A row with neither a link nor a title: no posting a person could open on the site.

    A site sometimes lists one, holding only its requisition id in `bulletFields`, while a posting
    is half published.
    """
    return isinstance(row, dict) and row.get("externalPath") in (None, "") and row.get("title") in (None, "")


def _listing(client: Client, root: str, query: str, applied: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Every posting one search matches, and how many bare rows (`_bare`) it skipped. Anything short
    of its total aborts the snapshot.

    A search may skip at most one row in 20 (always one). More means the listing has changed shape,
    and skipping them would return a snapshot that closes the postings they hide.
    """
    rows: dict[str, dict[str, Any]] = {}
    bare = 0
    total: int | None = None
    offset = 0
    while True:
        data = search(client, root, query, offset=offset, applied=applied)
        if total is None:
            total = data["total"]
            if total > MAX_PER_QUERY:
                raise SourceError(
                    f"Workday search {query!r} matches {total} postings, more than {MAX_PER_QUERY}. Its search "
                    "matches any of the words, so rewording does not narrow it: set `categories` on this source "
                    "to the site's job categories for these roles (`sources probe` lists them), or use fewer queries"
                )
        elif data["total"] not in (0, total):  # later pages report 0; any other number means it moved
            raise SourceError("Workday listing changed while it was read; the next update retries")
        batch = data["jobPostings"]
        for row in batch:
            if _bare(row):
                bare += 1
                continue
            path = row.get("externalPath") if isinstance(row, dict) else None
            if not isinstance(path, str) or not _PATH.match(path) or ".." in path:
                raise SourceError("Workday posting path is missing or unsafe")
            rows.setdefault(path, row)
        offset += len(batch)
        if not batch or offset >= total:
            break
    if bare > max(1, total // 20):
        raise SourceError(
            f"Workday search {query!r} lists {bare} of its {total} rows with neither a link nor a title; "
            "the listing may have changed shape"
        )
    if len(rows) + bare != total:
        raise SourceError("Workday listing ended short of its total; the next update retries")
    return list(rows.values()), bare


def _listed(source: Mapping[str, Any], base: str, row: Mapping[str, Any]) -> Job:
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
        url=base + path,
    )


def _own_place(info: Mapping[str, Any]) -> str:
    """Where a posting's own record puts it: its location with its country, then its other locations.

    The country belongs to the first location only, so it joins that place: a site often names a
    location by a campus or a street, which says no country by itself. The other locations are as
    the site names them.
    """
    country = info.get("country")
    named = country.get("descriptor") if isinstance(country, dict) else None
    first = [p.strip() for p in (info.get("location"), named) if isinstance(p, str) and p.strip()]
    more = info.get("additionalLocations")
    others = [p.strip() for p in more if isinstance(p, str) and p.strip()] if isinstance(more, list) else []
    return " / ".join(dict.fromkeys([", ".join(first), *others] if first else others))


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
    # part-time would look new again next time, and its text would be fetched again and again. The
    # place stays the listing's for the same reason. The record's own place goes in `own_location`,
    # and `collect` stores what it decides from it, so updates with only the listing still know.
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
        own_location=_own_place(info),
    )


def fetch(client: Client, source: Mapping[str, Any], rank: Callable[[Job], int | None] | None) -> list[Job]:
    host, base, root = site(source)
    client.space(host, DELAY)
    queries, names = source.get("queries"), source.get("categories") or []
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise SourceError("Workday is read through searches: set queries, or wanted roles in the criteria")
    if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
        raise SourceError("Workday categories must be a list of the site's job category names")
    applied = _narrowed(client, root, names) if names else {}
    listed: dict[str, tuple[Job, str]] = {}
    bare = 0
    for query in dict.fromkeys(q.strip() for q in queries):
        rows, skipped = _listing(client, root, query, applied)
        bare += skipped
        for row in rows:
            job = _listed(source, base, row)
            listed.setdefault(job.source_id, (job, row["externalPath"]))
    if bare and not listed:  # every row bare: a change of shape, not a half-published posting or two
        raise SourceError(
            "Workday listed only rows with neither a link nor a title; the listing may have changed shape"
        )
    if bare:  # once for each search that lists one
        skips.add("no link or title", bare)

    return lazy.complete(listed, rank, lazy.budget(source, DETAILS), lambda job, path: _detail(client, root, job, path))
