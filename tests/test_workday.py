"""Workday career sites: listings read through searches, text fetched only where an update needs it."""

import json

import pytest
from ajh import registry
from ajh import sources as S
from ajh.http import Client
from ajh.layout import Layout
from ajh.model import SourceError
from ajh.sources import skips, workday
from helpers import INITECH, Boards, full_tick, good_assessment, plan, rows, tick, wd_posting

SEARCHED = dict(INITECH, queries=["UX Researcher"])


def client(board):
    return Client(board.transport, sleep=lambda seconds: None, retries=0)


def site(*postings):
    board = Boards()
    board.workday = {posting["row"]["bulletFields"][0]: posting for posting in postings}
    return board


def positions(root):
    return {r["key"]: r for r in map(json.loads, Layout(root).positions.read_text().splitlines())}


def test_a_site_is_read_through_its_searches_and_text_is_fetched_only_when_asked():
    board = site(
        wd_posting("R1"),
        wd_posting("R2", places=("US, CA, San Jose", "US, Remote")),
        wd_posting("R3", "Account Executive"),  # not matched by the search
    )
    counted = S.fetch(client(board), SEARCHED)  # no rank: enough to count, no text
    assert sorted(job.source_id for job in counted) == ["R1", "R2"]
    assert all(job.detail_skipped and not job.description for job in counted) and board.details() == []

    jobs = {job.source_id: job for job in S.fetch(client(board), SEARCHED, lambda job: S.NEW)}
    assert board.details() == ["R2", "R1"] and not any(job.detail_skipped for job in jobs.values())  # newest first
    two = jobs["R2"]
    assert (two.title, two.location, two.posted_at, two.employment_type) == (
        "UX Researcher",
        "2 Locations",  # as the listing says: change is measured on what every update can see
        "2026-10-10",
        "unknown",  # in the text instead, for the same reason
    )
    assert two.description.startswith("Locations: US, CA, San Jose / US, Remote\nTime type: Full time\n\n")
    assert two.url == "https://initech.wd1.myworkdayjobs.com/External/job/US-CA-San-Jose/UX-Researcher_R2"
    assert jobs["R1"].description.startswith("Time type: Full time\n\n")


def test_every_page_is_read_and_anything_short_of_the_total_fails_the_source(monkeypatch):
    board = site(*(wd_posting(f"R{n:02d}") for n in range(25)))
    assert len(S.fetch(client(board), SEARCHED)) == 25  # two pages; the second reports a total of 0

    def short(method, url, headers, body, timeout):
        status, sent, data = board.transport(method, url, headers, body, timeout)
        page = json.loads(data)
        if method == "POST" and page["total"]:
            page["total"] += 5  # more than it will list
        return status, sent, json.dumps(page).encode()

    with pytest.raises(SourceError, match="short of its total"):
        S.fetch(Client(short, sleep=lambda s: None, retries=0), SEARCHED)
    for bad, problem in (
        (dict(SEARCHED, host="initech.example.com"), "Workday needs a host"),
        (dict(SEARCHED, site="../x"), "Workday needs a host"),
        (dict(SEARCHED, host="wd1.myworkdaysite.com"), "Workday needs a host"),  # a shared host needs the tenant
        (dict(INITECH), "searches"),  # no queries, and none filled in from the criteria
    ):
        with pytest.raises(SourceError, match=problem):
            S.fetch(client(board), bad)
    board.workday["R00"]["row"]["externalPath"] = "/job/../../wday/x_R00"
    with pytest.raises(SourceError, match="unsafe"):
        S.fetch(client(board), SEARCHED)
    monkeypatch.setattr(workday, "MAX_PER_QUERY", 20)
    with pytest.raises(SourceError, match="set `categories`"):
        S.fetch(client(board), SEARCHED)


