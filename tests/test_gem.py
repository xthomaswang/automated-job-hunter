"""Gem job boards: the public Job Board API, one request a board with every posting and its text."""

import copy
import json

import pytest
from ajh import registry
from ajh import sources as S
from ajh.cli import main
from ajh.http import Client, HttpError
from ajh.layout import Layout
from ajh.model import SourceError
from ajh.sources import lists as L
from ajh.sources.ats import feed_url, parse_feed
from helpers import NOW, Boards, full_tick, plan, prose, rows, tick

ACME = {"id": "acme", "company": "Acme", "ats": "gem", "slug": "acme"}
UUID = "3f1c2b7a-8d4e-4f0a-9b6c-2e5d7a1c9f30"  # an id may be a UUID-like token as well as digits
IDS = ["5550001", UUID, "5550003", "5550004", "5550005"]
SAMPLE = ["UX Researcher", "Design Researcher", "Research Operations Lead"]
JOB_LIST = {"id": "joblist", "ats": "github-list", "repo": "someone/job-list", "path": "jobs.json"}


def text(jid):
    return prose(sum(map(ord, jid)))


def post(jid, title="UX Researcher", place="New York, NY", workplace="in_office", offices=(), kind="full_time",
         published="2026-10-09T15:00:00.000Z", board="acme"):  # fmt: skip
    """A posting as Gem's Job Board API lists it, with every field the live check saw."""
    return {
        "id": jid,
        "title": title,
        "absolute_url": f"https://jobs.gem.com/{board}/{jid}",
        "location": {"name": place},
        "location_type": workplace,
        "offices": [{"name": f"Acme {name.split(',')[0]}", "location": {"name": name}} for name in offices],
        "departments": [{"name": "Design"}, {"name": "Research"}],
        "employment_type": kind,
        "created_at": "2026-10-01T12:00:00.000Z",
        "first_published_at": published,
        "updated_at": "2026-10-12T08:30:00.000Z",
        "requisition_id": "REQ-1001",
        "internal_job_id": f"internal-{jid[:6]}",
        "content": f"<h2>The role</h2><p>{text(jid)}</p><ul><li>Three years of research.</li></ul>",
        "content_plain": "Not read: the text comes from the HTML.",
    }


def board():
    """The fake Gem API, serving one board of five postings."""
    found = Boards()
    found.gem = {
        "acme": [
            post("5550001", offices=("New York, NY", "Austin, TX")),
            post(UUID, "Design Researcher", place="London, United Kingdom", workplace="remote", published=None),
            post("5550003", "Research Operations Lead", place="Toronto, ON", workplace="hybrid", kind="contract"),
            post("5550004", "Research Program Manager", place="Remote - US", workplace="remote"),
            post("5550005", "UX Research Intern", kind="internship"),
        ]
    }
    return found


def client(found):
    return Client(found.transport, sleep=lambda seconds: None, retries=0)


def listed(url, title="UX Researcher", company="Acme"):
    """A job list's row, which only points at the posting."""
    return {"company": company, "title": title, "location": "Remote, US", "url": url}


def test_a_board_is_read_whole_in_one_request_with_each_postings_facts_and_text():
    found = board()
    jobs = {job.source_id: job for job in S.fetch(client(found), ACME)}
    assert found.requests == [("gem", "acme")] and list(jobs) == IDS
    one = jobs["5550001"]
    assert (one.key, one.source, one.company, one.title) == ("acme:5550001", "acme", "Acme", "UX Researcher")
    assert one.url == "https://jobs.gem.com/acme/5550001" and one.apply_url is None and one.source_kind == "official"
    assert one.description == f"The role\n{text('5550001')}\n- Three years of research."  # from the HTML
    assert (one.location, one.department, one.posted_at, one.employment_type) == (
        "New York, NY / Austin, TX",  # the office in New York adds nothing, the one in Austin does
        "Design / Research",
        "2026-10-09T15:00:00.000Z",
        "full_time",
    )
    remote = jobs[UUID]
    assert (remote.location, remote.posted_at) == ("Remote, London, United Kingdom", "2026-10-01T12:00:00.000Z")
    hybrid = jobs["5550003"]
    assert (hybrid.location, hybrid.employment_type) == ("Hybrid, Toronto, ON", "contract")
    assert jobs["5550004"].location == "Remote - US"  # its place says so already


