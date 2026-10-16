"""Eightfold career sites, read through the JSON their own public pages load. No login, no key.

A career site (`<company>.eightfold.ai/careers`, or the same app on a company's own host) lists
postings 10 at a time without their text (`GET /api/pcsx/search`), with the search's total on
every page, and each posting's text is one more request (`GET /api/pcsx/position_details`). Both
need the site's `domain`, which its pages name. So, like Workday, a site is read through searches
(`queries`, by default the wanted roles), every matching posting is listed, and text is fetched
only where the rank function asks for it, at most `max_details` per update, one request a second,
as no rate is documented. docs/spikes.md (E11) records what the endpoints returned when this was
written; this is not a versioned API, so any change of shape fails the source and keeps its last
good snapshot.

Some sites on a company's own host still run the app's older layout, which a source names with
`"layout": "apply-v2"` (the add line `sources probe` prints carries it). Its pages embed their
settings as `smartApplyData`; it lists postings through `GET /api/apply/v2/jobs` and gives a
posting's text through `GET /api/apply/v2/jobs/<id>`, read with the same searches, pages of 10,
totals and budget. Such a site's robots.txt disallows all but a few paths, so every update of one
reads robots.txt first and refuses the site unless it allows what is asked. A source without the
field reads the newer layout, as before.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
from typing import Any
from urllib.parse import urlencode, urlsplit

from .. import robots
from ..http import Client, HttpError
from ..model import Job, SourceError
from ..textutil import html_to_text
from . import lazy

KINDS = ("eightfold",)
HOST = re.compile(r"[a-z0-9-]+\.eightfold\.ai\Z")  # <company>.eightfold.ai; a company's own host works too
OLDER = "apply-v2"  # the older layout, as a source's `"layout"` and its add line name it
_NAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")  # a host or domain name
_ID = re.compile(r"\d{1,20}\Z")
_CONFIG = re.compile(r"<code\b[^>]*\bid=[\"']pcsx-data[\"'][^>]*>([^<]{0,500})", re.I)  # the page's own settings
_CONFIG_DOMAIN = re.compile(r'\s*\{\s*"domain"\s*:\s*"([^"]+)"')
# The older layout's settings: HTML-escaped JSON of some 27 KB, so no `<` until the element ends.
_OLDER_CONFIG = re.compile(r"<code\b[^>]*\bid=[\"']smartApplyData[\"'][^>]*>([^<]*)", re.I)
_CUSTOM = "efcustomText"  # a site's own posting fields, such as its time or employment type
_WORKPLACE = {"remote": "Remote, ", "hybrid": "Hybrid, "}
PAGE = 10  # postings a listing request returns
MAX_PER_QUERY = 500  # 50 listing requests of 10 (a larger `num` is ignored); sites also refuse deep pages
DETAILS = 100  # texts fetched per source per update, unless the source sets max_details
DELAY = 1.0  # seconds between requests to one site
Page = Callable[[str, int], Mapping[str, Any]]  # one page of a search, (query, start): its count and positions


def site(source: Mapping[str, Any]) -> tuple[str, str]:
    """(host, domain) of a source, checked so that no request goes anywhere else.

    The host is the career site's: `<company>.eightfold.ai`, or the company's own host serving the
    same app. The domain is the one the site's pages name; the API answers nothing without it.
    """
    host, domain = source.get("host"), source.get("domain")
    host = host.lower() if isinstance(host, str) else ""
    domain = domain.lower() if isinstance(domain, str) else ""
    if not (len(host) <= 253 and _NAME.match(host) and len(domain) <= 253 and _NAME.match(domain)):
        raise SourceError(
            "Eightfold needs the career site's host, such as <company>.eightfold.ai, and the domain its pages "
            f"name, not {source.get('host')!r}/{source.get('domain')!r}"
        )
    return host, domain


def layout(source: Mapping[str, Any]) -> str | None:
    """The layout a source reads: None for the newer one, which needs no field, or "apply-v2"."""
    value = source.get("layout")
    if value is not None and value != OLDER:
        raise SourceError(
            f'Eightfold\'s "layout" is "{OLDER}" for a site in the older layout, or left out for the newer one, '
            f"not {value!r}"
        )
    return value


def _domain(value: Any) -> str | None:
    domain = value.lower() if isinstance(value, str) else ""
    return domain if len(domain) <= 253 and _NAME.match(domain) else None


def page_site(page: str) -> dict[str, str] | None:
    """What an Eightfold career page's own settings give a source, or None if it is not one: the domain
    they name, and `"layout": "apply-v2"` when they are the older layout's (`smartApplyData`)."""
    found = _CONFIG.search(page)
    named = _CONFIG_DOMAIN.match(html.unescape(found.group(1))) if found else None
    domain = _domain(named.group(1)) if named else None
    if domain:
        return {"domain": domain}
    older = _OLDER_CONFIG.search(page)
    try:
        settings = json.loads(html.unescape(older.group(1))) if older else None
    except (ValueError, RecursionError):
        return None
    if not isinstance(settings, dict) or type(settings.get("count")) is not int:
        return None
    domain = _domain(settings.get("domain")) if isinstance(settings.get("positions"), list) else None
    return {"domain": domain, "layout": OLDER} if domain else None