def test_a_row_with_neither_link_nor_title_is_skipped_and_counted_while_such_rows_are_few(repo):
    board = site(*(wd_posting(f"R{n:02d}") for n in range(25)))
    board.workday_bare = [{"bulletFields": ["R99"]}]  # a half-published posting, on the second page
    with skips.counting() as skipped:
        assert len(S.fetch(client(board), SEARCHED)) == 25
    assert skipped == {"no link or title": 1}
    root = repo(sources=(INITECH,))
    summary = tick(root, board)
    assert summary["sources"]["failed"] == 0 and len(rows(root, "initech")) == 25
    assert summary["sources"]["rows_skipped"] == {"initech": {"no link or title": 1}}
    tested = registry.test(root, client(board))["sources"]["initech"]
    assert tested == {"postings": 25, "rows_skipped": {"no link or title": 1}}

    for bare, problem in (
        ([{"bulletFields": ["R98"]}, {"bulletFields": ["R99"]}], "lists 2 of its 27 rows"),  # more than one in 20
        ([{"title": "UX Researcher", "bulletFields": ["R99"]}], "missing or unsafe"),  # a title but no link
    ):
        board.workday_bare = bare
        with pytest.raises(SourceError, match=problem):
            S.fetch(client(board), SEARCHED)
    alone = site()  # every row bare: a new shape, which must not empty the snapshot
    alone.workday_bare = [{"bulletFields": ["R99"]}]
    with pytest.raises(SourceError, match="only rows with neither a link nor a title"):
        S.fetch(client(alone), SEARCHED)


def test_new_postings_wait_for_their_text_and_settled_ones_need_none(repo):
    board = site(wd_posting("R1"), wd_posting("R2"), wd_posting("R3", "Senior UX Researcher"))

    def assess(job):  # the senior role lands below the bar, so it settles
        senior = job["title"].startswith("Senior")
        return good_assessment(job, fit=20 if senior else 82, role_match="adjacent" if senior else "direct")

    root = repo(sources=(dict(INITECH, max_details=2),))
    full_tick(root, board, assess=assess)  # the newest requisitions first
    assert board.details() == ["R3", "R2"] and sorted(rows(root, "initech")) == ["R2", "R3"]  # R1 waits outside
    assert rows(root, "initech")["R3"].status == "R"
    assert json.loads(Layout(root).feed_meta.read_text())["counts"]["waiting_for_text"] == 1  # and is counted
    assert "- New postings on career sites waiting for their text: 1." in Layout(root).readme.read_text()
    full_tick(root, board, assess=assess)  # the new one before the listed one
    assert board.details() == ["R1", "R2"] and set(positions(root)) == {"initech:R1", "initech:R2"}
    assert json.loads(Layout(root).feed_meta.read_text())["counts"]["waiting_for_text"] == 0
    full_tick(root, board, assess=assess)  # the settled one needs no text; the listed ones get it if there is room
    assert board.details() == ["R2", "R1"]


def test_a_posting_without_its_text_this_update_is_neither_judged_nor_rewritten(repo):
    board = site(wd_posting("R1"), wd_posting("R2", "Lead UX Researcher"))
    root = repo(sources=(INITECH,))
    tick(root, board)  # read, not judged: both wait for triage
    board.workday_down = {"R2"}
    tick(root, board)
    assert plan(root)["queue"]["triage"] == ["initech:R1"]  # never triaged on its title alone

    board.workday_down = set()
    full_tick(root, board)
    brief = (root / "jobs/initech/R1.md").read_bytes()
    criteria = json.loads(Layout(root).criteria.read_text())
    criteria["thresholds"] = {"strong": 90}  # a new tier rewrites the briefs it moves
    Layout(root).criteria.write_text(json.dumps(criteria))
    board.workday_down = {"R1"}
    full_tick(root, board)
    assert positions(root)["initech:R1"]["tier"] == "good"
    assert (root / "jobs/initech/R1.md").read_bytes() == brief  # kept, not replaced by an empty posting


