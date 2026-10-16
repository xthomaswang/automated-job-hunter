"""SmartRecruiters: the documented Posting API, read through searches with text fetched on demand."""

import json

import pytest
from ajh import registry, scaffold
from ajh import sources as S
from ajh.http import Client
from ajh.model import SourceError
from ajh.sources import smartrecruiters
from helpers import HOOLI, Boards, full_tick, rows, sr_posting

SEARCHED = dict(HOOLI, queries=["UX Researcher"])


def client(board):
    return Client(board.transport, sleep=lambda seconds: None, retries=0)


def company(*postings):
    board = Boards()
    board.hooli = {posting["row"]["id"]: posting for posting in postings}
    return board


def test_a_company_is_read_through_its_searches_with_its_facts_from_the_listing():
    board = company(
        sr_posting("1001"),
        sr_posting("1002", full="Lisbon, , Portugal", remote=True, kind="Part-time"),
        sr_posting("1003", "Account Executive"),  # not matched by the search
    )
    counted = {job.source_id: job for job in S.fetch(client(board), SEARCHED)}  # no rank: no text
    assert sorted(counted) == ["1001", "1002"] and board.details() == []
    one = counted["1001"]
    assert (one.location, one.employment_type, one.posted_at, one.department) == (
        "San Jose, CA, United States",  # the country named: CA and IN are also US states
        "Full-time",  # from the listing, so stage 0 can use it on every update
        "2026-10-09T08:00:00.000Z",
        "Design",
    )
    assert one.url == "https://jobs.smartrecruiters.com/Hooli/1001" and one.detail_skipped
    assert counted["1002"].location == "Remote, Lisbon, Portugal"

    jobs = {job.source_id: job for job in S.fetch(client(board), SEARCHED, lambda job: S.NEW)}
    assert board.details() == ["1002", "1001"]  # newest first
    detailed = jobs["1001"]
    assert detailed.url == "https://jobs.smartrecruiters.com/Hooli/1001-ux-researcher"
    assert detailed.apply_url == "https://jobs.smartrecruiters.com/Hooli/1001-ux-researcher?oga=true"
    assert "Qualifications" in detailed.description and "Three years of research." in detailed.description
    assert "Videos" not in detailed.description and not detailed.detail_skipped


def test_anything_short_of_a_complete_listing_fails_the_source(monkeypatch):
    board = company(*(sr_posting(str(2000 + n)) for n in range(150)))
    assert len(S.fetch(client(board), SEARCHED)) == 150  # two pages of 100

    def moving(method, url, headers, body, timeout):
        status, sent, data = board.transport(method, url, headers, body, timeout)
        page = json.loads(data)
        if "offset=100" in url:
            page["totalFound"] += 1  # the listing grew while it was read
        return status, sent, json.dumps(page).encode()

    with pytest.raises(SourceError, match="changed while it was read"):
        S.fetch(Client(moving, sleep=lambda s: None, retries=0), SEARCHED)
    for bad, problem in ((dict(SEARCHED, slug="../x"), "identifier"), (dict(HOOLI), "searches")):
        with pytest.raises(SourceError, match=problem):
            S.fetch(client(board), bad)
    monkeypatch.setattr(smartrecruiters, "MAX_PER_QUERY", 100)
    with pytest.raises(SourceError, match="more specific queries"):
        S.fetch(client(board), SEARCHED)


def test_stage_0_reads_the_listing_so_a_dropped_posting_costs_no_text(repo):
    board = company(sr_posting("1001"), sr_posting("1002", kind="Part-time"))
    root = repo(sources=(HOOLI,), criteria={"employment": ["full_time"]})
    full_tick(root, board)
    assert board.details() == ["1001"] and sorted(rows(root, "hooli")) == ["1001"]
    full_tick(root, board)  # listed: text only for its brief; the part-time one is still never fetched
    assert board.details() == ["1001"]


def test_probe_finds_a_company_by_name_or_url_and_adds_it(tmp_path):
    board = company(sr_posting("1001"), sr_posting("1002", "Analyst"))
    web = client(board)
    by_name = registry.probe("Hooli", web)
    assert by_name["matches"] == [
        {
            "ats": "smartrecruiters",
            "slug": "Hooli",  # the API's own spelling, though the probe asked for "hooli"
            "postings": 2,
            "sample": ["UX Researcher", "Analyst"],
            "add": "smartrecruiters:Hooli:Hooli",
            "confidence": "low",  # two postings are too few, though its postings name the company asked for
            "warning": "Only 2 postings, and a board this small is often another company's with the same name.",
        }
    ]
    assert registry.probe("Initrode", web)["matches"] == []  # an unknown company has no postings, not an error
    by_url = registry.probe("https://jobs.smartrecruiters.com/Hooli/1001-ux-researcher Hooli", web)
    assert by_url["matches"][0]["add"] == "smartrecruiters:Hooli:Hooli" and by_url["unsupported"] == []

    scaffold.init(tmp_path / "feed")
    root = tmp_path / "feed"
    assert registry.add(root, ["smartrecruiters:Hooli:Hooli"])["added"] == ["hooli"]
    entry = json.loads((root / "config/sources.json").read_text())[0]
    assert entry == {"id": "hooli", "company": "Hooli", "ats": "smartrecruiters", "slug": "Hooli"}
    assert registry.hosts(root) == {"hosts": ["api.smartrecruiters.com"]}
    with pytest.raises(SourceError):
        registry.add(root, ["smartrecruiters:a b:Bad"])
