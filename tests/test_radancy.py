"""Radancy TalentBrew career sites: the sitemap is the listing, and each posting's text comes from its page."""

import json
import re
import urllib.parse
from pathlib import Path

import pytest
from ajh import collect, registry, scaffold
from ajh import sources as S
from ajh.criteria import Places, Stage0
from ajh.http import Client, HttpError
from ajh.model import SourceError
from ajh.state import tag
from helpers import rows, tick

FIXTURES = Path(__file__).parent / "fixtures" / "radancy"
HOST = "careers.acme.com"
ACME = {"id": "acme", "company": "Acme", "ats": "radancy", "host": HOST}
POSTING = re.compile(r"/job/[^/]+/[^/]+/\d+/(\d+)/?\Z")
IDS = ["55500011122", "55500011123", "55500011124", "55500011125", "55500011126"]
SPRINGFIELD = "/en/job/springfield/retail-store-associate/1234/55500011122"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def urlset(*entries):
    """A sitemap of these entries: a path on the site, a whole address, or None for an entry without one."""
    locs = (f"https://{HOST}{e}" if isinstance(e, str) and e.startswith("/") else e for e in entries)
    urls = "".join(
        f"<url><loc>{loc}</loc></url>" if loc else "<url><lastmod>2026-10-15</lastmod></url>" for loc in locs
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


class Site:
    """A fake TalentBrew career site serving the fixture files: robots.txt, its sitemap (or an index of
    sitemaps), its home page, and a page per posting, found by the posting's id.

    Tests change `files` (by path, or `job-<id>` for a posting's page), `status` (a path's HTTP status)
    and `landed` (where a path's reply came from once redirects were followed).
    """

    def __init__(self, sitemap="sitemap.xml"):
        self.files = {"/robots.txt": fixture("robots.txt"), "/sitemap.xml": fixture(sitemap), "/": fixture("home.html")}
        self.files.update({f"/{name}": fixture(name) for name in ("sitemap-1.xml", "sitemap-2.xml")})
        self.files.update({page.stem: page.read_text(encoding="utf-8") for page in FIXTURES.glob("job-*.html")})
        self.status, self.landed, self.requests = {}, {}, []

    def transport(self, method, url, headers, body, timeout):
        parts = urllib.parse.urlsplit(url)
        assert (method, parts.scheme, parts.hostname) == ("GET", "https", HOST)  # only reading, only this site
        self.requests.append(parts.path)
        if parts.path in self.status:
            return self.status[parts.path], {}, b"Unavailable"
        found = POSTING.search(parts.path)
        text = self.files.get(f"job-{found.group(1)}" if found else parts.path)
        if text is None:
            return 404, {}, b"Not found"
        return 200, {}, text.encode("utf-8"), self.landed.get(parts.path, url)

    def pages(self):
        """The ids of the postings whose page was asked for since the last call."""
        asked = [POSTING.search(path).group(1) for path in self.requests if POSTING.search(path)]
        self.requests = [path for path in self.requests if not POSTING.search(path)]
        return asked


def client(site):
    return Client(site.transport, sleep=lambda seconds: None, retries=0)


def by_id(jobs):
    return {job.source_id: job for job in jobs}


def test_the_sitemap_is_the_listing_with_title_and_place_from_the_links():
    site = Site()
    web = client(site)
    jobs = by_id(S.fetch(web, ACME))  # no rank: counting only, so no posting page is read
    assert site.requests == ["/robots.txt", "/sitemap.xml"]  # no home, category, place or employment page
    assert list(jobs) == IDS
    assert [(job.title, job.location) for job in jobs.values()] == [
        ("Retail Store Associate", "Springfield"),
        ("Assistant Store Manager", "Shelbyville"),  # a link without a language segment
        ("销售代表", "上海"),  # percent-encoded UTF-8 slugs
        ("Ux Researcher", "Capital City"),
        ("Visual Merchandiser", "London"),
    ]
    one = jobs["55500011122"]
    assert (one.key, one.company, one.url) == ("acme:55500011122", "Acme", f"https://{HOST}{SPRINGFIELD}")
    assert (one.posted_at, one.employment_type, one.description, one.own_location) == (None, "unknown", "", "")
    assert one.detail_skipped  # the sitemap's date is when it was made, so it is not the posting's
    assert jobs["55500011125"].url == f"https://{HOST}/en/job/capital-city/ux-researcher/1234/55500011125"  # no query
    assert jobs["55500011124"].url.endswith(
        "/en/job/%E4%B8%8A%E6%B5%B7/%E9%94%80%E5%94%AE%E4%BB%A3%E8%A1%A8/1234/55500011124"
    )
    assert web.host_delay[HOST] == 1.0  # one request a second
    assert "radancy" in S.KINDS and "radancy" in S.DETAILED and "radancy" not in S.QUERY_SCOPED
    assert (
        S.scoped(ACME, {"targets": {"roles": ["UX Researcher"], "adjacent": []}}) == ACME
    )  # no search: the whole site


def test_a_posting_page_gives_its_text_date_and_own_place_with_the_employment_type_in_the_text():
    site = Site()
    jobs = by_id(S.fetch(client(site), ACME, lambda job: S.NEW))
    assert site.pages() == IDS[::-1]  # newest first
    assert not any(job.detail_skipped for job in jobs.values())
    springfield = jobs["55500011122"]
    assert springfield.description == (
        "Location: Springfield, IL, USA\nEmployment: Part-time\n\nAbout the role\n"
        "As a Retail Store Associate at Acme you help customers find what they need, keep the floor stocked and run "
        "the register.\nWhat you will do\n- Greet and help customers\n- Restock shelves and keep displays tidy\n"
        "- Handle payments and returns\nWhat you bring\n- One year of retail or customer service experience\n"
        "- Availability on weekends"
    )
    assert (springfield.posted_at, springfield.own_location) == ("2026-10-01", "Springfield, IL, USA")
    assert (springfield.title, springfield.location) == ("Retail Store Associate", "Springfield")
    assert springfield.url == f"https://{HOST}{SPRINGFIELD}"
    shelbyville = jobs["55500011123"]  # a @graph, a country spelled out, and HTML written with entities
    assert shelbyville.description == (
        "Location: Shelbyville, Indiana, United States\nEmployment: Full-time\n\n"
        "Lead a team of twelve associates and keep the store running smoothly.\n"
        "- Three years of retail experience, one of them leading a team\n- Open to early and late shifts"
    )
    assert shelbyville.title == "Assistant Store Manager"  # the page says "Assistant Manager, Store Operations"
    chinese = jobs["55500011124"]  # a list of JSON-LD objects
    assert (chinese.title, chinese.own_location, chinese.posted_at) == ("销售代表", "上海, 上海, CN", "2026-10-10")
    assert (
        chinese.description
        == "Location: 上海, 上海, CN\nEmployment: FULL_TIME\n\n负责门店销售与客户服务。\n- 两年以上零售经验"
    )
    two = jobs["55500011125"]  # two places, two employment types, and raw line breaks inside the JSON
    assert two.title == "UX Researcher"  # the slug's words, with the page's own capitals
    assert two.own_location == "Capital City, OR, US / Springfield, IL, United States"
    assert two.description.startswith(
        "Locations: Capital City, OR, US / Springfield, IL, United States\nEmployment: Full-time, Part-time\n\n"
        "Plan and run interviews and usability studies for Acme's store apps.\n- Three years of user research"
    )
    london = jobs["55500011126"]
    assert london.description == "Location: London, England, GB\n\nDesign the window displays of our flagship store."
    # The type is never `employment_type`: the sitemap has none, and stage 0 must see the same facts every update.
    assert {job.employment_type for job in jobs.values()} == {"unknown"}


def test_the_change_fingerprint_and_stage_0_see_the_same_posting_with_or_without_its_text():
    listed = by_id(S.fetch(client(Site()), ACME))
    detailed = by_id(S.fetch(client(Site()), ACME, lambda job: S.NEW))
    assert (listed["55500011125"].title, detailed["55500011125"].title) == ("Ux Researcher", "UX Researcher")
    assert listed["55500011123"].title == detailed["55500011123"].title == "Assistant Store Manager"
    criteria = {"companies": {"exclude": []}, "employment": ["full_time"], "locations": {"regions": ["US"]}}
    stage0 = Stage0(criteria, Places.bundled())
    for jid, job in listed.items():
        assert collect.fingerprints(job) == collect.fingerprints(detailed[jid])
        assert stage0.check(job) == stage0.check(detailed[jid])  # the part-time posting too: its type is in its text
    assert stage0.check(listed["55500011126"]) == "location"  # London, on its link alone


def test_stage_0_reads_the_links_and_a_posting_its_own_record_puts_elsewhere_is_parked(repo):
    site = Site()
    root = repo(sources=(ACME,), criteria={"locations": {"regions": ["US"]}, "employment": ["full_time"]})
    summary = tick(root, site)
    assert site.pages() == ["55500011125", "55500011124", "55500011123", "55500011122"]  # London's page is never read
    assert (summary["new"], summary["outside_regions"], summary["dropped"]) == (3, 1, {"location": 1})
    state = rows(root, "acme")
    assert sorted(state) == IDS[:4] and state["55500011122"].status == "P"  # part-time: its text says so, for triage
    assert state["55500011124"].status == "S" and tag(state["55500011124"], "place") == "CN"
    tick(root, site)  # the ones waiting for triage need their text again; the parked one and London do not
    assert site.pages() == ["55500011125", "55500011123", "55500011122"]


@pytest.mark.parametrize(
    "robots, asked",
    [
        ("User-agent: *\nDisallow: /sitemap.xml\n", ["/robots.txt"]),
        ("User-agent: *\nDisallow: /en/job/\n", ["/robots.txt", "/sitemap.xml"]),
        ("User-agent: *\nDisallow: /job/\n", ["/robots.txt", "/sitemap.xml"]),  # the link without a language
        ("User-agent: automated-job-hunter\nDisallow: /\n\nUser-agent: *\nDisallow: /search-jobs\n", ["/robots.txt"]),
        ("User-agent: *\nAllow: /\nDisallow: /en/job/\n", ["/robots.txt", "/sitemap.xml"]),  # the longest rule wins
        ("User-agent: *\nDisallow: /*/job/\n", ["/robots.txt", "/sitemap.xml"]),  # a wildcard
        ("User-agent: *\nDisallow: /search-jobs\n\nUser-agent: *\nDisallow: /job/\n", ["/robots.txt", "/sitemap.xml"]),
    ],
)
def test_whatever_robots_txt_disallows_fails_the_source_before_it_is_asked(robots, asked):
    site = Site()
    site.files["/robots.txt"] = robots
    with pytest.raises(SourceError, match=r"robots\.txt on careers\.acme\.com disallows"):
        S.fetch(client(site), ACME, lambda job: S.NEW)
    assert site.requests == asked  # no posting page, and nothing after what was refused


@pytest.mark.parametrize(
    "robots",
    [
        "User-agent: *\nDisallow: /?\n",  # the root with a query string, not the whole site
        "User-agent: *\nDisallow: /\n\nUser-agent: automated-job-hunter\nDisallow: /search-jobs\n",  # its own group
        "User-agent: otherbot\nDisallow: /\n\nUser-agent: *\nDisallow: /search-jobs\n",  # another crawler's group
        "User-agent: *\nDisallow: /\nAllow: /sitemap.xml\nAllow: /*/job/\nAllow: /job/\n",  # the longest rule wins
        "User-agent: *\nDisallow:\n",  # an empty Disallow matches nothing
    ],
)
def test_robots_txt_that_allows_the_sitemap_and_the_postings_lets_the_site_be_read(robots):
    site = Site()
    site.files["/robots.txt"] = robots
    assert len(S.fetch(client(site), ACME)) == 5


def test_robots_txt_that_cannot_be_read_now_reads_nothing_and_none_at_all_disallows_nothing():
    for status, problem in ((403, SourceError), (401, SourceError), (500, HttpError), (429, HttpError)):
        site = Site()
        site.status["/robots.txt"] = status
        with pytest.raises(problem):
            S.fetch(client(site), ACME)
        assert site.requests == ["/robots.txt"]
    site = Site()
    del site.files["/robots.txt"]  # 404: there is none, so nothing is disallowed (RFC 9309, 2.3.1.3)
    assert len(S.fetch(client(site), ACME)) == 5


def test_a_sitemap_index_is_read_sitemap_by_sitemap_on_the_sites_own_host():
    site = Site("sitemap-index.xml")
    assert sorted(by_id(S.fetch(client(site), ACME))) == IDS[:4]
    assert site.requests == ["/robots.txt", "/sitemap.xml", "/sitemap-1.xml", "/sitemap-2.xml"]  # not www.acme.com's

    for change, problem in (
        (lambda s: s.status.update({"/sitemap-2.xml": 500}), HttpError),
        (lambda s: s.files.pop("/sitemap-2.xml"), HttpError),
        (lambda s: s.files.update({"/sitemap-2.xml": fixture("home.html")}), SourceError),
    ):
        broken = Site("sitemap-index.xml")
        change(broken)
        with pytest.raises(problem):  # one sitemap short is not the board: nothing is returned
            S.fetch(client(broken), ACME)
    nested = Site("sitemap-index.xml")
    nested.files["/sitemap-1.xml"] = fixture("sitemap-index.xml")
    with pytest.raises(SourceError, match="index inside an index"):
        S.fetch(client(nested), ACME)
    refused = Site("sitemap-index.xml")
    refused.files["/robots.txt"] = "User-agent: *\nDisallow: /sitemap-2\n"
    with pytest.raises(SourceError, match=r"disallows its sitemap https://careers\.acme\.com/sitemap-2\.xml"):
        S.fetch(client(refused), ACME)
    assert "/sitemap-2.xml" not in refused.requests
    elsewhere = Site()
    elsewhere.files["/sitemap.xml"] = fixture("sitemap-index.xml").replace("careers.acme.com/", "jobs.acme.com/")
    with pytest.raises(SourceError, match=r"names no sitemap on careers\.acme\.com"):
        S.fetch(client(elsewhere), ACME)


@pytest.mark.parametrize(
    "body, problem",
    [
        (None, "HTTP 404"),  # no sitemap at all
        (fixture("home.html"), "not a sitemap: it declares a document type"),  # a page instead of the sitemap
        (
            '<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>&b;</loc></url></urlset>',
            "declares a document type",  # refused before it is parsed
        ),
        ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>x</url>', "not well-formed"),
        ("<urlset><url><loc>https://careers.acme.com/job/a/b/1/2</loc></url></urlset>", "is not a sitemap"),
        (urlset("/", "/en/category/retail-jobs/1234/8899/1", "/en/business/stores"), "names no posting"),
        (urlset(), "names no posting"),
    ],
)
def test_a_sitemap_that_cannot_be_read_as_one_fails_the_source(body, problem):
    site = Site()
    site.files["/sitemap.xml"] = body
    if body is None:
        del site.files["/sitemap.xml"]
    with pytest.raises(HttpError if body is None else SourceError, match=problem):
        S.fetch(client(site), ACME)
    assert site.pages() == []


def test_a_few_malformed_posting_links_are_skipped_and_more_fail_the_source():
    good = [f"/en/job/springfield/retail-store-associate/1234/{55500020000 + n}" for n in range(38)]
    ignored = [
        "https://www.acme.com/en/job/springfield/retail-store-associate/1234/55500029990",  # another host
        "/en/jobs/springfield/1234/55500029991",  # not a posting page
        "/en/job_location/springfield/1234/55500029992",
    ]
    bad = [
        "/en/job/springfield/retail-store-associate/1234/R-100200",  # an id that is not a number
        "/en/job/springfield/1234/55500029993",  # a segment missing
        f"http://{HOST}/en/job/springfield/retail-store-associate/1234/55500029994",  # not https
        None,  # an entry without its address
    ]
    site = Site()
    site.files["/sitemap.xml"] = urlset(*good, *ignored, *bad[:2])  # 2 of 40 posting links: within one in twenty
    assert len(S.fetch(client(site), ACME)) == 38
    site.files["/sitemap.xml"] = urlset(*good, *ignored, *bad[:3])  # 3 of 41
    with pytest.raises(SourceError, match="3 of 41 posting links in a shape this version cannot read"):
        S.fetch(client(site), ACME)
    site.files["/sitemap.xml"] = urlset(*good[:3], bad[3])  # a small site: one stray entry always passes
    assert len(S.fetch(client(site), ACME)) == 3
    site.files["/sitemap.xml"] = urlset(*good[:3], *bad[2:])
    with pytest.raises(SourceError, match="2 of 5 posting links"):
        S.fetch(client(site), ACME)
    other = good[0].replace("/en/job/springfield", "/job/shelbyville")  # one posting under two links
    for order in ((good[0], other), (other, good[0])):
        site.files["/sitemap.xml"] = urlset(*order, good[1])
        jobs = by_id(S.fetch(client(site), ACME))
        assert len(jobs) == 2 and jobs["55500020000"].location == "Springfield"  # the same one, in either order
    for host in ("careers.acme.com/../x", None, "10.0.0.1", "localhost"):
        with pytest.raises(SourceError, match="Radancy needs"):
            S.fetch(client(site), dict(ACME, host=host))


def test_texts_are_fetched_in_rank_order_within_the_budget_and_a_failed_page_waits():
    site = Site()
    ranks = dict(zip(IDS, (S.LISTED, S.NEW, S.PENDING, S.CHANGED, None)))
    jobs = by_id(S.fetch(client(site), dict(ACME, max_details=3), lambda job: ranks[job.source_id]))
    assert site.pages() == ["55500011124", "55500011123", "55500011125"]  # about to be judged, new, changed; no more
    assert sorted(jid for jid, job in jobs.items() if job.detail_skipped) == ["55500011122", "55500011126"]
    assert len(S.fetch(client(site), dict(ACME, max_details=0), lambda job: S.NEW)) == 5 and site.pages() == []

    site.landed[SPRINGFIELD] = f"https://{HOST}/en/search-jobs"  # moved elsewhere
    site.status["/job/shelbyville/assistant-store-manager/1234/55500011123"] = 500
    site.files["job-55500011124"] = fixture("no-longer-available.html").replace("55500011122", "55500011124")
    site.files["job-55500011125"] = fixture("job-55500011126.html")  # another posting's page
    blank = '"description":"<p>Design the window displays of our flagship store.</p>"'
    site.files["job-55500011126"] = fixture("job-55500011126.html").replace(blank, '"description":""')
    assert blank in fixture("job-55500011126.html")
    jobs = by_id(S.fetch(client(site), ACME, lambda job: S.NEW))
    assert sorted(site.pages()) == IDS  # each was asked once; none gave this posting's text, so each waits
    assert all(job.detail_skipped and not job.description for job in jobs.values())
    site.files["job-55500011125"] = fixture("job-55500011125.html").replace("<script type=", "<script data-type=")
    assert by_id(S.fetch(client(site), ACME, lambda job: S.NEW))["55500011125"].detail_skipped  # no JSON-LD


def test_the_probe_knows_a_site_by_its_page_and_the_add_line_writes_it(tmp_path):
    expected = {
        "system": "Radancy TalentBrew",
        "ats": "radancy",
        "host": HOST,
        "supported": True,
        "postings": 5,
        "sample": ["Retail Store Associate", "Assistant Store Manager", "销售代表"],
        "add": "radancy:careers.acme.com:Acme",
    }
    site = Site()
    home = registry.probe("https://careers.acme.com/ Acme", client(site))
    assert home["matches"] == [expected] and home["unsupported"] == []  # not the Workday sign-in the page links to
    assert site.requests == ["/", "/robots.txt", "/sitemap.xml"]  # its page, robots.txt and the sitemap, nothing more
    posting = registry.probe(f"https://{HOST}{SPRINGFIELD} Acme", client(site))
    assert posting["matches"] == [expected]
    site.status["/robots.txt"] = 403
    refused = registry.probe("https://careers.acme.com/ Acme", client(site))["matches"]
    assert len(refused) == 1 and "disallows the whole site" in refused[0]["error"] and "add" not in refused[0]

    def linking(method, url, headers, body, timeout):  # links to a posting, but is not a TalentBrew page itself
        return 200, {}, f'<a href="https://{HOST}{SPRINGFIELD}">Retail Store Associate</a>'.encode()

    other = registry.probe("https://www.globex.com/careers Globex", Client(linking, retries=0))
    assert other["matches"] == [] and other["next"].startswith("No link to a known career system")

    scaffold.init(tmp_path / "feed")
    root = tmp_path / "feed"
    assert (root / ".hunter/engine/ajh/sources/radancy.py").is_file()  # vendored with the rest of the engine
    registry.add(root, ["greenhouse:acme:Acme"])  # another board already called acme
    added = registry.add(root, ["radancy:Careers.Acme.com:Acme", "radancy:jobs.globex.com:Globex"])
    assert added == {"added": ["acme-radancy", "globex"], "unknown": [], "configured": 3}
    entries = {entry["id"]: entry for entry in json.loads((root / "config/sources.json").read_text())}
    assert entries["acme-radancy"] == {"id": "acme-radancy", "company": "Acme", "ats": "radancy", "host": HOST}
    assert entries["globex"] == {"id": "globex", "company": "Globex", "ats": "radancy", "host": "jobs.globex.com"}
    assert registry.add(root, ["radancy:careers.acme.com:Acme"])["added"] == []  # the same site again
    assert registry.hosts(root) == {"hosts": ["boards-api.greenhouse.io", HOST, "jobs.globex.com"]}
    for bad in ("careers.acme.com/en", "localhost", "10.0.0.1", "careers_acme.com"):
        with pytest.raises(SourceError, match="Radancy needs"):
            registry.add(root, [f"radancy:{bad}:Acme"])
