"""SmartRecruiters, through its documented public Posting API. No login, no key.

`GET /v1/companies/<company>/postings` lists a company's public postings 100 at a time with their
facts but not their text, which is one more request per posting. So, like Workday, a company is
read through searches (`queries`, by default the wanted roles), and text is fetched only where the
rank function asks for it, at most `max_details` per update, below the documented 10 requests a
second. docs/spikes.md (E8) records what the API returned when this was written.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from ..http import Client, HttpError
from ..model import Job, SourceError
from ..textutil import html_to_text
from . import lazy

KINDS = ("smartrecruiters",)
HOST = "api.smartrecruiters.com"
_SLUG = re.compile(r"[A-Za-z0-9_-]+\Z")
_ID = re.compile(r"\d+\Z")
PAGE = 100  # the most the API returns at once
MAX_PER_QUERY = 2000  # 20 listing requests; a search matching more is too broad to read every update
DETAILS = 100  # texts fetched per source per update, unless the source sets max_details
DELAY = 0.125  # seconds between requests: 8 a second, below the documented 10


def root(source: Mapping[str, Any]) -> str:
    slug = source.get("slug") or ""
    if not _SLUG.match(slug):
        raise SourceError(f"Invalid SmartRecruiters company identifier {slug!r}")
    return f"https://{HOST}/v1/companies/{slug}/postings"


def search(client: Client, url: str, query: str, limit: int = PAGE, offset: int = 0) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if query:
        params["q"] = query
    data = client.get(url, params=params).raise_for_status().json()
    if (
        not isinstance(data, dict)
        or type(data.get("totalFound")) is not int
        or not isinstance(data.get("content"), list)
        or data.get("offset") != offset
    ):
        raise SourceError("SmartRecruiters listing is not in the expected shape")
    return data


def _listing(client: Client, url: str, query: str) -> list[dict[str, Any]]:
    """Every posting one search matches. Anything short of its total aborts the snapshot."""
    rows: dict[str, dict[str, Any]] = {}
    total: int | None = None
    offset = 0
    while True:
        data = search(client, url, query, offset=offset)
        if total is None:
            total = data["totalFound"]
            if total > MAX_PER_QUERY:
                raise SourceError(
                    f"SmartRecruiters search {query!r} matches {total} postings, more than {MAX_PER_QUERY}: "
                    "use fewer or more specific queries"
                )
        elif data["totalFound"] != total:
            raise SourceError("SmartRecruiters listing changed while it was read; the next update retries")
        batch = data["content"]
        for row in batch:
            jid = str(row.get("id", "")) if isinstance(row, dict) else ""
            if not _ID.match(jid):
                raise SourceError("SmartRecruiters posting id is missing or not a number")
            rows.setdefault(jid, row)
        offset += len(batch)
        if not batch or offset >= total:
            break
    if len(rows) != total:
        raise SourceError("SmartRecruiters listing ended short of its total; the next update retries")
    return list(rows.values())


def _label(value: Any) -> str:
    return value.get("label") or "" if isinstance(value, dict) else ""


def _place(location: Any) -> str:
    """The listing's place, built the same way on every update. The country is named, not coded,
    because two-letter codes such as CA or IN also name US states."""
    if not isinstance(location, dict):
        return "unknown"
    full = location.get("fullLocation")
    if isinstance(full, str) and full.strip():
        place = ", ".join(part.strip() for part in full.split(",") if part.strip())
    else:
        place = ", ".join(str(location[k]) for k in ("city", "region", "country") if location.get(k))
    return ("Remote, " if location.get("remote") else "") + (place or "unknown")


def _listed(source: Mapping[str, Any], row: Mapping[str, Any]) -> Job:
    """A posting as the listing shows it: everything but its text."""
    jid = str(row["id"])
    return Job(
        key=f"{source['id']}:{jid}",
        source=source["id"],
        source_id=jid,
        company=source["company"],
        title=row.get("name") or "",
        location=_place(row.get("location")),
        url=f"https://jobs.smartrecruiters.com/{source['slug']}/{jid}",
        posted_at=row.get("releasedDate") if isinstance(row.get("releasedDate"), str) else None,
        employment_type=_label(row.get("typeOfEmployment")) or "unknown",
        department=_label(row.get("department")) or _label(row.get("function")),
    )


def _own(url: Any, slug: str, jid: str) -> str | None:
    """A posting link from the detail, kept only if it is this company's posting on SmartRecruiters."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    path = parts.path.strip("/").split("/")
    ok = (
        parts.scheme == "https"
        and parts.hostname == "jobs.smartrecruiters.com"
        and len(path) == 2
        and path[0].casefold() == slug.casefold()
        and (path[1] == jid or path[1].startswith(jid + "-"))
    )
    return url if ok else None


def _detail(client: Client, url: str, job: Job, slug: str) -> Job | None:
    """The posting with its text, or None when the API would not give it this time."""
    try:
        data = client.get(f"{url}/{job.source_id}").raise_for_status().json()
    except (HttpError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or str(data.get("id")) != job.source_id or data.get("active") is False:
        return None
    sections = (data.get("jobAd") or {}).get("sections") or {}
    parts = [
        f"<h3>{section.get('title') or name}</h3>{section.get('text') or ''}"
        for name, section in sections.items()
        if isinstance(section, dict) and section.get("text") and name != "videos"
    ]
    text = html_to_text("".join(parts))
    if not text:
        return None
    link = _own(data.get("postingUrl"), slug, job.source_id)
    applied = data.get("applyUrl")
    own_apply = isinstance(applied, str) and _own(applied.split("?", 1)[0], slug, job.source_id)
    released = data.get("releasedDate")  # for a posting known only from a list, which gives no date
    return replace(
        job,
        description=text,
        url=link or job.url,
        apply_url=applied if own_apply else None,
        posted_at=job.posted_at or (released if isinstance(released, str) else None),
    )


def fetch(client: Client, source: Mapping[str, Any], rank: Callable[[Job], int | None] | None) -> list[Job]:
    url = root(source)
    client.space(HOST, DELAY)
    queries = source.get("queries")
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise SourceError("SmartRecruiters is read through searches: set queries, or wanted roles in the criteria")
    listed: dict[str, tuple[Job, str]] = {}
    for query in dict.fromkeys(q.strip() for q in queries):
        for row in _listing(client, url, query):
            job = _listed(source, row)
            listed.setdefault(job.source_id, (job, source["slug"]))
    return lazy.complete(listed, rank, lazy.budget(source, DETAILS), lambda job, slug: _detail(client, url, job, slug))
