"""GitHub job lists: rows read as pointers to employers' own postings, and each posting judged once."""

import json

import pytest
from ajh import collect, registry
from ajh.http import Client, HttpError
from ajh.layout import Layout
from ajh.model import SourceError
from ajh.sources import lists as L
from helpers import (
    ACME,
    NOW,
    REMOTIVE,
    Boards,
    ashby_posting,
    full_tick,
    lever_posting,
    plan,
    posting,
    remotive_posting,
    rows,
    sr_posting,
    tick,
    wd_posting,
)

LEVER_ID = "0f3c1a2b-1111-2222-3333-444455556666"
ASHBY_ID = "1a2b3c4d-5555-6666-7777-888899990000"
JSON_LIST = {
    "id": "joblist",
    "ats": "github-list",
    "repo": "someone/job-list",
    "path": "jobs.json",
    "filters": {"region": ["US"], "seniority": ["junior", "mid"]},
}
README = {"id": "radar", "ats": "github-list", "repo": "someone/job-radar", "path": "README.md", "label": "Job radar"}
HOOLI = "https://jobs.smartrecruiters.com/Hooli/744000001-ux-researcher"


def role(company, url, title="UX Researcher", region="US", seniority="mid"):
    """A row as one structured list writes it."""
    return {"title": title, "company": company, "location": "Remote, US", "region": region, "state": "",
            "locationType": "remote", "seniority": seniority, "tags": ["Qualitative"], "salary": "", "url": url,
            "source": "Example", "postedAt": ""}  # fmt: skip


ROLES = [
    role("Acme", "https://job-boards.greenhouse.io/acme/jobs/1?gh_src=list"),  # on a board the feed watches
    role("Initech", f"https://jobs.lever.co/initech/{LEVER_ID}"),
    role("Globex", f"https://jobs.ashbyhq.com/globex/{ASHBY_ID}"),
    role("Hooli", HOOLI, region=""),  # a row that does not say is kept
    role("Globex", "https://globex.wd5.myworkdayjobs.com/en-US/External/job/Springfield/UX-Researcher_R1"),
    role("Acme", "https://job-boards.greenhouse.io/acme/jobs/2", region="Europe"),
    role("Initech", f"https://jobs.lever.co/initech/{LEVER_ID[:-1]}7", seniority="staff"),
    role("Globex", "https://globex.icims.com/jobs/7/job?in_iframe=1"),
    role("Initech", "https://www.linkedin.com/jobs/view/1"),
    role("Hooli", "https://careers.hooli.example/jobs/1"),
    role("Initech", f"https://jobs.lever.co/initech/{LEVER_ID}?utm_source=list"),  # named twice
]
RADAR = """# Job radar

## Mid

| Company | Role | Location | Application | Age |
|:---|:---|:---|:---:|:---:|
| **Hooli** 🔥 🇺🇸<br><sub>via Remotive</sub> | UX Researcher ⚠️<br><sub>req 2y</sub> | Remote<br>restrictions unspecified | [Apply](HOOLI) | 0d |
| ↳ | Research Ops<br><sub>pref 1–3y</sub> | San Jose, CA | [Apply](https://jobs.lever.co/hooli/99999999-1111-2222-3333-444455556666) | 3d |

<details><summary>Show 1 role</summary>

| Company | Role | Location | Application | Age |
|:---|:---|:---|:---:|:---:|
| **Initech** 🛂 | UX Researcher | Austin, TX | [Apply](https://himalayas.app/companies/initech/jobs/1) | 5d |

</details>

## Source health

| Source | Last successful fetch | Jobs | Error |
|:---|:---|:---|:---|
| Hooli | 2026-10-13 | 3 | |
""".replace("HOOLI", HOOLI)


def boards():
    found = Boards(acme=[posting(1)], globex=[posting(5, published="2026-07-01T00:00:00Z")])
    found.lists = {
        "someone/job-list/main/jobs.json": json.dumps({"updated": "2026-10-13T00:00:00Z", "count": 11, "roles": ROLES}),
        "someone/job-radar/main/README.md": RADAR,
    }
    found.lever = {"initech": {LEVER_ID: lever_posting(LEVER_ID)}, "hooli": {}}
    found.ashby = {"globex": [ashby_posting(ASHBY_ID)]}
    found.hooli = {"744000001": sr_posting("744000001")}
    found.workday = {"R1": wd_posting("R1")}
    return found