def test_a_postings_place_reads_the_same_whatever_order_its_offices_come_in():
    first = post("5550001", offices=("Austin, TX", "new york, ny", "Boston,  MA"))
    turned = dict(first, offices=list(reversed(first["offices"])))
    assert [parse_feed(ACME, [row])[0].location for row in (first, turned)] == [
        "New York, NY / Austin, TX / Boston, MA"
    ] * 2
    # an office's place, never its name, which is a label such as "HQ"
    offices = [{"name": "HQ"}, {"name": "Acme Austin", "location": {"name": "Austin, TX"}}]
    assert parse_feed(ACME, [dict(first, location=None, offices=offices)])[0].location == "Austin, TX"
    nowhere = dict(first, location={}, offices=[])
    assert parse_feed(ACME, [nowhere])[0].location == "unknown"
    assert parse_feed(ACME, [dict(nowhere, location_type="remote")])[0].location == "Remote"


FEED = [post("5550001"), post(UUID, "Design Researcher")]


@pytest.mark.parametrize(
    "changes",
    [
        {"id": None},
        {"title": None},
        {"title": "  "},
        {"absolute_url": None},
        {"absolute_url": "http://jobs.gem.com/acme/5550001"},
        {"id": 5550001},  # Gem's ids are strings
        {"id": ""},
        {"id": "5550001/../x"},  # not safe in a path
        {"id": "5550001 "},
        {"id": "5550%2F1"},
        {"content": ""},
        {"content": "<script>x</script>"},
    ],
)
def test_one_incomplete_posting_aborts_the_snapshot_instead_of_dropping_the_job(changes):
    assert [job.source_id for job in parse_feed(ACME, FEED)] == ["5550001", UUID]  # the board as it is reads whole
    broken = copy.deepcopy(FEED)
    for field, value in changes.items():
        if value is None:
            broken[0].pop(field)
        else:
            broken[0][field] = value
    with pytest.raises(SourceError):
        parse_feed(ACME, broken)


@pytest.mark.parametrize("payload", [{"job_posts": FEED}, {"error": "Not found"}, None, ["not an object"], FEED * 2])
def test_a_reply_that_is_not_one_whole_board_is_refused(payload):
    assert len(parse_feed(ACME, FEED)) == 2
    with pytest.raises(SourceError):
        parse_feed(ACME, payload)


@pytest.mark.parametrize("name", ["", "Acme", "acme_ai", "acme.com", "acme/../x", "acme?x=1", "a b", "acme%2Fx", None])
def test_a_board_name_is_lowercase_letters_digits_and_hyphens(name):
    assert feed_url(dict(ACME, slug="acme-ai-2")) == "https://api.gem.com/job_board/v0/acme-ai-2/job_posts/"
    with pytest.raises(SourceError, match="Invalid board slug"):
        feed_url(dict(ACME, slug=name))


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.gem.com/acme",  # the board's page, an app shell with no postings in it
        "https://jobs.gem.com/acme/5550001?utm_source=list",  # one posting's link
        "https://api.gem.com/job_board/v0/acme/job_posts/",
        f"https://api.gem.com/job_board/v0/acme/job_posts/{UUID}",
        "https://api.gem.com/job_board/v0/acme/departments/",
    ],
)
def test_probe_reads_a_board_from_its_page_a_postings_link_or_its_api(url):
    found = board()
    result = registry.probe(f"{url} Acme", client(found))
    assert result["matches"] == [{"system": "Gem", "ats": "gem", "slug": "acme", "supported": True, "postings": 5,
                                  "sample": SAMPLE, "add": "gem:acme:Acme"}]  # fmt: skip
    assert result["unsupported"] == [] and found.requests == [("gem", "acme")]  # the listing, and not the page


