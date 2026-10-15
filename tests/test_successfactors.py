"""SAP SuccessFactors career sites: search pages checked against their own count, text fetched on demand."""

import html
import json
import re
import urllib.parse

import pytest
from ajh import registry, scaffold
from ajh import sources as S
from ajh.http import Client
from ajh.model import SourceError
from helpers import full_tick, prose, rows

ACME = {"id": "acme", "company": "Acme", "ats": "successfactors", "host": "jobs.acme.example"}
GLOBEX = {
    "id": "globex-labs",
    "company": "Globex",
    "ats": "successfactors",
    "host": "careers.globex.example",
    "brand": "labs",
}
SEARCHED = dict(ACME, queries=["UX Researcher"])
# What every Career Site Builder page sets up: SAP's stylesheet, its scripts and their settings, which
# name the sign-in host and the brand. A stylesheet rule names the row class without being a row.
SETUP = (
    '<link rel="stylesheet" href="https://rmkcdn.successfactors.com/0a1b2c3d/css/site.css">'
    "<style>.searchResultsShell .table > tbody > tr.data-row { border-bottom: none; }</style>"
    '<script src="/platform/js/j2w/min/j2w.core.min.js?h=1"></script>'
    "<script>j2w.init({\"ssoCompanyId\" : 'acme', \"ssoUrl\" : 'https://career4.successfactors.com', "
    '"brand"          : "BRAND"});</script>'
)


def posting(jid, title="UX Researcher", place="San Jose, CA, US", others=(), text=None, date=None):
    """A posting on the fake site: what its row shows, and the text of its own page."""
    return {"id": jid, "title": title, "place": place, "others": list(others), "date": date,
            "text": text or prose(int(jid) % 997)}  # fmt: skip