@pytest.mark.parametrize(
    "url, source_id",
    [
        ("https://job-boards.greenhouse.io/acme/jobs/4012345?gh_src=abc", "greenhouse~acme~4012345"),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345", "greenhouse~acme~4012345"),
        (f"https://jobs.lever.co/initech/{LEVER_ID}/apply", f"lever~initech~{LEVER_ID}"),
        (f"https://jobs.ashbyhq.com/globex/{ASHBY_ID.upper()}/application?utm_source=x", f"ashby~globex~{ASHBY_ID}"),
        (HOOLI, "smartrecruiters~Hooli~744000001"),
        ("https://globex.wd1.myworkdayjobs.com/External/job/City/UX-Researcher_R123", "workday~globex~External~R123"),
        (
            "https://globex.wd5.myworkdayjobs.com/en-US/External/job/City/UX-Researcher_JR-0042/apply",
            "workday~globex~External~JR-0042",
        ),
        (
            "https://wd1.myworkdaysite.com/recruiting/globex/Careers/job/City/UX-Researcher_R9",
            "workday~globex~Careers~R9",
        ),
    ],
)
def test_a_link_to_one_posting_on_a_system_with_a_public_api_names_where_it_lives(url, source_id):
    found = L.link(url)
    assert found.source_id == source_id and "?" not in found.url and not found.url.endswith(("/apply", "/application"))
    assert L.link(found.url).source_id == source_id  # the posting's own page names the same place


@pytest.mark.parametrize(
    "url, why",
    [
        ("https://www.linkedin.com/jobs/view/1", "re-poster"),
        ("https://www.adzuna.co.uk/jobs/details/1", "re-poster"),
        ("https://simplify.jobs/p/1", "re-poster"),  # a redirect: following it would read the job from there
        ("https://globex.icims.com/jobs/7/job?in_iframe=1", "no reader"),
        ("https://www.google.com/search?q=ux+researcher", "no reader"),
        ("https://job-boards.greenhouse.io/acme", "not one posting"),
        ("https://careers.hooli.example/jobs/1", "other site"),
        (f"http://jobs.lever.co/initech/{LEVER_ID}", "no link"),
    ],
)
def test_any_other_link_is_skipped_and_says_why(url, why):
    assert L.link(url) is None and L._why(url) == why


def test_a_watched_boards_posting_lives_where_a_lists_link_to_it_says():
    assert L.board_identity(ACME, "1") == L.identity(L.link("https://job-boards.greenhouse.io/acme/jobs/1").source_id)
    site = {
        "id": "g",
        "ats": "workday",
        "company": "Globex",
        "host": "globex.wd5.myworkdayjobs.com",
        "site": "External",
    }
    linked = L.link("https://globex.wd5.myworkdayjobs.com/External/job/Springfield/UX-Researcher_R1")
    assert L.board_identity(site, "R1") == L.identity(linked.source_id) == ("workday", "globex/external", "r1")
    assert L.board_identity(REMOTIVE, "1") is None and L.identity("acme-123") is None


def test_markdown_tables_give_their_rows_and_other_tables_are_skipped():
    facts = [found for found, _ in L._markdown_rows(README, RADAR)]
    assert facts == [
        {"company": "Hooli", "title": "UX Researcher", "location": "Remote; restrictions unspecified", "link": HOOLI},
        {"company": "Hooli", "title": "Research Ops", "location": "San Jose, CA",
         "link": "https://jobs.lever.co/hooli/99999999-1111-2222-3333-444455556666"},
        {"company": "Initech", "title": "UX Researcher", "location": "Austin, TX",
         "link": "https://himalayas.app/companies/initech/jobs/1"},
    ]  # fmt: skip


def test_sources_test_says_what_became_of_every_row_and_hosts_name_every_system(repo):
    root = repo(sources=(JSON_LIST, README))
    report = registry.test(root, Client(boards().transport, sleep=lambda s: None, retries=0))
    assert report["failing"] == []
    assert report["sources"]["joblist"] == {
        "rows": 11,
        "filtered": 2,
        "skipped": {"no reader": 1, "re-poster": 1, "other site": 1},
        "repeated": 1,
        "systems": {"greenhouse": 1, "lever": 1, "ashby": 1, "smartrecruiters": 1, "workday": 1},
        "postings": 5,
    }
    assert report["sources"]["radar"]["skipped"] == {"re-poster": 1}
    hosts = registry.hosts(root)["hosts"]
    assert {"raw.githubusercontent.com", "boards-api.greenhouse.io", "api.lever.co", "*.myworkdayjobs.com"} <= set(
        hosts
    )


