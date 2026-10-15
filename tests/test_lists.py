"""GitHub job lists: rows read as pointers to employers' own postings, and each posting judged once."""

import json

import pytest
from ajh import collect, registry, requeue
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
README = {
    "id": "tables",
    "ats": "github-list",
    "repo": "someone/open-roles",
    "path": "README.md",
    "label": "Open roles",
}
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
TABLES = """# Open roles

## 🌿 Open Mid

| Company | Role | Location | Application | Age |
|:---|:---|:---|:---:|:---:|
| **Hooli** 🔥 🇺🇸<br><sub>via Remotive</sub> | UX Researcher ⚠️<br><sub>req 2y</sub> | Remote<br>restrictions unspecified | [Apply](HOOLI) | 0d |
| ↳ | Research Ops<br><sub>pref 1–3y</sub> | San Jose, CA | [Apply](https://jobs.lever.co/hooli/99999999-1111-2222-3333-444455556666) | 3d |

## 🌳 Open Senior

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


PLACES = {"id": "places", "ats": "github-list", "repo": "someone/places", "path": "jobs.json",
          "filters": {"region": ["US"]}}  # fmt: skip
CAMPUS = "Springfield, Acme Campus, 1 Example Drive"
WORKDAY = "https://globex.wd5.myworkdayjobs.com/en-US/External/job/Springfield/UX-Researcher_"
ABROAD = "workday~globex~External~R7"


def placed_list():
    """A list that labels every row US, linking to Workday postings whose own records say where they are."""
    found = Boards()
    found.lists = {
        "someone/places/main/jobs.json": json.dumps([
            dict(role("Globex", WORKDAY + "R7"), location=CAMPUS),  # the record says United Kingdom
            dict(role("Globex", WORKDAY + "R8"), location=CAMPUS),  # the record says United States
            dict(role("Globex", WORKDAY + "R9"), location="3 Locations"),  # one of them in the US
            dict(role("Globex", WORKDAY + "R10"), location="Springfield, Acme Campus"),  # no country anywhere
        ])
    }  # fmt: skip
    found.workday = {
        "R7": wd_posting("R7", places=(CAMPUS,), country="United Kingdom"),
        "R8": wd_posting("R8", places=(CAMPUS,), country="United States of America"),
        "R9": wd_posting("R9", places=("Springfield, Acme Campus", "US, CA, San Jose", "Germany, Berlin"),
                         country="United Kingdom"),
        "R10": wd_posting("R10", places=("Springfield, Acme Campus",)),
    }  # fmt: skip
    return found


def boards():
    found = Boards(acme=[posting(1)], globex=[posting(5, published="2026-07-01T00:00:00Z")])
    found.lists = {
        "someone/job-list/main/jobs.json": json.dumps({"updated": "2026-10-13T00:00:00Z", "count": 11, "roles": ROLES}),
        "someone/open-roles/main/README.md": TABLES,
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
    facts = [found for found, _ in L._markdown_rows(README, TABLES)]
    assert facts == [
        {"company": "Hooli", "title": "UX Researcher", "location": "Remote; restrictions unspecified", "link": HOOLI},
        {"company": "Hooli", "title": "Research Ops", "location": "San Jose, CA",
         "link": "https://jobs.lever.co/hooli/99999999-1111-2222-3333-444455556666"},
        {"company": "Initech", "title": "UX Researcher", "location": "Austin, TX",
         "link": "https://himalayas.app/companies/initech/jobs/1"},
    ]  # fmt: skip


def test_sections_keep_only_the_tables_under_the_headings_named(repo):
    found = boards()
    client = Client(found.transport, sleep=lambda s: None, retries=0)
    listed, tally = L.read(client, {**README, "sections": ["Mid"]})
    assert (tally["rows"], tally["filtered"], tally["skipped"]) == (3, 1, {})  # the Senior table is left out
    assert sorted(listed) == ["lever~hooli~99999999-1111-2222-3333-444455556666", "smartrecruiters~Hooli~744000001"]
    assert L.problems({**README, "sections": "Mid"}) == ["tables: sections must be a list of headings"]
    # names are words in the heading, which may begin with a picture and a shared word
    assert L.read(client, {**README, "sections": ["Mid", "Junior"]})[1]["unmatched"] == {"sections": ["Junior"]}
    with pytest.raises(SourceError, match=r"No heading of the list contains any of `sections`.*open mid, open senior"):
        L.read(client, {**README, "sections": ["Junior", "Midwest"]})  # a typo would empty the list: refused


def test_a_filter_that_matches_nothing_in_the_list_is_refused_rather_than_closing_its_postings(repo):
    client = Client(boards().transport, sleep=lambda s: None, retries=0)
    with pytest.raises(SourceError, match="No row of the list has a value for the filter field 'Region'"):
        L.read(client, {**JSON_LIST, "filters": {"Region": ["US"]}})
    with pytest.raises(SourceError, match=r"None of \['USA'\] is a region in the list; it has \['europe', 'us'\]"):
        L.read(client, {**JSON_LIST, "filters": {"region": ["USA"]}})


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
        "unmatched": {"seniority": ["junior"]},  # no junior role in the list today: said, not refused
    }
    assert report["sources"]["tables"]["skipped"] == {"re-poster": 1}
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
    tables = rows(root, "tables")
    hooli = "smartrecruiters~Hooli~744000001"
    assert (tables[hooli].status, tables[hooli].note) == ("V", f"joblist:{hooli}")  # two lists, one posting
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


def test_a_board_named_in_another_case_is_the_same_board(repo):
    root = repo(sources=(JSON_LIST, README))
    found = boards()
    found.lists["someone/open-roles/main/README.md"] = TABLES.replace("/Hooli/", "/hooli/")
    tick(root, found)
    tables = rows(root, "tables")["smartrecruiters~hooli~744000001"]  # in the state, so its text was fetched
    assert (tables.status, tables.note) == ("V", "joblist:smartrecruiters~Hooli~744000001")
    site = {"id": "g", "ats": "workday", "company": "Globex", "host": "globex.wd5.myworkdayjobs.com", "site": 7}
    assert L.board_identity(site, "R1") is None


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


def test_a_posting_the_list_labels_us_but_whose_own_record_is_abroad_is_parked_not_judged(repo):
    root = repo(sources=(PLACES,), criteria={"locations": {"regions": ["US"]}})
    found = placed_list()
    summary = tick(root, found)
    table = rows(root, "places")
    assert (table[ABROAD].status, table[ABROAD].note) == ("S", "place:GB")  # parked, and why
    assert (summary["outside_regions"], summary["new"], summary["dropped"]) == (1, 3, {})
    # in the US, in the US among other places, or in no country the engine can read: judged as before
    assert {jid: row.status for jid, row in table.items() if jid != ABROAD} == dict.fromkeys(
        ["workday~globex~External~R8", "workday~globex~External~R9", "workday~globex~External~R10"], "P"
    )
    assert f"places:{ABROAD}" not in plan(root)["queue"]["triage"] and len(plan(root)["queue"]["triage"]) == 3

    found.details()
    summary = tick(root, found)  # the list alone says nothing new about it, so its record is not asked for
    assert sorted(found.details()) == ["R10", "R8", "R9"]  # the others are waiting for triage
    assert (rows(root, "places")[ABROAD].status, summary["outside_regions"], summary["new"]) == ("S", 0, 0)
    assert f"places:{ABROAD}" not in plan(root)["queue"]["triage"]


def test_widened_regions_bring_a_posting_parked_for_its_place_back_and_narrowed_ones_park_it(repo):
    root = repo(sources=(PLACES,), criteria={"locations": {"regions": ["US"]}})
    found = placed_list()
    tick(root, found)
    assert rows(root, "places")[ABROAD].status == "S"
    repo(sources=(PLACES,), criteria={"locations": {"regions": ["US", "GB"]}})
    found.details()
    summary = tick(root, found)
    assert (rows(root, "places")[ABROAD].status, rows(root, "places")[ABROAD].note) == ("P", "-")
    assert summary["unparked"] == 1 and "R7" not in found.details()  # from the stored countries alone
    assert f"places:{ABROAD}" not in plan(root)["queue"]["triage"]  # its text comes with the next update
    tick(root, found)
    assert f"places:{ABROAD}" in plan(root)["queue"]["triage"]

    repo(sources=(PLACES,), criteria={"locations": {"regions": ["US"]}})
    summary = tick(root, found)  # still waiting for triage, and its record says it is outside again
    assert (rows(root, "places")[ABROAD].status, rows(root, "places")[ABROAD].note) == ("S", "place:GB")
    assert summary["outside_regions"] == 1 and f"places:{ABROAD}" not in plan(root)["queue"]["triage"]


def test_a_parked_posting_whose_own_record_moves_into_the_regions_comes_back(repo):
    root = repo(sources=(PLACES,), criteria={"locations": {"regions": ["US"]}})
    found = placed_list()
    tick(root, found)
    found.workday["R7"]["country"] = "United States of America"  # the employer moved it
    tick(root, found)
    assert rows(root, "places")[ABROAD].status == "S"  # nothing in the list changed, so nothing asked
    listed = json.loads(found.lists["someone/places/main/jobs.json"])
    listed[0]["location"] = "Springfield"  # the list's row changed, so the record is read again
    found.lists["someone/places/main/jobs.json"] = json.dumps(listed)
    summary = tick(root, found)
    assert (rows(root, "places")[ABROAD].status, rows(root, "places")[ABROAD].note) == ("P", "-")
    assert summary["unparked"] == 1 and f"places:{ABROAD}" in plan(root)["queue"]["triage"]


def test_each_system_a_list_leads_to_gives_the_place_that_decides(repo):
    root = repo(sources=(JSON_LIST,), criteria={"locations": {"regions": ["US"]}})
    found = boards()
    found.jobs["globex"][5]["location"] = {"name": "Berlin, Germany"}
    found.lever["initech"][LEVER_ID] = lever_posting(LEVER_ID, location="London, United Kingdom")
    found.ashby["globex"][0]["location"] = "Toronto, Canada"
    found.hooli["744000002"] = sr_posting("744000002", full="Lisbon, , Portugal")
    found.lists["someone/job-list/main/jobs.json"] = json.dumps(
        [role("Globex", "https://job-boards.greenhouse.io/globex/jobs/5"),
         role("Initech", f"https://jobs.lever.co/initech/{LEVER_ID}"),
         role("Globex", f"https://jobs.ashbyhq.com/globex/{ASHBY_ID}"),
         role("Hooli", HOOLI),  # San Jose, CA, United States
         role("Hooli", "https://jobs.smartrecruiters.com/Hooli/744000002-ux-researcher")]
    )  # fmt: skip
    tick(root, found)
    assert {jid: (row.status, row.note) for jid, row in rows(root, "joblist").items()} == {
        "greenhouse~globex~5": ("S", "place:DE"),
        f"lever~initech~{LEVER_ID}": ("S", "place:GB"),
        f"ashby~globex~{ASHBY_ID}": ("S", "place:CA"),
        "smartrecruiters~Hooli~744000001": ("P", "-"),
        "smartrecruiters~Hooli~744000002": ("S", "place:PT"),
    }


def test_postings_parked_for_their_place_are_counted_apart_and_left_parked_by_requeue(repo):
    root = repo(sources=(PLACES,), criteria={"locations": {"regions": ["US"]}, "backfill": {"days": 30}})
    found = placed_list()
    listed = json.loads(found.lists["someone/places/main/jobs.json"])
    found.lists["someone/places/main/jobs.json"] = json.dumps(
        [*listed, role("Initech", f"https://jobs.lever.co/initech/{LEVER_ID}")]
    )
    found.lever = {"initech": {LEVER_ID: lever_posting(LEVER_ID, created=1767225600000)}}  # first posted 2026-01-01
    full_tick(root, found)
    assert (rows(root, "places")[ABROAD].status, rows(root, "places")[f"lever~initech~{LEVER_ID}"].status) == ("S", "S")
    counts = json.loads(Layout(root).feed_meta.read_text())["counts"]
    assert (counts["parked"], counts["outside_regions"], counts["published"]) == (1, 1, 3)
    readme = Layout(root).readme.read_text()
    assert "- Parked as older than the backfill limit: 1." in readme
    assert (
        "- Parked as outside `locations.regions` by the employer's own record, which the listing did not show: 1."
        in readme
    )
    assert requeue.run(root, "parked")["requeued"] == 1  # only the one parked for its age
    table = rows(root, "places")
    assert (table[f"lever~initech~{LEVER_ID}"].status, table[ABROAD].status) == ("P", "S")