def test_probe_follows_a_careers_page_to_its_gem_board_and_says_what_is_wrong_with_a_bare_link():
    found = board()
    page = '<a href="https://jobs.gem.com/acme">Open roles</a> <a href="https://www.gem.com/">Powered by Gem</a>'

    def transport(method, url, headers, body, timeout):
        if url == "https://www.acme.example/careers":
            return 200, {}, page.encode()
        return found.transport(method, url, headers, body, timeout)

    web = Client(transport, retries=0)
    result = registry.probe("https://www.acme.example/careers Acme", web)
    assert [(m["add"], m["postings"]) for m in result["matches"]] == [("gem:acme:Acme", 5)]
    bare = registry.probe("https://jobs.gem.com/ Acme", web)["matches"]
    assert len(bare) == 1 and "Invalid board slug" in bare[0]["error"] and "add" not in bare[0]


def test_a_name_probe_tries_gem_as_it_tries_the_other_boards():
    found = board()
    result = registry.probe("Acme", client(found))
    # medium: Gem's postings name no company, so only the board's address matches the name
    assert result["matches"] == [{"ats": "gem", "slug": "acme", "postings": 5, "sample": SAMPLE,
                                  "add": "gem:acme:Acme", "confidence": "medium"}]  # fmt: skip
    assert found.requests.count(("gem", "acme")) == 1 and "next" not in result
    assert "Gem" in registry.probe("Nobody", client(found))["next"]  # among the systems a name probe tries


def test_a_board_is_added_by_its_name_and_hosts_name_its_api(repo):
    root = repo(sources=())
    assert registry.add(root, ["gem:acme:Acme"]) == {"added": ["acme"], "unknown": [], "configured": 1}
    assert json.loads((root / "config" / "sources.json").read_text()) == [ACME]
    assert registry.add(root, ["gem:acme:Acme"])["added"] == []  # the same board again
    assert registry.hosts(root) == {"hosts": ["api.gem.com"]}
    for bad in ("gem:Acme:Acme", "gem:acme.com:Acme", "gem:../x:Acme", "gem:acme_ai:Acme"):
        with pytest.raises(SourceError):
            registry.add(root, [bad])
    assert main(["--repo", str(root), "sources", "add", "gem:a b:Bad"]) == 2
    repo(sources=(JOB_LIST,))
    assert "api.gem.com" in registry.hosts(root)["hosts"]  # a list can lead to a Gem board


@pytest.mark.parametrize(
    "url, source_id",
    [
        ("https://jobs.gem.com/acme/5550001?utm_source=list", "gem~acme~5550001"),
        (f"https://jobs.gem.com/acme/{UUID}/", f"gem~acme~{UUID}"),
        ("https://jobs.gem.com/acme-ai/5550001", "gem~acme-ai~5550001"),
    ],
)
def test_a_link_to_one_gem_posting_names_where_it_lives(url, source_id):
    found = L.link(url)
    assert found.source_id == source_id and found.url == f"https://jobs.gem.com/{found.board}/{found.id}"
    assert L.link(found.url).source_id == source_id  # the posting's own page names the same place
    assert L.board_identity(ACME, "5550001") == L.identity("gem~acme~5550001") == ("gem", "acme", "5550001")


@pytest.mark.parametrize(
    "url", ["https://jobs.gem.com/acme", "https://jobs.gem.com/Acme/5550001", "https://jobs.gem.com/acme/5550%2F1"]
)
def test_any_other_link_to_gem_is_skipped_as_not_one_posting(url):
    assert L.link(url) is None and L._why(url) == "not one posting"