class Site:
    """A fake Career Site Builder site: its search pages as a table or as tiles, and a page per posting.

    `layout` is "table", "tiles", "script" (the page fills in its results from a script, so it shows
    none) or "changed" (rows without their count). The search matches a phrase anywhere in a posting's
    title or text, as the real one does with quotes. Tests set the other attributes to break it.
    """

    def __init__(self, *postings, host="jobs.acme.example", brand="", layout="table"):
        self.postings = {p["id"]: p for p in postings}
        self.host, self.brand, self.layout, self.prefix = host, brand, layout, f"/{brand}" if brand else ""
        self.requests, self.down, self.paths = [], set(), {}
        self.extra = self.grow = self.hide = 0  # counted but not listed; counted more after page one; left off a page
        self.link_past = False  # link a page past the last one its count gives
        self.named, self.blank = {}, set()  # a page naming another posting as itself; a page without text

    def path(self, p):
        slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{p['place'].split(',')[0]} {p['title']}").strip("-")
        return self.paths.get(p["id"], f"{self.prefix}/job/{slug}/{p['id']}/")

    def page(self, body):
        return f"<html><head>{SETUP.replace('BRAND', self.brand)}</head><body>{body}</body></html>"

    def search(self, query, start):
        phrase = query.strip('"').casefold()
        found = [p for p in self.postings.values() if phrase in p["title"].casefold() or phrase in p["text"].casefold()]
        if self.layout == "script":  # an empty, hidden results template, filled in by a script
            unify = '<script src="/platform/js/j2w/min/j2w.searchResultsUnify.min.js?h=1"></script>'
            return self.page(f'{unify}<div id="noresults" style="display:none"></div><ul id="job-results-list"></ul>')
        if not found:
            notice = '<span id="noresults-message">There are no open positions matching this search.</span>'
            return self.page(f'<div id="noresults" class="alert alert-block"><div id="attention"></div>{notice}</div>')
        total = len(found) + self.extra + (self.grow if start else 0)
        shown = found[start : start + 25]
        listed = shown[: len(shown) - self.hide] if self.hide else shown
        last = (total - 1) // 25 * 25 + (25 if self.link_past else 0)
        ask = urllib.parse.quote_plus(query)
        pages = "".join(
            f'<li><a href="{self.prefix}/search/?q={ask}&amp;startrow={n}">{n // 25 + 1}</a></li>'
            for n in range(0, last + 1, 25)
        )
        names = (start + 1, start + len(shown), total)
        if self.layout == "tiles":
            count = f'<span id="tile-search-results-label">Showing {names[0]} to {names[1]} of {names[2]} Jobs</span>'
            tiles = "".join(self.tile(p) for p in listed)
            body = f'<ul id="job-tile-list" data-per-page="25" data-record-returned="{len(shown)}">{tiles}</ul>'
        else:
            count = (
                f'<span class="paginationLabel" aria-label="Results {names[0]} – {names[1]}">'
                f"Results <b>{names[0]} – {names[1]}</b> of <b>{names[2]}</b></span>"
            )
            body = f'<table id="searchresults"><tbody>{"".join(self.row(p) for p in listed)}</tbody></table>'
        if self.layout == "changed":
            count = ""
        return self.page(f'{count}{body}<ul class="pagination">{pages}</ul>')

    def row(self, p):
        title, link = html.escape(p["title"]), self.path(p)
        date = f'<span class="jobDate">{p["date"]}</span>' if p["date"] else ""
        return (
            f'<tr class="data-row"><td class="colTitle"><span class="jobTitle hidden-phone">'
            f'<a href="{link}" class="jobTitle-link">{title}</a></span><div class="jobdetail-phone visible-phone">'
            f'<span class="jobTitle visible-phone"><a class="jobTitle-link" href="{link}">{title}</a></span></div></td>'
            f'<td class="colLocation hidden-phone"><span class="jobLocation"> {p["place"]} </span></td>'
            f'<td class="colDate hidden-phone">{date}</td></tr>'
        )

    def tile(self, p):
        fields = {
            "location": p["place"],
            "multilocation": "<br>".join([p["place"], *p["others"]]),
            "department": "Design",
        }
        cells = "".join(
            f'<div id="job-{p["id"]}-{screen}-section-{name}" class="section-field {name}">'
            f'<span id="job-{p["id"]}-{screen}-section-{name}-label" class="section-label">{name.title()}</span>'
            f'<div id="job-{p["id"]}-{screen}-section-{name}-value">{value} </div></div>'
            for screen in ("desktop", "mobile")
            for name, value in fields.items()
        )
        link = self.path(p)
        return (
            f'<li class="job-tile job-id-{p["id"]}" data-url="{link}"><div class="tiletitle">'
            f'<a class="jobTitle-link" href="{link}"> {html.escape(p["title"])} </a></div>{cells}</li>'
        )

    def posting_page(self, jid):
        p = self.postings[jid]
        named = f"https://{self.host}{self.path(dict(p, id=self.named.get(jid, jid)))}"
        text = "" if jid in self.blank else f"<p>{p['text']}</p><ul><li>Three years of research.</li></ul>"
        return self.page(
            f'<link rel="canonical" href="{named}" /><meta itemprop="datePosted" content="Thu Oct 15 07:00:00 UTC 2026">'
            f'<span itemprop="title">{html.escape(p["title"])}</span>'
            f'<span class="jobdescription" itemprop="description">{text}</span>'
            f'<a href="/talentcommunity/apply/{jid}/?locale=en_US">Apply now</a>'
        )

    def transport(self, method, url, headers, body, timeout):
        parts = urllib.parse.urlsplit(url)
        if parts.path == f"{self.prefix}/search/":
            ask = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
            self.requests.append(("search", ask["q"]))
            return 200, {}, self.search(ask["q"], int(ask["startrow"])).encode()
        one = re.fullmatch(re.escape(self.prefix) + r"/job/[^/]+/(\d+)/", parts.path)
        if one:
            self.requests.append(("detail", one.group(1)))
            if one.group(1) in self.down or one.group(1) not in self.postings:
                return 404, {}, b"Not found"
            return 200, {}, self.posting_page(one.group(1)).encode()
        if parts.path in ("/", f"{self.prefix}/"):
            self.requests.append(("home", parts.path))
            return 200, {}, self.page('<form action="/search/"><input name="q"></form>').encode()
        return 404, {}, b"Not found"

    def details(self):
        """The ids whose text was asked for since the last call."""
        asked = [jid for kind, jid in self.requests if kind == "detail"]
        self.requests = [r for r in self.requests if r[0] != "detail"]
        return asked


def client(transport):
    return Client(transport, sleep=lambda seconds: None, retries=0)


def web(*sites):
    """One transport for several sites by host, recording every host asked."""
    by_host, asked = {site.host: site for site in sites}, []

    def transport(method, url, headers, body, timeout):
        host = urllib.parse.urlsplit(url).hostname
        asked.append(host)
        return by_host[host].transport(method, url, headers, body, timeout) if host in by_host else (404, {}, b"")

    return transport, asked


