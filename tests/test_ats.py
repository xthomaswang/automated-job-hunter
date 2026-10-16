import copy
import json

import pytest
from ajh import sources
from ajh.http import Client, HttpError
from ajh.model import Job, SourceError, path_id
from ajh.sources import lists as L
from ajh.sources.ats import feed_url, parse_feed

GREENHOUSE = {"id": "acme", "company": "Acme", "ats": "greenhouse", "slug": "acme"}
ASHBY = {"id": "globex", "company": "Globex", "ats": "ashby", "slug": "globex"}
LEVER = {"id": "initech", "company": "Initech", "ats": "lever", "slug": "initech"}
RECRUITEE = {"id": "hooli", "company": "Hooli", "ats": "recruitee", "slug": "hooli"}

GREENHOUSE_FEED = {
    "jobs": [
        {
            "id": 4001,
            "title": "UX Researcher",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/4001",
            "content": "&lt;p&gt;Run usability studies.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;3+ years required&lt;/li&gt;&lt;/ul&gt;",
            "location": {"name": "Remote, US"},
            "first_published": "2026-10-01T09:00:00-04:00",
            "updated_at": "2026-10-05T09:00:00-04:00",
            "departments": [{"id": 1, "name": "Design"}, {"id": 2, "name": "Research"}],
        }
    ]
}

ASHBY_FEED = {
    "apiVersion": "1",
    "jobs": [
        {
            "title": "Product Analyst",
            "jobUrl": "https://jobs.ashbyhq.com/globex/1b2c3d4e-0000-4000-8000-000000000001",
            "applyUrl": "https://jobs.ashbyhq.com/globex/1b2c3d4e-0000-4000-8000-000000000001/application",
            "descriptionHtml": "<h2>The role</h2><p>Design experiments.</p>",
            "descriptionPlain": "ignored when HTML is present",
            "location": "New York",
            "secondaryLocations": [{"location": "Boston"}, {"location": "New York"}],
            "publishedAt": "2026-10-02T12:00:00.000+00:00",
            "employmentType": "FullTime",
            "department": "Data",
            "team": "Insights",
            "isListed": True,
        },
        {
            "title": "Hidden role",
            "jobUrl": "https://jobs.ashbyhq.com/globex/hidden",
            "descriptionPlain": "x",
            "isListed": False,
        },
        {
            "title": "Plain only",
            "jobUrl": "https://jobs.ashbyhq.com/globex/plain-1/",
            "descriptionPlain": " Plain text posting. ",
        },
    ],
}

LEVER_FEED = [
    {
        "id": "a1b2",
        "text": "Design Researcher",
        "hostedUrl": "https://jobs.lever.co/initech/a1b2",
        "applyUrl": "https://jobs.lever.co/initech/a1b2/apply",
        "createdAt": 1790000000000,
        "description": "<div>Mixed-methods research.</div>",
        "lists": [{"text": "Requirements", "content": "<li>Interview skills</li><li>SQL</li>"}],
        "additional": "<p>We are an equal opportunity employer.</p>",
        "categories": {
            "location": "Seattle, WA",
            "commitment": "Full-time",
            "team": "Research",
            "department": "Product",
        },
    }
]

RECRUITEE_FEED = {
    "offers": [
        {
            "id": 123,
            "title": "Researcher",
            "careers_url": "https://careers.hooli.example/o/researcher",
            "description": "<p>Run usability studies.</p>",
            "requirements": "<p>One year preferred.</p>",
            "city": "Amsterdam",
            "country": "Netherlands",
            "published_at": "2026-09-30 08:00:00 UTC",
            "employment_type_code": "fulltime",
            "department": "Research",
        }
    ]
}


def test_greenhouse_posting_is_normalized_with_its_structure_and_department():
    (job,) = parse_feed(GREENHOUSE, GREENHOUSE_FEED)
    assert job.key == "acme:4001" and job.source_id == "4001" and job.company == "Acme"
    assert job.description == "Run usability studies.\n- 3+ years required"
    assert job.location == "Remote, US" and job.department == "Design / Research"
    assert job.posted_at == "2026-10-01T09:00:00-04:00" and job.employment_type == "unknown"
    assert job.source_kind == "official" and job.url.endswith("/acme/jobs/4001")