def search(client: Client, host: str, domain: str, query: str, start: int = 0) -> dict[str, Any]:
    params = {"domain": domain, "query": query, "location": "", "start": start}
    body = client.get(f"https://{host}/api/pcsx/search", params=params).raise_for_status().json()
    data = body.get("data") if isinstance(body, dict) else None
    if (
        not isinstance(data, dict)
        or body.get("status") != 200
        or type(data.get("count")) is not int
        or not isinstance(data.get("positions"), list)
    ):
        raise SourceError("Eightfold listing is not in the expected shape")
    return data


def _refused(host: str, url: str) -> SourceError:
    return SourceError(f"robots.txt on {host} disallows {urlsplit(url).path}, so the site cannot be read")


def search_older(
    client: Client, permits: Callable[[str], bool], host: str, domain: str, query: str, start: int = 0
) -> dict[str, Any]:
    """One page of an older layout's search, once the site's robots.txt (`permits`) allows asking for it."""
    params = {"domain": domain, "start": start, "num": PAGE, "query": query, "sort_by": "relevance"}
    url = f"https://{host}/api/apply/v2/jobs?{urlencode(params)}"
    if not permits(url):
        raise _refused(host, url)
    body = client.get(url).raise_for_status().json()
    if not isinstance(body, dict) or type(body.get("count")) is not int or not isinstance(body.get("positions"), list):
        raise SourceError("Eightfold listing is not in the expected shape")
    return body


def _text_url(host: str, domain: str, jid: str) -> str:
    """Where an older layout's site gives one posting's record with its text."""
    return f"https://{host}/api/apply/v2/jobs/{jid}?{urlencode({'domain': domain})}"


def _id(row: Any) -> str:
    value = row.get("id") if isinstance(row, dict) else None
    jid = str(value) if isinstance(value, (int, str)) and not isinstance(value, bool) else ""
    if not _ID.match(jid):
        raise SourceError("Eightfold posting id is missing or not a number")
    return jid


def _listing(page: Page, query: str) -> list[dict[str, Any]]:
    """Every posting one search matches. Anything short of its total aborts the snapshot."""
    rows: dict[str, dict[str, Any]] = {}
    total: int | None = None
    start = 0
    while True:
        data = page(query, start)
        if total is None:
            total = data["count"]
            if total > MAX_PER_QUERY:
                raise SourceError(
                    f"Eightfold search {query!r} matches {total} postings, more than {MAX_PER_QUERY}: "
                    "use fewer or more specific queries"
                )
        elif data["count"] != total:
            raise SourceError("Eightfold listing changed while it was read; the next update retries")
        batch = data["positions"]
        for row in batch:
            rows.setdefault(_id(row), row)
        start += len(batch)
        if not batch or start >= total:
            break
    if len(rows) != total:
        raise SourceError("Eightfold listing ended short of its total; the next update retries")
    return list(rows.values())


def _place(row: Mapping[str, Any]) -> str:
    """The listing's places, built the same way on every update. They are the site's own names, which
    spell the country out: its standardized ones code it, and codes such as DE or IN also name US states."""
    places = row.get("locations") if isinstance(row.get("locations"), list) else []
    named = dict.fromkeys(" ".join(place.split()) for place in places if isinstance(place, str) and place.strip())
    workplace = row.get("workLocationOption")
    prefix = _WORKPLACE.get(workplace.lower(), "") if isinstance(workplace, str) else ""
    return prefix + (" / ".join(named) or "unknown")


def _older_places(record: Mapping[str, Any]) -> list[str]:
    """An older layout's record's places, each as "Helsinki, Finland" where it writes "Helsinki,Finland":
    the country spelled out. Its list of places, or else its one place."""
    places = record.get("locations")
    places = places if isinstance(places, list) and places else [record.get("location")]
    parts = ([part for part in place.split(",") if part.strip()] for place in places if isinstance(place, str))
    named = (", ".join(" ".join(part.split()) for part in each) for each in parts)
    return list(dict.fromkeys(place for place in named if place))