def test_a_lists_link_to_a_watched_gem_boards_posting_is_that_boards_posting(repo):
    root = repo(sources=(ACME, JOB_LIST))
    found = board()
    found.lists["someone/job-list/main/jobs.json"] = json.dumps(
        [listed("https://jobs.gem.com/acme/5550001?utm_source=list"),
         listed(f"https://jobs.gem.com/acme/{UUID}", "Design Researcher")]
    )  # fmt: skip
    tick(root, found)
    assert sorted(rows(root, "acme")) == sorted(IDS)
    assert {jid: (row.status, row.note) for jid, row in rows(root, "joblist").items()} == {
        "gem~acme~5550001": ("V", "acme:5550001"),
        f"gem~acme~{UUID}": ("V", f"acme:{UUID}"),
    }
    assert sorted(plan(root)["queue"]["triage"]) == sorted(f"acme:{jid}" for jid in IDS)  # judged once, as the board's
    assert found.requests.count(("gem", "acme")) == 2  # the watched board, and the list's postings once between them


def test_a_lists_gem_posting_brings_its_text_date_and_place_from_its_board_read_once_an_update(repo):
    root = repo(sources=(JOB_LIST,), criteria={"locations": {"regions": ["US"]}})
    found = board()
    found.gem["globex"] = [post("7770001", board="globex"),
                           post("7770002", "Data Analyst", place="Berlin, Germany", board="globex")]  # fmt: skip
    found.lists["someone/job-list/main/jobs.json"] = json.dumps(
        [listed("https://jobs.gem.com/globex/7770001", company="Globex"),
         listed("https://jobs.gem.com/globex/7770002", "Data Analyst", "Globex"),  # the list says US
         listed("https://jobs.gem.com/globex/7770009", company="Globex")]  # no longer on the board
    )  # fmt: skip
    full_tick(root, found)
    assert found.requests.count(("gem", "globex")) == 1
    table = rows(root, "joblist")
    assert sorted(table) == ["gem~globex~7770001", "gem~globex~7770002"]
    assert (table["gem~globex~7770002"].status, table["gem~globex~7770002"].note) == ("S", "place:DE")
    records = {r["key"]: r for r in map(json.loads, Layout(root).positions.read_text().splitlines())}
    record = records["joblist:gem~globex~7770001"]
    assert (record["source_kind"], record["company"], record["url"], record["posted"]) == (
        "official",
        "Globex",
        "https://jobs.gem.com/globex/7770001",
        "2026-10-09",
    )
    brief = (root / record["brief"]).read_text()
    assert text("7770001")[:40] in brief and "Employment: full_time" in brief


def test_a_tick_reads_a_board_end_to_end_and_a_failed_read_closes_nothing(repo):
    root = repo(sources=(ACME,), criteria={"employment": ["full_time"], "locations": {"regions": ["US"]}})
    found = board()
    summary = tick(root, found)
    assert summary["dropped"] == {"location": 1, "employment": 2}  # London; the contract and the internship
    assert sorted(rows(root, "acme")) == ["5550001", "5550004"]
    full_tick(root, found)
    records = {r["key"]: r for r in map(json.loads, Layout(root).positions.read_text().splitlines())}
    assert sorted(records) == ["acme:5550001", "acme:5550004"]
    one = records["acme:5550001"]
    assert (one["company"], one["location"], one["employment"], one["url"], one["posted"]) == (
        "Acme",
        "New York, NY / Austin, TX",
        "full_time",
        "https://jobs.gem.com/acme/5550001",
        "2026-10-09",
    )
    assert text("5550001")[:40] in (root / one["brief"]).read_text()
    before = {jid: row.status for jid, row in rows(root, "acme").items()}

    found.status["gem:acme"] = 503
    with pytest.raises(HttpError):
        S.fetch(client(found), ACME)
    failed = tick(root, found, now=NOW.replace(hour=16))
    assert failed["sources"]["failed"] == 1 and failed["closed"] == 0
    assert {jid: row.status for jid, row in rows(root, "acme").items()} == before
    assert all(row.miss == 0 for row in rows(root, "acme").values())