def test_a_site_is_read_through_phrase_searches_with_its_facts_from_the_listing():
    site = Site(
        posting("1001", date="Oct 9, 2026"),
        posting("1002", "Senior UX Researcher", place="Toronto, ON, CA"),
        posting("1003", "Account Executive", text="Sell to research teams."),  # no "UX Researcher" anywhere
        posting("1004", "Research Lead", text="Work with a UX researcher every day."),  # in its text
    )
    counted = {job.source_id: job for job in S.fetch(client(site.transport), SEARCHED)}  # no rank: no text
    assert sorted(counted) == ["1001", "1002", "1004"] and site.details() == []
    assert site.requests == [("search", '"UX Researcher"')]  # a phrase: unquoted, any of the words would match
    one = counted["1001"]
    assert (one.title, one.location, one.posted_at, one.employment_type) == (
        "UX Researcher",
        "San Jose, CA, US",
        "Oct 9, 2026",  # the row's own date, when the site shows one
        "unknown",
    )
    assert one.url == "https://jobs.acme.example/job/San-Jose-UX-Researcher/1001/" and one.detail_skipped
    assert counted["1002"].posted_at is None and counted["1002"].location == "Toronto, ON, CA"

    jobs = {job.source_id: job for job in S.fetch(client(site.transport), SEARCHED, lambda job: S.NEW)}
    assert site.details() == ["1004", "1002", "1001"]  # newest first
    detailed = jobs["1001"]
    assert "Three years of research." in detailed.description and not detailed.detail_skipped
    assert (detailed.title, detailed.location, detailed.url, detailed.posted_at) == (
        one.title,
        one.location,
        one.url,
        "Oct 9, 2026",  # not the page's datePosted, which is the day it was read
    )
    assert detailed.apply_url is None  # the apply path is one robots.txt disallows


def test_tiles_and_a_brands_own_path_give_the_same_facts():
    site = Site(
        posting("2001", others=("Toronto, ON, CA",)),
        posting("2002", "UX Researcher II"),
        host="careers.globex.example",
        brand="labs",
        layout="tiles",
    )
    searched = dict(GLOBEX, queries=["UX Researcher"])
    jobs = {job.source_id: job for job in S.fetch(client(site.transport), searched, lambda job: S.NEW)}
    assert jobs["2001"].location == "San Jose, CA, US / Toronto, ON, CA"  # every place it lists, for stage 0
    assert jobs["2001"].url == "https://careers.globex.example/labs/job/San-Jose-UX-Researcher/2001/"
    assert (jobs["2002"].location, jobs["2002"].department, jobs["2002"].title) == (
        "San Jose, CA, US",
        "Design",
        "UX Researcher II",
    )
    assert "Three years of research." in jobs["2002"].description


def test_anything_short_of_a_complete_checked_listing_fails_the_source(monkeypatch):
    postings = [posting(str(3000 + n)) for n in range(60)]
    assert len(S.fetch(client(Site(*postings).transport), SEARCHED)) == 60  # three pages of 25
    assert len(S.fetch(client(Site(*postings, layout="tiles").transport), SEARCHED)) == 60
    for layout in ("table", "tiles"):
        for change, problem in (
            ({"extra": 3}, "short of its total"),  # it counts more postings than it lists
            ({"grow": 1}, "changed while it was read"),
            ({"hide": 1}, "does not match its own count"),  # a page shows fewer rows than its count names
            ({"link_past": True}, "links past the count"),  # its pages go on past its count: the count was misread
        ):
            broken = Site(*postings, layout=layout)
            vars(broken).update(change)
            with pytest.raises(SourceError, match=problem):
                S.fetch(client(broken.transport), SEARCHED)
    with pytest.raises(SourceError, match=r"robots\.txt disallows"):  # its hidden, empty template is not "no postings"
        S.fetch(client(Site(*postings, layout="script").transport), SEARCHED)
    with pytest.raises(SourceError, match="neither postings with their count"):
        S.fetch(client(Site(*postings, layout="changed").transport), SEARCHED)
    assert S.fetch(client(Site().transport), SEARCHED) == []  # only the site's own notice says nothing matched

    site = Site(*postings)
    for bad, problem in (
        (dict(SEARCHED, host="career4.successfactors.com"), "SAP's own host"),
        (dict(SEARCHED, host="10.0.0.1"), "host name"),
        (dict(SEARCHED, brand="../x"), "host name"),
        (dict(ACME), "searches"),  # no queries, and none filled in from the criteria
    ):
        with pytest.raises(SourceError, match=problem):
            S.fetch(client(site.transport), bad)
    for link in ("/job/../../services/x/3000/", "https://elsewhere.example/job/x/3000/", "/content/3000/"):
        site.paths = {"3000": link}
        with pytest.raises(SourceError, match="not one posting on this site"):
            S.fetch(client(site.transport), SEARCHED)
    from ajh.sources import successfactors

    monkeypatch.setattr(successfactors, "MAX_PER_QUERY", 50)
    with pytest.raises(SourceError, match="more specific queries"):
        S.fetch(client(Site(*postings).transport), SEARCHED)


@pytest.mark.parametrize("written, total", [("1,234", 1234), ("1.234", 1234), ("1\u00a0234", 1234), ("1 234", None)])
def test_a_count_is_read_by_its_structure_and_one_that_cannot_be_read_is_refused(written, total):
    from ajh.sources import successfactors

    page = (
        f'<span class="paginationLabel">Results <b>1 – 1</b> of <b>{written}</b></span>'
        '<table><tr class="data-row"><td><a class="jobTitle-link" href="/job/x/1/">A</a></td></tr></table>'
    )
    if total is None:  # an ordinary space between digits could be two numbers: refused rather than guessed
        with pytest.raises(SourceError, match="cannot read"):
            successfactors.read(page, 0)
    else:
        assert successfactors.read(page, 0).total == total


