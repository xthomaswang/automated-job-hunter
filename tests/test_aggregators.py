import json
from urllib.parse import parse_qsl, urlsplit

import pytest
from ajh.http import Client, HttpError
from ajh.model import SourceError
from ajh.sources.aggregators import fetch_aggregator


def source(provider, **overrides):
    return {"id": provider, "ats": "aggregator", "provider": provider, "request_delay_seconds": 0, **overrides}


def fetch(provider, handler, **overrides):
    """Run one aggregator against `handler(query params) -> payload or (status, payload)`."""
    def transport(method, url, headers, body, timeout):
        reply = handler(dict(parse_qsl(urlsplit(url).query)))
        status, payload = reply if isinstance(reply, tuple) else (200, reply)
        return status, {}, json.dumps(payload).encode()

    return fetch_aggregator(Client(transport, sleep=lambda seconds: None, retries=0), source(provider, **overrides))


def remotive_row():
    return {"id": 123, "title": "UX Researcher", "company_name": "Example", "publication_date": "2026-10-01T10:00:00",
            "url": "https://remotive.com/remote-jobs/design/ux-researcher-123", "description": "<p>Conduct user interviews.</p>"}


def jobicy_row(jid):
    return {"id": jid, "jobTitle": "UX Researcher", "companyName": "Example",
            "url": f"https://jobicy.com/jobs/{jid}-ux-researcher", "jobDescription": "Conduct user interviews."}


def himalayas_row(slug):
    url = "https://himalayas.app/companies/example/jobs/" + slug
    return {"guid": url, "applicationLink": url, "title": "UX Researcher", "companyName": "Example",
            "description": "Conduct user interviews.", "pubDate": 1790000000}


def test_remotive_posting_keeps_the_provider_listing_and_its_label():
    (job,) = fetch("remotive", lambda q: {"job-count": 1, "total-job-count": 1, "jobs": [remotive_row()]})
    assert job.source_kind == "aggregator" and job.source_label == "Remotive" and job.apply_url is None
    assert job.url == remotive_row()["url"] and job.description == "Conduct user interviews."
    assert job.key == "remotive:123" and job.location == "unknown"


def test_remotive_partial_feed_count_is_a_failure():
    with pytest.raises(SourceError, match="count mismatch"):
        fetch("remotive", lambda q: {"job-count": 1, "total-job-count": 2, "jobs": [remotive_row()]})


def test_pagination_cap_never_returns_a_partial_result():
    page = {"success": True, "jobs": [jobicy_row(1)], "jobCount": 1, "nextCursor": "next", "hasMore": True}
    with pytest.raises(SourceError, match="cap reached"):
        fetch("jobicy", lambda q: page, max_pages=1)


def test_jobicy_follows_its_cursor_to_the_end():
    seen = []

    def handler(query):
        cursor = query.get("cursor")
        seen.append(cursor)
        return {"success": True, "jobCount": 1, "jobs": [jobicy_row(1 if cursor is None else 2)],
                "nextCursor": "opaque+/=" if cursor is None else None, "hasMore": cursor is None}

    jobs = fetch("jobicy", handler, membership_mode="rolling")
    assert seen == [None, "opaque+/="] and [j.source_id for j in jobs] == ["1", "2"]


def test_jobicy_broken_continuation_is_not_an_empty_feed():
    with pytest.raises(SourceError, match="ended early"):
        fetch("jobicy", lambda q: {"success": True, "jobCount": 0, "jobs": [], "nextCursor": None, "hasMore": True})


def test_remoteok_envelope_is_not_a_job_and_the_url_keeps_its_case():
    url = "https://remoteOK.com/remote-jobs/ux-researcher-55"
    row = {"id": "55", "position": "UX Researcher", "company": "Example", "description": "Conduct user interviews.", "url": url, "epoch": 1790000000}
    (job,) = fetch("remoteok", lambda q: [{"legal": "Credit Remote OK"}, row], membership_mode="rolling")
    assert job.url == url and job.source_label == "Remote OK" and job.posted_at == "2026-09-21T14:13:20+00:00"
    with pytest.raises(SourceError, match="envelope"):
        fetch("remoteok", lambda q: [row])


def test_himalayas_walks_index_offsets_despite_sparse_pages():
    requested = []

    def handler(query):
        page = int(query["page"])
        requested.append(page)
        return {"totalCount": 3, "limit": 2, "offset": (page - 1) * 2, "jobs": [himalayas_row(str(page))]}

    jobs = fetch("himalayas", handler, queries=["UX research"])
    assert requested == [1, 2] and len(jobs) == 2


def test_himalayas_overlapping_searches_are_merged_without_rejecting_jobs():
    page = {"totalCount": 1, "limit": 20, "offset": 0, "jobs": [himalayas_row("same")]}
    assert len(fetch("himalayas", lambda q: page, queries=["UX research", "human factors"])) == 1


@pytest.mark.parametrize("page,message", [
    ({"totalCount": 30, "limit": 20, "offset": 0, "jobs": [himalayas_row("same")]}, "ignored the requested page"),
    ({"totalCount": 10, "limit": 20, "offset": 0, "jobs": []}, "no listings for a nonzero"),
    ({"totalCount": 1, "limit": 500, "offset": 0, "jobs": []}, "metadata missing"),
])
def test_himalayas_inconsistent_pages_are_failures(page, message):
    calls = []

    def handler(query):
        calls.append(query)
        return page if len(calls) == 1 else dict(page, offset=0)

    with pytest.raises(SourceError, match=message):
        fetch("himalayas", handler, queries=["UX research"])


def test_himalayas_requires_queries():
    with pytest.raises(SourceError, match="queries"):
        fetch("himalayas", lambda q: {}, queries=[])


def test_rate_limiting_surfaces_and_is_not_an_empty_snapshot():
    with pytest.raises(HttpError) as failure:
        fetch("remotive", lambda q: (429, {"error": "Rate limited"}))
    assert failure.value.status == 429


def test_a_listing_link_on_another_host_is_rejected():
    row = dict(remotive_row(), url="https://untrusted.example.com/job/123")
    with pytest.raises(SourceError, match="listing URL"):
        fetch("remotive", lambda q: {"job-count": 1, "jobs": [row]})


def test_an_image_only_description_stays_empty_rather_than_invented_or_dropped():
    row = {"id": "55", "position": "UX Researcher", "company": "Example", "description": "<img src='https://example.com/job.png'>",
           "url": "https://remoteok.com/remote-jobs/ux-researcher-55"}
    (job,) = fetch("remoteok", lambda q: [{"legal": "Credit Remote OK"}, row])
    assert job.description == "" and job.location == "Remote; restrictions unspecified"


@pytest.mark.parametrize("change", [{"title": " "}, {"company_name": ""}, {"id": ""}])
def test_an_incomplete_posting_aborts_the_snapshot(change):
    with pytest.raises(SourceError, match="Incomplete"):
        fetch("remotive", lambda q: {"job-count": 1, "jobs": [dict(remotive_row(), **change)]})


def test_unknown_provider_is_refused():
    with pytest.raises(SourceError, match="Unsupported"):
        fetch("craigslist", lambda q: {})