def test_ashby_skips_unlisted_postings_and_merges_locations():
    analyst, plain = parse_feed(ASHBY, ASHBY_FEED)
    assert analyst.source_id == "1b2c3d4e-0000-4000-8000-000000000001"
    assert analyst.description == "The role\nDesign experiments."
    assert analyst.location == "New York / Boston" and analyst.department == "Data / Insights"
    assert analyst.employment_type == "FullTime" and analyst.apply_url.endswith("/application")
    assert plain.source_id == "plain-1" and plain.description == "Plain text posting." and plain.location == "unknown"


def test_lever_joins_its_sections_and_dates_the_posting():
    (job,) = parse_feed(LEVER, LEVER_FEED)
    assert job.description == (
        "Mixed-methods research.\nRequirements\n- Interview skills\n- SQL\nWe are an equal opportunity employer."
    )
    assert job.posted_at == "2026-09-21" and job.employment_type == "Full-time"
    assert job.location == "Seattle, WA" and job.department == "Product / Research"


def test_recruitee_keeps_requirements_and_the_source_link():
    (job,) = parse_feed(RECRUITEE, RECRUITEE_FEED)
    assert job.source_id == "123" and job.url == RECRUITEE_FEED["offers"][0]["careers_url"]
    assert job.description == "Run usability studies.\nOne year preferred."
    assert job.location == "Amsterdam, Netherlands" and job.department == "Research"


@pytest.mark.parametrize(
    "source,payload",
    [
        (RECRUITEE, {"error": "authentication required"}),
        (GREENHOUSE, {"jobs": None}),
        (GREENHOUSE, []),
        (LEVER, {"ok": False}),
        (ASHBY, {"jobs": ["not an object"]}),
    ],
)
def test_a_payload_that_is_not_a_complete_board_is_refused(source, payload):
    with pytest.raises(SourceError):
        parse_feed(source, payload)


def broken(feed, **changes):
    feed = copy.deepcopy(feed)
    row = feed["jobs"][0]
    for field, value in changes.items():
        if value is None:
            row.pop(field)
        else:
            row[field] = value
    return feed


@pytest.mark.parametrize(
    "changes",
    [
        {"content": ""},
        {"content": "&lt;script&gt;x&lt;/script&gt;"},
        {"title": "  "},
        {"title": None},
        {"title": None, "absolute_url": None},
        {"id": None},
        {"id": None, "absolute_url": None},  # nor can its page on Greenhouse be named without its id
        {"id": "4001/x", "absolute_url": "http://insecure.example.com/1"},  # nor by an id that is not a number
    ],
)
def test_one_incomplete_posting_aborts_the_snapshot_instead_of_dropping_the_job(changes):
    with pytest.raises(SourceError):
        parse_feed(GREENHOUSE, broken(GREENHOUSE_FEED, **changes))


def test_a_greenhouse_posting_without_an_https_link_of_its_own_is_linked_to_its_page_on_greenhouse():
    own = "https://www.acme.example/careers?gh_jid=4001"  # on the employer's own site
    feed = broken(GREENHOUSE_FEED, absolute_url=own)
    assert [job.url for job in parse_feed(GREENHOUSE, feed)] == [own]  # a board whose links are https is unchanged
    row = feed["jobs"][0]
    missing = {field: value for field, value in row.items() if field != "absolute_url"}
    insecure = dict(row, id=4003, absolute_url="http://www.acme.example/careers?gh_jid=4003")
    feed["jobs"] += [dict(missing, id=4002), insecure, dict(row, id=4004, absolute_url=None)]
    jobs = parse_feed(GREENHOUSE, feed)  # read whole
    assert [(job.source_id, job.url) for job in jobs] == [
        ("4001", own),
        ("4002", "https://job-boards.greenhouse.io/acme/jobs/4002"),
        ("4003", "https://job-boards.greenhouse.io/acme/jobs/4003"),
        ("4004", "https://job-boards.greenhouse.io/acme/jobs/4004"),
    ]
    assert [L.link(job.url).source_id for job in jobs[1:]] == [f"greenhouse~acme~{n}" for n in (4002, 4003, 4004)]