def test_texts_are_fetched_in_rank_order_within_the_budget_and_a_failed_one_is_skipped():
    site = Site(*(posting(str(4000 + n)) for n in range(5)))
    ranks = {"4000": S.LISTED, "4001": S.NEW, "4002": S.PENDING, "4003": S.CHANGED, "4004": None}
    searched = dict(SEARCHED, max_details=3)
    jobs = {job.source_id: job for job in S.fetch(client(site.transport), searched, lambda job: ranks[job.source_id])}
    assert site.details() == ["4002", "4001", "4003"]  # about to be judged, new, changed; then the budget ends
    assert sorted(jid for jid, job in jobs.items() if job.detail_skipped) == ["4000", "4004"]

    site.down, site.named, site.blank = {"4002"}, {"4001": "4999"}, {"4003"}  # fails; another posting's page; empty
    jobs = {job.source_id: job for job in S.fetch(client(site.transport), searched, lambda job: ranks[job.source_id])}
    assert site.details() == ["4002", "4001", "4003"]
    assert all(jobs[jid].detail_skipped and not jobs[jid].description for jid in ("4002", "4001", "4003"))


def test_stage_0_reads_the_listing_so_a_posting_elsewhere_costs_no_text(repo):
    site = Site(posting("5001"), posting("5002", place="Munich, Germany"))
    root = repo(sources=(ACME,), criteria={"locations": {"regions": ["US"]}})
    full_tick(root, site)
    assert ("search", '"UX Researcher"') in site.requests  # the wanted role from the criteria, as a phrase
    assert site.details() == ["5001"] and sorted(rows(root, "acme")) == ["5001"]  # Munich is out on the listing
    full_tick(root, site)  # listed now: its text only for its brief; the one in Munich is still never fetched
    assert site.details() == ["5001"]


def test_the_probe_knows_a_career_site_by_its_page_and_the_add_line_writes_it(tmp_path):
    acme = Site(posting("6001"), posting("6002", "Analyst"))
    globex = Site(posting("6101"), host="careers.globex.example", brand="labs", layout="tiles")
    initech = Site(posting("6201"), host="jobs.initech.example", layout="script")
    transport, asked = web(acme, globex, initech)
    found = registry.probe("https://jobs.acme.example/ Acme", client(transport))
    assert found["matches"] == [
        {
            "system": "SAP SuccessFactors",
            "ats": "successfactors",
            "host": "jobs.acme.example",
            "supported": True,
            "postings": 2,
            "sample": ["UX Researcher", "Analyst"],
            "add": "successfactors:jobs.acme.example:Acme",
        }
    ]
    assert found["unsupported"] == []  # not its links to SAP's own hosts: those are its scripts and sign-in
    branded = registry.probe(
        "https://careers.globex.example/labs/job/San-Jose-UX-Researcher/6101/ Globex", client(transport)
    )
    assert branded["matches"][0]["add"] == "successfactors:careers.globex.example/labs:Globex"
    assert branded["matches"][0]["postings"] == 1
    scripted = registry.probe("https://jobs.initech.example/ Initech", client(transport))
    assert scripted["matches"] == [] and "cannot read" in scripted["next"]
    assert scripted["unsupported"] == [
        "SAP SuccessFactors (a career site whose search loads its postings from a path its robots.txt disallows)"
    ]
    old = registry.probe("https://career4.successfactors.com/career?company=hooli Hooli", client(transport))
    assert old["unsupported"] == ["SAP SuccessFactors on SAP's own hosts (closed by robots.txt)"]
    assert "career4.successfactors.com" not in asked  # named by its host alone: its robots.txt disallows reading

    scaffold.init(tmp_path / "feed")
    root = tmp_path / "feed"
    added = registry.add(
        root, ["successfactors:Jobs.Acme.example:Acme", "successfactors:careers.globex.example/labs:Globex"]
    )
    assert added["added"] == ["acme", "globex-labs"]
    assert json.loads((root / "config/sources.json").read_text()) == [ACME, GLOBEX]
    assert registry.add(root, ["successfactors:careers.acme.example:Acme"])["added"] == ["careers-acme-example"]
    assert registry.hosts(root) == {"hosts": ["careers.acme.example", "careers.globex.example", "jobs.acme.example"]}
    for bad in ("career4.successfactors.com", "jobs.acme.example/../x", "localhost", "10.0.0.1"):
        with pytest.raises(SourceError):
            registry.add(root, [f"successfactors:{bad}:Acme"])
