"""Boards whose public API returns every posting with its description in one response.

Greenhouse, Ashby, Lever and Recruitee need no authentication for these endpoints.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ..http import Client
from ..model import Job, SourceError
from ..textutil import html_to_text

ATS = ("greenhouse", "ashby", "lever", "recruitee")
_SLUG = re.compile(r"[A-Za-z0-9_-]+\Z")


def feed_url(source: Mapping[str, Any]) -> str:
    slug = source.get("slug") or ""
    if not _SLUG.match(slug):
        raise SourceError(f"Invalid board slug {slug!r}")
    urls = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
        "recruitee": f"https://{slug}.recruitee.com/api/offers/",
    }
    if source.get("ats") not in urls:
        raise SourceError(f"Not a single-request board: {source.get('ats')!r}")
    return urls[source["ats"]]


def _names(items: Any) -> str:
    """Join the `name` of each entry in a list of objects (departments, offices)."""
    if not isinstance(items, list):
        return ""
    return " / ".join(dict.fromkeys(str(x["name"]) for x in items if isinstance(x, dict) and x.get("name")))


def _greenhouse(row: Mapping[str, Any]) -> dict:
    return dict(
        source_id=str(row["id"]), title=row["title"], url=row["absolute_url"],
        description=html_to_text(row.get("content")),
        location=(row.get("location") or {}).get("name") or "unknown",
        posted_at=row.get("first_published") or row.get("updated_at"),
        department=_names(row.get("departments")))


def _ashby(row: Mapping[str, Any]) -> dict:
    url = row["jobUrl"]
    secondary = [x.get("location") for x in row.get("secondaryLocations") or [] if isinstance(x, dict)]
    places = [row.get("location"), *secondary]
    return dict(
        source_id=url.rstrip("/").split("/")[-1], title=row["title"], url=url,
        description=html_to_text(row.get("descriptionHtml")) or (row.get("descriptionPlain") or "").strip(),
        location=" / ".join(dict.fromkeys(p for p in places if isinstance(p, str) and p)) or "unknown",
        posted_at=row.get("publishedAt"), employment_type=row.get("employmentType") or "unknown",
        department=" / ".join(dict.fromkeys(x for x in (row.get("department"), row.get("team")) if x)),
        apply_url=row.get("applyUrl"))


def _lever(row: Mapping[str, Any]) -> dict:
    categories = row.get("categories") or {}
    sections = "".join(f"<h3>{x.get('text', '')}</h3>{x.get('content', '')}" for x in row.get("lists") or [])
    created = row.get("createdAt")
    posted = (datetime.fromtimestamp(created / 1000.0, tz=timezone.utc).date().isoformat()
              if isinstance(created, (int, float)) and not isinstance(created, bool) else None)
    return dict(
        source_id=str(row["id"]), title=row["text"], url=row["hostedUrl"],
        description=html_to_text(f"{row.get('description', '')}{sections}<div>{row.get('additional', '')}</div>"),
        location=categories.get("location") or "unknown", posted_at=posted,
        employment_type=categories.get("commitment") or "unknown",
        department=" / ".join(dict.fromkeys(x for x in (categories.get("department"), categories.get("team")) if x)),
        apply_url=row.get("applyUrl"))


def _recruitee(row: Mapping[str, Any]) -> dict:
    # The public careers API permits unauthenticated reads today; its documentation announces
    # mandatory employer tokens from 2027-02-10. A future 401 must surface as a source failure,
    # never as an empty board.
    place = row.get("location") or ", ".join(x for x in (row.get("city"), row.get("country")) if x)
    return dict(
        source_id=str(row["id"]), title=row["title"], url=row["careers_url"],
        description=html_to_text(f"{row.get('description') or ''}<div>{row.get('requirements') or ''}</div>"),
        location=place or "unknown", posted_at=row.get("published_at"),
        employment_type=row.get("employment_type_code") or "unknown", department=row.get("department") or "")


_PARSERS = {"greenhouse": _greenhouse, "ashby": _ashby, "lever": _lever, "recruitee": _recruitee}


def parse_feed(source: Mapping[str, Any], payload: Any) -> list[Job]:
    """Every listed posting of one board. Any incomplete posting aborts the whole snapshot."""
    kind = source["ats"]
    if kind == "lever":
        rows = payload
    else:
        rows = payload.get("offers" if kind == "recruitee" else "jobs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SourceError("Invalid or incomplete feed: expected a list of postings")
    jobs: list[Job] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError("Invalid posting in feed")
        if kind == "ashby" and row.get("isListed") is False:
            continue  # unlisted positions are not part of the public board
        try:
            facts = _PARSERS[kind](row)
        except (KeyError, TypeError, AttributeError) as error:
            raise SourceError(f"Posting is missing a required field: {error!r}") from error
        if not facts["description"]:
            raise SourceError(f"Posting {facts['source_id']} has no description: abort instead of closing absent jobs")
        if facts.get("apply_url") is not None and not isinstance(facts["apply_url"], str):
            facts["apply_url"] = None
        jobs.append(Job(key=f"{source['id']}:{facts['source_id']}", source=source["id"],
                        company=source["company"], **facts))
    if len({job.key for job in jobs}) != len(jobs):
        raise SourceError("Duplicate posting ids in feed")
    return jobs


def fetch_board(client: Client, source: Mapping[str, Any]) -> list[Job]:
    reply = client.get(feed_url(source)).raise_for_status()
    return parse_feed(source, reply.json())
