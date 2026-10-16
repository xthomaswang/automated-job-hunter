"""Boards whose public API returns every posting with its description in one response.

Greenhouse, Ashby, Lever, Recruitee and Gem need no authentication for these endpoints.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from ..http import Client
from ..model import Job, SourceError, public_https
from ..textutil import html_to_text

ATS = ("greenhouse", "ashby", "lever", "recruitee", "gem")
_SLUG = re.compile(r"[A-Za-z0-9_-]+\Z")
# An Ashby board's name may also have spaces and dots, as in `Acme AI` or `acme.com`, but not a space
# at either end, nor dots alone: `.` and `..` are steps in a URL's path.
_ASHBY_NAME = re.compile(r"(?!\.*\Z)(?! )[A-Za-z0-9 ._-]+(?<! )\Z")
# A Gem board is the vanity path of its page, jobs.gem.com/<board>: lowercase letters, digits and hyphens.
_GEM_BOARD = re.compile(r"[a-z0-9-]+\Z")
# A Gem posting's id is a string of digits or a UUID-like token. It goes into a link's path and a file's name.
_GEM_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


def feed_url(source: Mapping[str, Any]) -> str:
    slug, kind = source.get("slug") or "", source.get("ats")
    if not (_ASHBY_NAME if kind == "ashby" else _GEM_BOARD if kind == "gem" else _SLUG).match(slug):
        raise SourceError(f"Invalid board slug {slug!r}")
    urls = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug, safe='')}",
        "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
        "recruitee": f"https://{slug}.recruitee.com/api/offers/",
        # Gem documents this API as public: "Most endpoints in the Job Board API are public and do not
        # require authentication — job board information can be read by anyone using your job board's
        # vanity URL path." And: "Endpoints return complete lists of all matching resources in a single
        # response." So, like the other documented public board APIs here, it is read without consulting
        # robots.txt, which is a crawler policy for a site's pages: api.gem.com/robots.txt answers 403 from
        # the API's gateway, which says nothing about crawling, and jobs.gem.com/robots.txt answers 404.
        # docs/spikes.md (E14) records what was checked.
        "gem": f"https://api.gem.com/job_board/v0/{slug}/job_posts/",
    }
    if source.get("ats") not in urls:
        raise SourceError(f"Not a single-request board: {source.get('ats')!r}")
    return urls[source["ats"]]


def _names(items: Any) -> str:
    """Join the `name` of each entry in a list of objects (departments, offices)."""
    if not isinstance(items, list):
        return ""
    return " / ".join(dict.fromkeys(str(x["name"]) for x in items if isinstance(x, dict) and x.get("name")))


def _greenhouse(row: Mapping[str, Any], board: str) -> dict:
    jid, url = str(row["id"]), row.get("absolute_url")
    # Some boards give a posting no link, or not an https one: its page on Greenhouse, as `lists.link` writes it.
    if not public_https(url) and jid.isdigit():
        url = f"https://job-boards.greenhouse.io/{board}/jobs/{jid}"
    return dict(
        source_id=jid,
        title=row["title"],
        url=url,
        description=html_to_text(row.get("content")),
        location=(row.get("location") or {}).get("name") or "unknown",
        posted_at=row.get("first_published") or row.get("updated_at"),
        department=_names(row.get("departments")),
    )


def _ashby(row: Mapping[str, Any]) -> dict:
    url = row["jobUrl"]
    secondary = [x.get("location") for x in row.get("secondaryLocations") or [] if isinstance(x, dict)]
    places = [row.get("location"), *secondary]
    return dict(
        source_id=url.rstrip("/").split("/")[-1],
        title=row["title"],
        url=url,
        description=html_to_text(row.get("descriptionHtml")) or (row.get("descriptionPlain") or "").strip(),
        location=" / ".join(dict.fromkeys(p for p in places if isinstance(p, str) and p)) or "unknown",
        posted_at=row.get("publishedAt"),
        employment_type=row.get("employmentType") or "unknown",
        department=" / ".join(dict.fromkeys(x for x in (row.get("department"), row.get("team")) if x)),
        apply_url=row.get("applyUrl"),
    )


def _lever(row: Mapping[str, Any]) -> dict:
    categories = row.get("categories") or {}
    sections = "".join(f"<h3>{x.get('text', '')}</h3>{x.get('content', '')}" for x in row.get("lists") or [])
    created = row.get("createdAt")
    posted = (
        datetime.fromtimestamp(created / 1000.0, tz=timezone.utc).date().isoformat()
        if isinstance(created, (int, float)) and not isinstance(created, bool)
        else None
    )
    return dict(
        source_id=str(row["id"]),
        title=row["text"],
        url=row["hostedUrl"],
        description=html_to_text(f"{row.get('description', '')}{sections}<div>{row.get('additional', '')}</div>"),
        location=categories.get("location") or "unknown",
        posted_at=posted,
        employment_type=categories.get("commitment") or "unknown",
        department=" / ".join(dict.fromkeys(x for x in (categories.get("department"), categories.get("team")) if x)),
        apply_url=row.get("applyUrl"),
    )


def _recruitee(row: Mapping[str, Any]) -> dict:
    # The public careers API permits unauthenticated reads today; its documentation announces
    # mandatory employer tokens from 2027-02-10. A future 401 must surface as a source failure,
    # never as an empty board.
    place = row.get("location") or ", ".join(x for x in (row.get("city"), row.get("country")) if x)
    return dict(
        source_id=str(row["id"]),
        title=row["title"],
        url=row["careers_url"],
        description=html_to_text(f"{row.get('description') or ''}<div>{row.get('requirements') or ''}</div>"),
        location=place or "unknown",
        posted_at=row.get("published_at"),
        employment_type=row.get("employment_type_code") or "unknown",
        department=row.get("department") or "",
    )


_WORKPLACE = {"remote": "Remote", "hybrid": "Hybrid"}  # Gem's `location_type`, as the place says it


def _place_name(value: Any) -> str:
    """The name of a place such as `{"name": "New York, NY"}`, its spaces tidied; empty when it has none."""
    name = value.get("name") if isinstance(value, dict) else None
    return " ".join(name.split()) if isinstance(name, str) else ""


def _gem_place(row: Mapping[str, Any]) -> str:
    """Where a Gem posting is: its location, then each other place its offices are in, sorted.

    Stage 0 leaves a posting out only when every place it names is outside the wanted regions, so one
    open in several offices is never left out for its first. Sorted, so that the order the API lists
    offices in never changes the place, which change is measured on. An office's own name is a label,
    such as "HQ", not a place. Remote or hybrid work leads the place, as on SmartRecruiters and Eightfold
    boards, unless the place says so itself; stage 0 reads past the word.
    """
    offices = row.get("offices") if isinstance(row.get("offices"), list) else []
    places = [_place_name(office.get("location")) for office in offices if isinstance(office, dict)]
    found: dict[str, str] = {}  # each place once, compared without case
    for place in [_place_name(row.get("location")), *sorted(places, key=lambda p: (p.casefold(), p))]:
        if place:
            found.setdefault(place.casefold(), place)
    place = " / ".join(found.values())
    workplace = row.get("location_type")
    word = _WORKPLACE.get(workplace.casefold()) if isinstance(workplace, str) else None
    if word and not re.search(rf"\b{word}\b", place, re.IGNORECASE):
        place = f"{word}, {place}" if place else word
    return place or "unknown"


def _gem(row: Mapping[str, Any]) -> dict:
    jid = row["id"]
    if not isinstance(jid, str) or not _GEM_ID.match(jid):
        raise SourceError(f"Posting id {jid!r} is not a string of letters, digits, hyphens or underscores")
    kind = row.get("employment_type")
    dates = (row.get("first_published_at"), row.get("created_at"))
    return dict(
        source_id=jid,
        title=row["title"],
        url=row["absolute_url"],
        description=html_to_text(row.get("content")),
        location=_gem_place(row),
        posted_at=next((value for value in dates if isinstance(value, str) and value.strip()), None),
        employment_type=kind if isinstance(kind, str) and kind.strip() else "unknown",
        department=_names(row.get("departments")),
    )


# and `_greenhouse`, which is given the board too
_PARSERS = {"ashby": _ashby, "lever": _lever, "recruitee": _recruitee, "gem": _gem}


def parse_feed(source: Mapping[str, Any], payload: Any) -> list[Job]:
    """Every listed posting of one board. Any incomplete posting aborts the whole snapshot."""
    kind = source["ats"]
    if kind in ("lever", "gem"):  # a list of postings, not an object holding one
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
            facts = _greenhouse(row, source["slug"]) if kind == "greenhouse" else _PARSERS[kind](row)
        except (KeyError, TypeError, AttributeError) as error:
            raise SourceError(f"Posting is missing a required field: {error!r}") from error
        if not facts["description"]:
            raise SourceError(f"Posting {facts['source_id']} has no description: abort instead of closing absent jobs")
        if facts.get("apply_url") is not None and not isinstance(facts["apply_url"], str):
            facts["apply_url"] = None
        jobs.append(
            Job(key=f"{source['id']}:{facts['source_id']}", source=source["id"], company=source["company"], **facts)
        )
    if len({job.key for job in jobs}) != len(jobs):
        raise SourceError("Duplicate posting ids in feed")
    return jobs


def fetch_board(client: Client, source: Mapping[str, Any]) -> list[Job]:
    reply = client.get(feed_url(source)).raise_for_status()
    return parse_feed(source, reply.json())
