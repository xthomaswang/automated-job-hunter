"""No-key public job feeds. They are discovery feeds, not confirmations from employers.

A rolling feed lists only recent postings, so a posting's absence from it is never evidence that
the opening closed; the source's `membership_mode` tells `collect` which kind it is.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from ..http import Client
from ..model import Job, SourceError, public_https
from ..textutil import html_to_text

# provider -> (display name, host its listing links must be on, endpoint)
PROVIDERS = {
    "remotive": ("Remotive", "remotive.com", "https://remotive.com/api/remote-jobs"),
    "remoteok": ("Remote OK", "remoteok.com", "https://remoteok.com/api"),
    "jobicy": ("Jobicy", "jobicy.com", "https://jobicy.com/api/v2/remote-jobs"),
    "himalayas": ("Himalayas", "himalayas.app", "https://himalayas.app/jobs/api/search"),
}


def _posted(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    return str(value)


def _job(source: Mapping[str, Any], row: Mapping[str, Any]) -> Job:
    provider = source["provider"]
    label, host, _ = PROVIDERS[provider]
    try:
        if provider == "remotive":
            jid, title, company, url = row["id"], row["title"], row["company_name"], row["url"]
            text, location = row["description"], row.get("candidate_required_location") or "unknown"
            posted, employment = row.get("publication_date"), row.get("job_type") or "unknown"
        elif provider == "remoteok":
            jid, title, company, url = row["id"], row["position"], row["company"], row["url"]
            text, location = row["description"], row.get("location") or "Remote; restrictions unspecified"
            posted, employment = row.get("date") or row.get("epoch"), "unknown"
        elif provider == "jobicy":
            jid, title, company, url = row["id"], row["jobTitle"], row["companyName"], row["url"]
            text, location = row["jobDescription"], row.get("jobGeo") or "unknown"
            posted, employment = row.get("pubDate"), " / ".join(row.get("jobType") or []) or "unknown"
        else:
            jid, title, company, url = row["guid"], row["title"], row["companyName"], row["applicationLink"]
            text, location = row["description"], " / ".join(row.get("locationRestrictions") or []) or "Worldwide"
            posted, employment = row.get("pubDate"), row.get("employmentType") or "unknown"
    except (KeyError, TypeError) as error:
        raise SourceError(f"Incomplete {provider} posting ({error!r}); refusing a partial snapshot") from error
    named = isinstance(title, str) and title.strip() and isinstance(company, str) and company.strip()
    if not str(jid) or not named:
        raise SourceError(f"Incomplete {provider} posting; refusing a partial snapshot")
    actual = (urlparse(url).hostname or "") if isinstance(url, str) else ""
    if not public_https(url) or (actual[4:] if actual.startswith("www.") else actual) != host:
        raise SourceError(f"Unexpected {provider} listing URL")
    return Job(key=f"{source['id']}:{jid}", source=source["id"], source_id=str(jid), company=company.strip(),
               title=title.strip(), location=str(location), url=url, description=html_to_text(text),
               posted_at=_posted(posted), employment_type=str(employment), source_kind="aggregator", source_label=label)


def _add(source: Mapping[str, Any], rows: Any, jobs: dict[str, Job]) -> None:
    if not isinstance(rows, list):
        raise SourceError("Aggregator response has no list of postings")
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError("Malformed aggregator posting")
        job = _job(source, row)
        if job.key in jobs:
            raise SourceError("Repeated posting across pages; the snapshot may be incomplete")
        jobs[job.key] = job


def _get(client: Client, url: str, params: Mapping[str, Any] | None = None) -> Any:
    # A 429 raises here; the caller keeps the previous snapshot and the next tick tries again.
    return client.get(url, params=params).raise_for_status().json()


def _pause(client: Client, source: Mapping[str, Any]) -> None:
    client.sleep(max(0.0, float(source.get("request_delay_seconds", 1))))


def fetch_aggregator(client: Client, source: Mapping[str, Any]) -> list[Job]:
    provider = source.get("provider")
    if provider not in PROVIDERS:
        raise SourceError(f"Unsupported aggregator provider {provider!r}")
    endpoint = PROVIDERS[provider][2]
    max_pages = int(source.get("max_pages", 200))
    if max_pages < 1:
        raise SourceError("max_pages must be positive")
    jobs: dict[str, Job] = {}
    if provider == "remotive":
        data = _get(client, endpoint)
        rows = data.get("jobs") if isinstance(data, dict) else None
        _add(source, rows, jobs)
        if data.get("job-count") != len(rows) or data.get("total-job-count", len(rows)) != len(rows):
            raise SourceError("Remotive count mismatch; incomplete feed")
    elif provider == "remoteok":
        data = _get(client, endpoint)
        if not isinstance(data, list) or not data or not isinstance(data[0], dict) or "legal" not in data[0]:
            raise SourceError("Remote OK feed envelope missing")
        _add(source, data[1:], jobs)
    elif provider == "jobicy":
        cursor, seen = None, set()
        for _ in range(max_pages):
            data = _get(client, endpoint, {"count": 200, **({"cursor": cursor} if cursor else {})})
            if not isinstance(data, dict) or data.get("success") is not True:
                raise SourceError("Jobicy did not report success")
            rows = data.get("jobs")
            _add(source, rows, jobs)
            if data.get("jobCount") != len(rows):
                raise SourceError("Jobicy page count mismatch")
            if "nextCursor" not in data or not isinstance(data.get("hasMore"), bool):
                raise SourceError("Jobicy cursor metadata missing")
            cursor = data["nextCursor"]
            if not data["hasMore"]:
                if cursor is not None:
                    raise SourceError("Jobicy contradictory pagination metadata")
                break
            if not rows or not isinstance(cursor, str) or not cursor or cursor in seen:
                raise SourceError("Jobicy pagination ended early or repeated")
            seen.add(cursor)
            _pause(client, source)
        else:
            raise SourceError("Jobicy pagination cap reached; incomplete synchronization")
    else:
        queries = source.get("queries")
        if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
            raise SourceError("Himalayas requires explicit search queries")
        for number, query in enumerate(queries):
            found: dict[str, Job] = {}
            total = None
            if number:
                _pause(client, source)
            for page in range(1, max_pages + 1):
                data = _get(client, endpoint, {"q": query, "sort": "recent", "page": page})
                if not isinstance(data, dict):
                    raise SourceError("Himalayas response is not an object")
                page_total, limit, offset = data.get("totalCount"), data.get("limit"), data.get("offset")
                if (type(page_total) is not int or page_total < 0 or type(limit) is not int or not 1 <= limit <= 20):
                    raise SourceError("Himalayas count/page metadata missing")
                if offset != (page - 1) * limit:
                    raise SourceError("Himalayas ignored the requested page; incomplete search")
                if total is not None and page_total != total:
                    raise SourceError("Himalayas search count changed during pagination")
                total, rows = page_total, data.get("jobs")
                if not isinstance(rows, list) or len(rows) > limit:
                    raise SourceError("Himalayas page size mismatch")
                # The search index can return fewer listings than its advertised count, so walk
                # the offsets rather than counting rows, and never invent rows for the gap.
                _add(source, rows, found)
                if offset + limit >= total:
                    break
                _pause(client, source)
            else:
                raise SourceError("Himalayas page cap reached; incomplete search")
            if total and not found:
                raise SourceError("Himalayas returned no listings for a nonzero search count")
            jobs.update(found)
    return list(jobs.values())