def test_searches_default_to_the_wanted_roles_and_stage_0_saves_the_text_request(repo):
    board = site(wd_posting("R1"), wd_posting("R2", places=("Germany, Munich",)), wd_posting("R3", "Design Researcher"))
    root = repo(sources=(INITECH,), criteria={"locations": {"regions": ["US"]}})
    full_tick(root, board)
    assert ("search", "UX Researcher") in board.requests and board.details() == ["R1"]  # Munich is out on the listing
    criteria = json.loads(Layout(root).criteria.read_text())
    criteria["targets"]["roles"] = ["Design Researcher"]
    Layout(root).criteria.write_text(json.dumps(criteria))
    full_tick(root, board)
    assert ("search", "Design Researcher") in board.requests
    assert rows(root, "initech")["R1"].miss == 0  # a new search holds closures for one update
    full_tick(root, board)
    assert rows(root, "initech")["R1"].miss == 1  # the same search again: now it counts
    sources = [dict(INITECH, categories=["Research"])]
    Layout(root).sources.write_text(json.dumps(sources))
    full_tick(root, board)
    assert rows(root, "initech")["R1"].miss == 1  # new categories are a new search too


def test_a_posting_whose_own_record_puts_every_place_outside_the_regions_is_parked(repo):
    board = site(
        wd_posting("R1", places=("Germany, Berlin", "France, Paris")),  # listed as "2 Locations"
        wd_posting("R2", places=("Springfield Campus",), country="United Kingdom"),  # a campus, no country
        wd_posting("R3", places=("Germany, Berlin", "US, CA, San Jose")),
        wd_posting("R4", places=("Springfield Campus",)),  # nowhere the engine can read
    )
    root = repo(sources=(INITECH,), criteria={"locations": {"regions": ["US"]}})
    summary = tick(root, board)
    assert {jid: (row.status, row.note) for jid, row in rows(root, "initech").items()} == {
        "R1": ("S", "place:DE,FR"),
        "R2": ("S", "place:GB"),
        "R3": ("P", "-"),
        "R4": ("P", "-"),
    }
    assert summary["outside_regions"] == 2 and plan(root)["queue"]["triage"] == ["initech:R3", "initech:R4"]
    board.details()
    tick(root, board)
    assert board.details() == ["R4", "R3"]  # the parked ones need no text
    info = {"location": "Springfield Campus", "country": {"descriptor": "United Kingdom"}, "additionalLocations": "x"}
    assert workday._own_place(info) == "Springfield Campus, United Kingdom"  # a malformed list of others is ignored


def test_categories_narrow_a_broad_search_and_a_shared_host_builds_its_own_links():
    board = site(
        wd_posting("R1"),
        wd_posting("R2", "UX Researcher, Hardware", category="Engineering"),
    )
    narrowed = S.fetch(client(board), dict(SEARCHED, categories=["research"]), lambda job: S.NEW)  # any case
    assert [job.source_id for job in narrowed] == ["R1"]
    with pytest.raises(SourceError, match="no job category 'Design'"):
        S.fetch(client(board), dict(SEARCHED, categories=["Design"]))
    shared = dict(SEARCHED, host="wd1.myworkdaysite.com", tenant="initech")
    job = S.fetch(client(board), shared, lambda job: S.NEW)[0]
    assert job.url.startswith("https://wd1.myworkdaysite.com/recruiting/initech/External/job/")


def test_job_categories_are_found_where_sites_file_them():
    group = {
        "facetParameter": "jobFamilyGroupGroup",
        "values": [{"facetParameter": "jobFamily", "values": [{"descriptor": "Research", "id": "f1", "count": 4}]}],
    }
    flat = {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Design", "id": "g1", "count": 2}]}
    found = workday.categories({"facets": [group, flat, {"facetParameter": "timeType", "values": []}]})
    assert found == {"research": ("jobFamily", "f1", 4, "Research"), "design": ("jobFamilyGroup", "g1", 2, "Design")}
    assert workday.categories({"total": 3, "jobPostings": []}) == {}  # a site with none