def _older_place(row: Mapping[str, Any]) -> str:
    """An older layout's listing's places, built the same way on every update, as `_place` builds the newer's."""
    workplace = row.get("work_location_option")
    prefix = _WORKPLACE.get(workplace.lower(), "") if isinstance(workplace, str) else ""
    return prefix + (" / ".join(_older_places(row)) or "unknown")


def _day(value: Any) -> str | None:
    """The listing's posting time, in seconds since the epoch, as an ISO date."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _listed(source: Mapping[str, Any], host: str, row: Mapping[str, Any], older: bool = False) -> Job:
    """A posting as the listing shows it: everything but its text. Its title and place are the ones
    change is measured on; its place, remote work and date are what stage 0 can read on every update.
    An older layout's row has the same facts under other names, its date the time it was created."""
    jid = _id(row)
    name, department = row.get("name"), row.get("department")
    return Job(
        key=f"{source['id']}:{jid}",
        source=source["id"],
        source_id=jid,
        company=source["company"],
        title=name if isinstance(name, str) else "",
        location=_older_place(row) if older else _place(row),
        url=f"https://{host}/careers/job/{jid}",  # the canonical link, the same in both layouts
        posted_at=_day(row.get("t_create" if older else "postedTs")),
        department=department if isinstance(department, str) else "",
    )


def _fields(data: Mapping[str, Any]) -> list[str]:
    """The site's own posting fields as lines: `efcustomTextTimeType: ["Full time"]` is "Time type: Full time"."""
    lines = []
    for key, value in data.items():
        if not (isinstance(key, str) and key.startswith(_CUSTOM) and isinstance(value, list)):
            continue
        values = [" ".join(item.split()) for item in value if isinstance(item, str) and item.strip()]
        words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key[len(_CUSTOM) :]).split()
        if values and words:
            lines.append(f"{' '.join(words).capitalize()}: {', '.join(values)}")
    return lines


def _detail(client: Client, host: str, domain: str, job: Job) -> Job | None:
    """The posting with its text, or None when the site would not give it this time."""
    params = {"position_id": job.source_id, "domain": domain, "hl": "en"}
    try:
        body = client.get(f"https://{host}/api/pcsx/position_details", params=params).raise_for_status().json()
    except (HttpError, OSError, ValueError):
        return None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or str(data.get("id")) != job.source_id:
        return None
    described = data.get("jobDescription")
    text = html_to_text(described if isinstance(described, str) else "")
    if not text:
        return None
    # The site's own fields, such as its time or employment type, stay in the text, not in
    # `employment_type`: stage 0 must see the same facts every update, and most updates have only the
    # listing, which has no such field. Otherwise a posting it drops for being part-time would look
    # new again next time, and its text would be fetched again and again.
    head = _fields(data)
    return replace(job, description="\n".join(head) + "\n\n" + text if head else text)


def _older_detail(client: Client, host: str, domain: str, job: Job) -> Job | None:
    """The posting with its text from an older layout's record, or None when the site would not give it
    this time. Any of the site's own fields head the text, as `_detail` puts them. The record's places
    go in `own_location`: they spell the country out, so `collect` parks a posting they put outside
    the wanted regions even when its listing row named no place."""
    try:
        data = client.get(_text_url(host, domain, job.source_id)).raise_for_status().json()
    except (HttpError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or str(data.get("id")) != job.source_id:
        return None
    described = data.get("job_description")
    text = html_to_text(described if isinstance(described, str) else "")
    if not text:
        return None
    head = _fields(data)
    description = "\n".join(head) + "\n\n" + text if head else text
    return replace(job, description=description, own_location=" / ".join(_older_places(data)))


def fetch(client: Client, source: Mapping[str, Any], rank: Callable[[Job], int | None] | None) -> list[Job]:
    host, domain = site(source)
    older = layout(source) == OLDER
    client.space(host, DELAY)
    queries = source.get("queries")
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise SourceError("Eightfold is read through searches: set queries, or wanted roles in the criteria")
    if older:
        permits = robots.permits(client, host)  # before anything else is asked of the site
        page: Page = partial(search_older, client, permits, host, domain)
    else:
        page = partial(search, client, host, domain)
    listed: dict[str, tuple[Job, None]] = {}
    for query in dict.fromkeys(q.strip() for q in queries):
        for row in _listing(page, query):
            job = _listed(source, host, row, older)
            listed.setdefault(job.source_id, (job, None))
    if older:  # every posting's text must be allowed too, as some of them are fetched now and others later
        refused = next((url for url in (_text_url(host, domain, jid) for jid in listed) if not permits(url)), None)
        if refused:
            raise _refused(host, refused)
    detail = _older_detail if older else _detail
    return lazy.complete(listed, rank, lazy.budget(source, DETAILS), lambda job, _: detail(client, host, domain, job))