def test_each_posting_is_judged_once_wherever_it_is_listed(repo):
    root = repo(sources=(ACME, JSON_LIST, README, REMOTIVE))
    found = boards()
    found.remotive = [remotive_posting(7, company="Initech")]  # an aggregator's copy of a list's posting
    tick(root, found)
    listed = rows(root, "joblist")
    assert (listed["greenhouse~acme~1"].status, listed["greenhouse~acme~1"].note) == ("V", "acme:1")
    assert all(listed[jid].status == "P" for jid in listed if jid != "greenhouse~acme~1") and len(listed) == 5
    radar = rows(root, "radar")
    hooli = "smartrecruiters~Hooli~744000001"
    assert (radar[hooli].status, radar[hooli].note) == ("V", f"joblist:{hooli}")  # two lists, one posting
    assert (rows(root, "remotive")["7"].status, rows(root, "remotive")["7"].note) == (
        "V",
        f"joblist:lever~initech~{LEVER_ID}",
    )
    assert sorted(plan(root)["queue"]["triage"]) == [
        "acme:1",
        f"joblist:ashby~globex~{ASHBY_ID}",
        f"joblist:lever~initech~{LEVER_ID}",
        f"joblist:{hooli}",
        "joblist:workday~globex~External~R1",
    ]


def test_a_lists_posting_is_the_employers_own_with_its_text_and_date_from_their_system(repo):
    root = repo(sources=(JSON_LIST,), criteria={"backfill": {"days": 45}})
    found = boards()
    found.lists["someone/job-list/main/jobs.json"] = json.dumps(
        [role("Initech", f"https://jobs.lever.co/initech/{LEVER_ID}"),
         role("Globex", "https://job-boards.greenhouse.io/globex/jobs/5")]  # first published in July
    )  # fmt: skip
    full_tick(root, found)
    assert rows(root, "joblist")["greenhouse~globex~5"].status == "S"  # older than the backfill limit, by its own date
    records = {r["key"]: r for r in map(json.loads, Layout(root).positions.read_text().splitlines())}
    record = records[f"joblist:lever~initech~{LEVER_ID}"]
    assert (record["source_kind"], record["source_label"], record["url"]) == (
        "official",
        "GitHub list someone/job-list",
        f"https://jobs.lever.co/initech/{LEVER_ID}",
    )
    assert record["posted"] == "2026-10-10" and record["company"] == "Initech"
    brief = (root / record["brief"]).read_text()
    assert lever_posting(LEVER_ID)["description"][3:40] in brief and "Employment: Full-time" in brief


def test_a_list_that_cannot_be_read_or_has_no_rows_fails_without_closing_anything(repo):
    client = Client(boards().transport, sleep=lambda s: None, retries=0)
    with pytest.raises(HttpError):
        L.read(client, {**JSON_LIST, "path": "moved.json"})
    found = boards()
    found.lists["someone/job-list/main/jobs.json"] = json.dumps({"roles": []})
    with pytest.raises(SourceError, match="no rows"):
        L.read(Client(found.transport, sleep=lambda s: None, retries=0), JSON_LIST)
    root = repo(sources=(JSON_LIST,))
    tick(root, boards())
    tick(root, found, now=NOW.replace(hour=13))
    assert len(rows(root, "joblist")) == 5 and all(r.miss == 0 for r in rows(root, "joblist").values())


def test_a_lists_entry_is_checked_before_anything_is_read(repo):
    bad = {"id": "bad", "ats": "github-list", "repo": "nobody", "path": "../jobs.txt", "filters": {"region": "US"}}
    root = repo(sources=(bad,))
    with pytest.raises(collect.ConfigError) as refused:
        collect.load_sources(Layout(root).sources)
    detail = str(refused.value)
    assert "repo must be" in detail and "path must be" in detail and "filters must" in detail
    assert L.problems({**JSON_LIST, "path": "jobs.txt"}) == ["joblist: format must be json or markdown"]
    assert L.problems({**JSON_LIST, "path": "jobs.txt", "format": "markdown"}) == []