def test_duplicate_ids_abort_the_snapshot():
    feed = {"jobs": GREENHOUSE_FEED["jobs"] * 2}
    with pytest.raises(SourceError, match="Duplicate"):
        parse_feed(GREENHOUSE, feed)


@pytest.mark.parametrize(
    "source,slug",
    [
        *[(GREENHOUSE, slug) for slug in ("", "acme/../x", "acme?x=1", "a b", None)],
        *[(other, name) for other in (GREENHOUSE, LEVER, RECRUITEE) for name in ("Acme AI", "acme.com")],
        *[
            (ASHBY, name)
            for name in ("", ".", "..", " Acme", "Acme ", "Acme/AI", "Acme%20AI", "Acme?x=1", "a\tb", None)
        ],
    ],
)
def test_board_slugs_cannot_smuggle_url_syntax(source, slug):
    with pytest.raises(SourceError):
        feed_url(dict(source, slug=slug))


def transport_for(payload, status=200):
    seen = []

    def transport(method, url, headers, body, timeout):
        seen.append(url)
        return status, {}, json.dumps(payload).encode()

    return transport, seen


@pytest.mark.parametrize(
    "source,feed,url",
    [
        (GREENHOUSE, GREENHOUSE_FEED, "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"),
        (ASHBY, ASHBY_FEED, "https://api.ashbyhq.com/posting-api/job-board/globex"),
        (LEVER, LEVER_FEED, "https://api.lever.co/v0/postings/initech?mode=json"),
        (RECRUITEE, RECRUITEE_FEED, "https://hooli.recruitee.com/api/offers/"),
    ],
)
def test_fetch_reads_each_board_from_its_public_endpoint(source, feed, url):
    transport, seen = transport_for(feed)
    jobs = sources.fetch(Client(transport), source)
    assert seen == [url] and jobs and all(job.source == source["id"] for job in jobs)


@pytest.mark.parametrize("name,path", [("Acme AI", "Acme%20AI"), ("acme.com", "acme.com")])
def test_an_ashby_board_whose_name_has_a_space_or_a_dot_is_read_like_any_other(name, path):
    feed = json.loads(json.dumps(ASHBY_FEED).replace("/globex/", f"/{path}/"))  # its links, as Ashby writes them
    transport, seen = transport_for(feed)
    analyst, plain = sources.fetch(Client(transport), dict(ASHBY, slug=name))
    assert seen == [f"https://api.ashbyhq.com/posting-api/job-board/{path}"]
    assert analyst.url == f"https://jobs.ashbyhq.com/{path}/1b2c3d4e-0000-4000-8000-000000000001"
    assert (analyst.source_id, plain.source_id) == ("1b2c3d4e-0000-4000-8000-000000000001", "plain-1")


def test_a_blocked_board_is_a_failure_not_an_empty_board():
    transport, _ = transport_for({"message": "unauthorized"}, status=401)
    with pytest.raises(HttpError):
        sources.fetch(Client(transport), RECRUITEE)
    with pytest.raises(SourceError):
        sources.fetch(Client(transport_for({})[0]), {"id": "x", "company": "X", "ats": "carrier-pigeon"})


def test_job_round_trips_and_rejects_inconsistent_identity():
    (job,) = parse_feed(GREENHOUSE, GREENHOUSE_FEED)
    assert Job.from_dict(dict(job.to_dict(), unknown_field=1)) == job
    with pytest.raises(SourceError):
        Job.from_dict(dict(job.to_dict(), key="other:4001"))
    with pytest.raises(SourceError):
        Job.from_dict(dict(job.to_dict(), source_kind="rumor"))


def test_path_id_is_file_safe_and_never_merges_distinct_ids():
    assert path_id("4001") == "4001" and path_id("1b2c-3d4e_x.y") == "1b2c-3d4e_x.y"
    odd, long_a, long_b = path_id("R/123 (EU)"), path_id("x" * 200), path_id("x" * 199 + "y")
    for value in (odd, long_a, long_b):
        assert len(value) <= 80 and "/" not in value and " " not in value
    assert odd.startswith("R-123-EU") and long_a != long_b
    assert path_id("a/b") != path_id("a-b")
    assert path_id("..") not in ("", ".", "..")
