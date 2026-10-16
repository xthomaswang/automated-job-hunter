"""Eightfold career sites: the JSON their own pages load, read through searches with text fetched on demand."""

import html
import json
import re
import urllib.parse

import pytest
from ajh import registry, scaffold
from ajh import sources as S
from ajh.http import Client, HttpError
from ajh.model import SourceError
from ajh.sources import eightfold
from ajh.state import tag
from helpers import full_tick, prose, rows, tick

ACME = {"id": "acme", "company": "Acme", "ats": "eightfold", "host": "acme.eightfold.ai", "domain": "acme.example"}
SEARCHED = dict(ACME, queries=["UX Researcher"])
GLOBEX = "careers.globex.example"  # the same app on a company's own host
OCT_12 = 1791763200  # 2026-10-12T00:00:00Z, as the listing gives it: seconds since the epoch
OLDER_HOST = "careers.acme.com"  # a site in the app's older layout, on its company's own host
OLDER = {"id": "acme", "company": "Acme", "ats": "eightfold", "host": OLDER_HOST, "domain": "acme.com",
         "layout": "apply-v2"}  # fmt: skip
OLDER_SEARCHED = dict(OLDER, queries=["UX Researcher"])
# robots.txt as such a site serves it: everything disallowed but a few paths, the older API's among them.
ROBOTS = (
    "User-agent: *\nDisallow: /\nAllow: /careers\nAllow: /api/apply\nAllow: /api/pcsx\nAllow: /static\n"
    f"Sitemap: https://{OLDER_HOST}/sitemap.xml\n"
)
UI = {"branding": {"theme": "light"}, "showLocationFilter": True, "i18n": {"apply": "Apply"}}  # a few of many


def page(domain):
    """A career page as the app serves it: its settings in a hidden element, with escaped quotes."""
    settings = json.dumps({"domain": domain, "configs": {"pcsxConfig": {"enabled": True}}}).replace('"', "&#34;")
    links = '<a href="https://eightfold.ai/privacy-policy/">Privacy</a> <a href="https://app.eightfold.ai">Sign in</a> '
    links += '<a href="https://acme.eightfold.ai/careers?domain=acme.example">Acme roles</a>'
    hidden = f'<code id="pcsx-data" style="display:none;" data-nosnippet>{settings}</code>'
    return f'<html><body><div id="pcsx"></div>{hidden}{links}</body></html>'


def position(jid, name="UX Researcher", places=("San Jose, California, United States",), workplace="onsite",
             coded=("San Jose, CA, US",), posted=OCT_12, kind="Full time"):  # fmt: skip
    """A posting on a fake site: its listing row, and the text and fields its detail adds."""
    return {
        "row": {
            "id": jid,
            "displayJobId": f"R{jid % 1000}",
            "name": name,
            "locations": list(places),
            "standardizedLocations": list(coded),
            "postedTs": posted,
            "creationTs": posted - 86400,
            "department": "Design",
            "workLocationOption": workplace,
            "locationFlexibility": None,
            "atsJobId": f"R{jid % 1000}",
            "positionUrl": f"/careers/job/{jid}",
            "isHot": 0,
            "stars": 0,
        },
        "text": f"<p>{prose(jid % 997)}</p><ul><li>Three years of research.</li></ul>",
        "fields": {"efcustomTextTimeType": [kind], "efcustomTextTimeTypeEvg": [], "efcustomTextJobFunction2": None},
    }


def older_page(domain, rows):
    """A career page in the older layout: its settings as HTML-escaped JSON in a hidden element, the
    first 10 postings and their count among them, and the domain after other settings."""
    settings = {**UI, "positions": rows[:10], "count": len(rows), "domain": domain, "searchPlaceholder": "Search"}
    hidden = f'<code id="smartApplyData" style="display: none">{html.escape(json.dumps(settings))}</code>'
    links = '<a href="https://eightfold.ai/privacy-policy/">Privacy</a> <a href="https://app.eightfold.ai">Sign in</a>'
    return f'<html><body><div id="EightfoldWidget"></div>{hidden}{links}</body></html>'


def older_position(jid, name="UX Researcher", places=("Helsinki,Finland",), workplace="onsite", created=OCT_12,
                   record=None):  # fmt: skip
    """A posting on the fake site in the older layout: its listing row and its text. `record` is the
    places its own record names, when they are not the row's."""
    row = {
        "id": jid,
        "name": name,
        "posting_name": name,
        "location": places[0] if places else "",
        "locations": list(places),
        "department": "Design",
        "business_unit": "Research",
        "t_create": created,
        "t_update": created + 86400,
        "ats_job_id": f"JR{jid % 100000}",
        "work_location_option": workplace,
        "canonicalPositionUrl": f"https://{OLDER_HOST}/careers/job/{jid}",
        "job_description": "",  # empty in the listing
    }
    places = list(places if record is None else record)
    text = f"<p>{prose(jid % 997)}</p><ul><li>Three years of research.</li></ul>"
    return {"row": row, "text": text, "record": {"location": places[0] if places else "", "locations": places}}


class Sites:
    """Fake Eightfold career sites by host, each with the domain its pages name and its postings.

    A search matches a title containing the query, 10 a page, with its total on every page. A request
    without the site's domain is refused, as there; `down` lists postings whose detail fails. `older`
    holds the postings of a site in the older layout (see `older`). Every URL asked is in `urls`.
    """

    def __init__(self, *postings, globex=(), older=()):
        self.sites = {
            "acme.eightfold.ai": ("acme.example", {p["row"]["id"]: p for p in postings}),
            GLOBEX: ("globex.example", {p["row"]["id"]: p for p in globex}),
            OLDER_HOST: ("acme.com", {p["row"]["id"]: p for p in older}),
        }
        self.down, self.requests, self.urls = set(), [], []
        self.robots = (200, ROBOTS)  # the status and text of the older site's robots.txt

    def older(self, parts, ask):
        """The site in the older layout: robots.txt, its pages, and its listing and records under /api/apply/v2."""
        domain, postings = self.sites[OLDER_HOST]
        if parts.path == "/robots.txt":
            self.requests.append(("robots",))
            return self.robots[0], {}, self.robots[1].encode()
        if parts.path == "/careers" or parts.path.startswith("/careers/"):
            self.requests.append(("page", parts.path))
            return 200, {}, older_page(domain, [p["row"] for p in postings.values()]).encode()
        if parts.path.startswith("/api/pcsx/"):  # the newer layout's API is not this site's
            return 403, {}, b'{"message": "Not authorized"}'
        if ask.get("domain") != domain:
            return 400, {}, b'{"message": "domain is required"}'
        if parts.path == "/api/apply/v2/jobs":
            query, start, num = ask["query"], int(ask["start"]), min(int(ask["num"]), 10)
            self.requests.append(("search", query, start))
            matched = [p["row"] for p in postings.values() if query.lower() in p["row"]["name"].lower()]
            listing = {**UI, "count": len(matched), "positions": matched[start : start + num]}  # no wrapper
            return 200, {}, json.dumps(listing).encode()
        if parts.path.startswith("/api/apply/v2/jobs/"):
            jid = int(parts.path.rsplit("/", 1)[1])
            self.requests.append(("detail", jid))
            if jid in self.down or jid not in postings:
                return 500, {}, b"{}"
            found = postings[jid]
            return 200, {}, json.dumps({**found["row"], **found["record"], "job_description": found["text"]}).encode()
        return 404, {}, b"{}"

    def transport(self, method, url, headers, body, timeout):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        ask = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        if parts.hostname == OLDER_HOST:
            return self.older(parts, ask)
        if parts.hostname not in self.sites:
            self.requests.append(("unknown", parts.hostname))
            return 404, {}, b"<html><body>Group ID not found</body></html>"
        domain, postings = self.sites[parts.hostname]
        if parts.path == "/careers" or parts.path.startswith("/careers/"):
            self.requests.append(("page", parts.path))
            return 200, {}, page(domain).encode()
        if ask.get("domain") != domain:
            return 422, {}, json.dumps({"messages": {"domain": ["Missing data for required field."]}}).encode()
        if parts.path == "/api/pcsx/search":
            query, start = ask["query"], int(ask["start"])
            self.requests.append(("search", query, start))
            matched = [p["row"] for p in postings.values() if query.lower() in p["row"]["name"].lower()]
            data = {"positions": matched[start : start + 10], "count": len(matched), "appliedFilters": {}}
            return 200, {}, json.dumps({"status": 200, "error": {"message": ""}, "data": data}).encode()
        if parts.path == "/api/pcsx/position_details":
            jid = int(ask["position_id"])
            self.requests.append(("detail", jid))
            if jid in self.down or jid not in postings:
                return 500, {}, b"{}"
            found = postings[jid]
            data = {**found["row"], "jobDescription": found["text"], "location": ", ".join(found["row"]["locations"]),
                    "publicUrl": f"https://{parts.hostname}/careers/job/{jid}", **found["fields"]}  # fmt: skip
            return 200, {}, json.dumps({"status": 200, "error": {"message": ""}, "data": data}).encode()
        return 404, {}, b"{}"

    def asked(self, kind):
        """What was asked of this kind since the last call: detail ids, or (query, start) of searches."""
        found = [r[1] if kind == "detail" else r[1:] for r in self.requests if r[0] == kind]
        self.requests = [r for r in self.requests if r[0] != kind]
        return found


def client(sites):
    return Client(sites.transport, sleep=lambda seconds: None, retries=0)


def test_a_site_is_read_through_its_searches_with_its_facts_from_the_listing():
    sites = Sites(
        position(500000000101),
        position(500000000102, places=("Lisbon, Lisbon, Portugal", "Porto, Porto, Portugal"), workplace="remote"),
        position(500000000103, "Account Executive"),  # not matched by the search
    )
    counted = {job.source_id: job for job in S.fetch(client(sites), SEARCHED)}  # no rank: no text
    assert sorted(counted) == ["500000000101", "500000000102"] and sites.asked("detail") == []
    one = counted["500000000101"]
    assert (one.title, one.location, one.posted_at, one.employment_type, one.department) == (
        "UX Researcher",
        "San Jose, California, United States",  # the site's own names: its codes say CA, and DE or IN are states too
        "2026-10-12",
        "unknown",  # the listing has none; the detail's goes into the text
        "Design",
    )
    assert one.url == "https://acme.eightfold.ai/careers/job/500000000101" and one.detail_skipped
    assert (
        not one.description
        and counted["500000000102"].location == "Remote, Lisbon, Lisbon, Portugal / Porto, Porto, Portugal"
    )

    jobs = {job.source_id: job for job in S.fetch(client(sites), SEARCHED, lambda job: S.NEW)}
    assert sites.asked("detail") == [500000000102, 500000000101]  # newest first
    detailed = jobs["500000000101"]
    assert (
        detailed.description.startswith("Time type: Full time\n\n")
        and "Three years of research." in detailed.description
    )
    assert (detailed.location, detailed.url) == (one.location, one.url) and not detailed.detail_skipped


def test_every_page_is_read_and_anything_short_of_the_total_fails_the_source():
    sites = Sites(*(position(500000000200 + n) for n in range(25)))
    assert len(S.fetch(client(sites), SEARCHED)) == 25
    assert sites.asked("search") == [("UX Researcher", 0), ("UX Researcher", 10), ("UX Researcher", 20)]

    def changed(change):
        def transport(method, url, headers, body, timeout):
            status, sent, data = sites.transport(method, url, headers, body, timeout)
            reply = json.loads(data)
            if "start=" in url and "start=0" not in url:
                change(reply["data"])
            return status, sent, json.dumps(reply).encode()

        return Client(transport, sleep=lambda seconds: None, retries=0)

    with pytest.raises(SourceError, match="short of its total"):  # a row missing from a later page
        S.fetch(changed(lambda data: data["positions"].pop()), SEARCHED)
    with pytest.raises(SourceError, match="changed while it was read"):  # the search grew while it was read
        S.fetch(changed(lambda data: data.update(count=data["count"] + 1)), SEARCHED)
    with pytest.raises(SourceError, match="short of its total"):  # a later page repeats rows instead
        S.fetch(changed(lambda data: data.update(positions=data["positions"][:1] * 10)), SEARCHED)
    for bad, problem in (
        (dict(SEARCHED, domain=None), "Eightfold needs"),
        (dict(SEARCHED, host="acme.eightfold.ai/../x"), "Eightfold needs"),
        (dict(SEARCHED, host="localhost"), "Eightfold needs"),
        (dict(ACME), "searches"),  # no queries, and none filled in from the criteria
    ):
        with pytest.raises(SourceError, match=problem):
            S.fetch(client(sites), bad)
    sites.sites["acme.eightfold.ai"][1][500000000200]["row"]["id"] = "../x"
    with pytest.raises(SourceError, match="not a number"):
        S.fetch(client(sites), SEARCHED)


def test_a_search_matching_too_many_postings_is_refused_before_it_is_read():
    sites = Sites(*(position(500000000300 + n) for n in range(12)))

    def broad(method, url, headers, body, timeout):
        status, sent, data = sites.transport(method, url, headers, body, timeout)
        reply = json.loads(data)
        reply["data"]["count"] = 501  # 51 pages: too many to read every update
        return status, sent, json.dumps(reply).encode()

    with pytest.raises(SourceError, match="matches 501 postings, more than 500: use fewer or more specific queries"):
        S.fetch(Client(broad, sleep=lambda seconds: None, retries=0), SEARCHED)
    assert sites.asked("search") == [("UX Researcher", 0)]  # the first page only
    reply = Client(lambda *a: (200, {}, b'{"status": 500, "data": {}}'), retries=0)
    with pytest.raises(SourceError, match="not in the expected shape"):
        S.fetch(reply, SEARCHED)


def test_texts_are_fetched_in_rank_order_within_the_budget_and_a_failed_one_waits():
    sites = Sites(*(position(500000000400 + n) for n in range(5)))
    pending = "500000000401"
    rank = lambda job: S.PENDING if job.source_id == pending else S.NEW  # noqa: E731
    jobs = {job.source_id: job for job in S.fetch(client(sites), dict(SEARCHED, max_details=3), rank)}
    assert sites.asked("detail") == [500000000401, 500000000404, 500000000403]  # the one about to be judged first
    assert sorted(jid for jid, job in jobs.items() if job.detail_skipped) == ["500000000400", "500000000402"]

    sites.down = {500000000404}
    jobs = {job.source_id: job for job in S.fetch(client(sites), SEARCHED, rank)}
    assert jobs["500000000404"].detail_skipped and not jobs["500000000404"].description  # it waits for a later update
    assert not jobs["500000000403"].detail_skipped and len(sites.asked("detail")) == 5


def test_stage_0_reads_the_listing_so_an_out_of_region_posting_costs_no_text(repo):
    sites = Sites(
        position(500000000501),
        position(500000000502, places=("Berlin, Berlin, Germany",), coded=("Berlin, BE, DE",)),  # DE: also Delaware
        position(500000000503, "Senior UX Researcher", places=(), workplace="remote"),  # nothing to read: kept
    )
    root = repo(sources=(ACME,), criteria={"locations": {"regions": ["US"]}})
    full_tick(root, sites)
    assert sites.asked("search") == [("UX Researcher", 0)]  # the wanted role, from the criteria
    assert sites.asked("detail") == [500000000503, 500000000501] and sorted(rows(root, "acme")) == [
        "500000000501",
        "500000000503",
    ]
    full_tick(root, sites)  # listed: text only for their briefs; the one in Berlin is still never fetched
    assert 500000000502 not in sites.asked("detail")


def test_probe_finds_a_site_by_its_careers_or_posting_url():
    sites = Sites(position(500000000601), position(500000000602, "Analyst"), globex=[position(700000000001)])
    web = client(sites)
    expected = {"system": "Eightfold", "ats": "eightfold", "host": "acme.eightfold.ai", "domain": "acme.example",
                "supported": True, "postings": 2, "sample": ["UX Researcher", "Analyst"],
                "add": "eightfold:acme.eightfold.ai/acme.example:Acme"}  # fmt: skip
    careers = registry.probe("https://acme.eightfold.ai/careers?domain=acme.example&query=design Acme", web)
    assert careers["matches"] == [expected] and careers["unsupported"] == []
    assert sites.asked("page") == [] and sites.asked("search") == [("", 0)]  # the domain was in the link: one request
    posting = registry.probe("https://acme.eightfold.ai/careers/job/500000000601 Acme", web)
    assert posting["matches"] == [expected] and sites.asked("page") == [("/careers",)]  # its pages name the domain

    own = registry.probe(f"https://{GLOBEX}/careers/job/700000000001 Globex", web)  # an unknown host: its page says
    assert [m["add"] for m in own["matches"]] == [f"eightfold:{GLOBEX}/globex.example:Globex"]  # not what it links to
    assert own["matches"][0]["postings"] == 1 and own["unsupported"] == []

    def linking(method, url, headers, body, timeout):  # a company's own page linking to its site
        if url == "https://www.acme.example/careers":
            return 200, {}, b'<a href="https://acme.eightfold.ai/careers?domain=acme.example">Open roles</a>'
        return sites.transport(method, url, headers, body, timeout)

    linked = registry.probe("https://www.acme.example/careers", Client(linking, retries=0))
    assert [m["add"] for m in linked["matches"]] == ["eightfold:acme.eightfold.ai/acme.example:<Company Name>"]
    unknown = registry.probe("https://initech.eightfold.ai/careers Initech", web)["matches"][0]
    assert "404" in unknown["error"] and "add" not in unknown
    for vendor in ("https://eightfold.ai/privacy-policy/", "https://app.eightfold.ai/"):  # links on every site
        assert registry.probe(vendor, Client(lambda *a: (200, {}, b"<html></html>"), retries=0))["matches"] == []


def test_probe_follows_an_old_careers_address_to_the_host_it_moved_to():
    sites = Sites(globex=[position(700000000002)])
    old, landing = "https://jobs.globex.example/global/en/search", f"https://{GLOBEX}/careers"

    def moved(method, url, headers, body, timeout):  # the old address redirects to the site's new host
        if url == old:
            return (*sites.transport(method, landing, headers, body, timeout), landing)
        return sites.transport(method, url, headers, body, timeout)

    found = registry.probe(f"{old} Globex", Client(moved, retries=0))
    assert found["resolved"] == landing
    assert [m["add"] for m in found["matches"]] == [f"eightfold:{GLOBEX}/globex.example:Globex"]
    assert found["matches"][0]["postings"] == 1
    assert sites.asked("unknown") == [] and sites.asked("search") == [("", 0)]  # asked of the new host, not the old


def test_a_site_is_added_by_host_and_domain_and_hosts_name_it(tmp_path):
    scaffold.init(tmp_path / "feed")
    root = tmp_path / "feed"
    registry.add(root, ["greenhouse:acme:Acme"])  # another board already called acme
    added = registry.add(
        root, ["eightfold:Acme.Eightfold.ai/acme.example:Acme", f"eightfold:{GLOBEX}/globex.example:Globex"]
    )
    assert added == {"added": ["acme-eightfold", "globex"], "unknown": [], "configured": 3}
    entries = {entry["id"]: entry for entry in json.loads((root / "config/sources.json").read_text())}
    assert entries["acme-eightfold"] == {"id": "acme-eightfold", "company": "Acme", "ats": "eightfold",
                                         "host": "acme.eightfold.ai", "domain": "acme.example"}  # fmt: skip
    assert entries["globex"]["host"] == GLOBEX and entries["acme"]["ats"] == "greenhouse"
    assert registry.add(root, [f"eightfold:{GLOBEX}/globex.example:Globex"])["added"] == []  # the same site again
    assert registry.hosts(root) == {"hosts": ["acme.eightfold.ai", "boards-api.greenhouse.io", GLOBEX]}
    for bad in (
        "eightfold:acme.eightfold.ai:Acme",
        "eightfold:acme.eightfold.ai/a b:Acme",
        "eightfold:/acme.example:A",
    ):
        with pytest.raises(SourceError, match="Eightfold needs"):
            registry.add(root, [bad])


def test_probe_knows_a_site_in_the_older_layout_by_its_careers_or_posting_page():
    sites = Sites(older=[older_position(790315531000 + n, f"Researcher {n}") for n in range(12)])
    expected = {"system": "Eightfold", "ats": "eightfold", "host": OLDER_HOST, "domain": "acme.com",
                "layout": "apply-v2", "supported": True, "postings": 12,
                "sample": ["Researcher 0", "Researcher 1", "Researcher 2"],
                "add": f"eightfold:{OLDER_HOST}/acme.com/apply-v2:Acme"}  # fmt: skip
    for path in ("/careers", "/careers/job/790315531004"):
        found = registry.probe(f"https://{OLDER_HOST}{path} Acme", client(sites))
        assert found["matches"] == [expected] and found["unsupported"] == []
        asked = [urllib.parse.urlsplit(url) for url in sites.urls]
        assert [url.path for url in asked] == [path, "/robots.txt", "/api/apply/v2/jobs"]  # its page names the site
        assert asked[2].query == "domain=acme.com&start=0&num=10&query=&sort_by=relevance"
        sites.urls.clear()

    sites.robots = (200, "User-agent: *\nDisallow: /\nAllow: /careers\n")  # its pages, but not its listing
    refused = registry.probe(f"https://{OLDER_HOST}/careers Acme", client(sites))["matches"][0]
    assert "robots.txt on careers.acme.com disallows /api/apply/v2/jobs" in refused["error"] and "add" not in refused
    assert [urllib.parse.urlsplit(url).path for url in sites.urls] == ["/careers", "/robots.txt"]

    listed = [p["row"] for p in sites.sites[OLDER_HOST][1].values()]
    assert eightfold.page_site(older_page("acme.com", listed)) == {"domain": "acme.com", "layout": "apply-v2"}
    assert eightfold.page_site(page("acme.example")) == {"domain": "acme.example"}  # the newer layout: no field
    settings = {"domain": "acme.com", "positions": [], "count": 0}
    for other in (
        '<code id="smartApplyData">{"domain": "acme.com"</code>',  # not JSON
        f'<code id="smartApplyData">{html.escape(json.dumps({"domain": "acme.com"}))}</code>',  # no postings, no count
        f'<code id="smartApplyData">{html.escape(json.dumps(dict(settings, domain="../x")))}</code>',
        f'<code id="otherData">{html.escape(json.dumps(settings))}</code>',
    ):
        assert eightfold.page_site(other) is None


def test_the_add_line_of_a_site_in_the_older_layout_writes_its_layout_and_hosts_name_it(tmp_path):
    scaffold.init(tmp_path / "feed")
    root = tmp_path / "feed"

    def entries():
        return {entry["id"]: entry for entry in json.loads((root / "config/sources.json").read_text())}

    added = registry.add(root, [f"eightfold:{OLDER_HOST}/acme.com/apply-v2:Acme"])
    assert added == {"added": ["acme"], "unknown": [], "configured": 1} and entries() == {"acme": OLDER}
    assert registry.hosts(root) == {"hosts": [OLDER_HOST]}
    # A site that moves to the newer layout is probed again, and its new add line replaces the entry, id and all.
    assert registry.add(root, [f"eightfold:{OLDER_HOST}/acme.com:Acme"])["added"] == []
    assert entries() == {"acme": {key: value for key, value in OLDER.items() if key != "layout"}}
    for bad in ("acme.com/apply-v3", "acme.com/", "acme.com/apply-v2/x", "acme.com/Apply-V2"):
        with pytest.raises(SourceError, match='"layout" is "apply-v2" for a site in the older layout'):
            registry.add(root, [f"eightfold:{OLDER_HOST}/{bad}:Acme"])


def test_a_site_in_the_older_layout_is_read_page_by_page_with_its_facts_from_the_listing():
    sites = Sites(
        older=[
            *(older_position(790315531100 + n) for n in range(23)),
            older_position(790315531200, places=("Helsinki,Finland", " Espoo , Finland "), workplace="remote"),
            older_position(790315531201, "Design Lead", places=()),
            older_position(790315531202, "Design Lead", places=(), workplace="hybrid"),
            older_position(790315531203, "Account Executive"),  # matched by neither search
        ]
    )
    sites.sites[OLDER_HOST][1][790315531201]["row"]["location"] = "Oulu,Finland"  # its one place, and no list
    searched = dict(OLDER, queries=["UX Researcher", "Design Lead"])
    counted = {job.source_id: job for job in S.fetch(client(sites), searched)}  # no rank: no text
    assert len(counted) == 26 and "790315531203" not in counted
    assert sites.requests == [("robots",), ("search", "UX Researcher", 0), ("search", "UX Researcher", 10),
                              ("search", "UX Researcher", 20), ("search", "Design Lead", 0)]  # fmt: skip
    first = urllib.parse.urlsplit(sites.urls[1])
    assert (first.path, first.query) == (
        "/api/apply/v2/jobs",
        "domain=acme.com&start=0&num=10&query=UX+Researcher&sort_by=relevance",
    )
    one = counted["790315531100"]
    assert (one.title, one.location, one.posted_at, one.department, one.employment_type) == (
        "UX Researcher",
        "Helsinki, Finland",  # as the record writes "Helsinki,Finland": the country spelled out
        "2026-10-12",  # its t_create
        "Design",
        "unknown",
    )
    assert one.url == f"https://{OLDER_HOST}/careers/job/790315531100" and one.detail_skipped and not one.description
    assert counted["790315531200"].location == "Remote, Helsinki, Finland / Espoo, Finland"
    assert (counted["790315531201"].location, counted["790315531202"].location) == ("Oulu, Finland", "Hybrid, unknown")

    def broad(method, url, headers, body, timeout):
        status, sent, data = sites.transport(method, url, headers, body, timeout)
        if "/api/apply/v2/jobs?" in url:
            data = json.dumps(dict(json.loads(data), count=501)).encode()  # 51 pages: too many to read every update
        return status, sent, data

    sites.requests.clear()
    with pytest.raises(SourceError, match="matches 501 postings, more than 500: use fewer or more specific queries"):
        S.fetch(Client(broad, sleep=lambda seconds: None, retries=0), OLDER_SEARCHED)
    assert sites.requests == [("robots",), ("search", "UX Researcher", 0)]  # the first page only


def test_an_older_listing_short_of_its_count_or_in_another_shape_fails_the_source():
    sites = Sites(older=[older_position(790315531300 + n) for n in range(25)])
    assert len(S.fetch(client(sites), OLDER_SEARCHED)) == 25

    def changed(change):
        def transport(method, url, headers, body, timeout):
            status, sent, data = sites.transport(method, url, headers, body, timeout)
            if "/api/apply/v2/jobs?" in url and "start=0&" not in url:
                reply = json.loads(data)
                change(reply)
                data = json.dumps(reply).encode()
            return status, sent, data

        return Client(transport, sleep=lambda seconds: None, retries=0)

    with pytest.raises(SourceError, match="short of its total"):  # a row missing from a later page
        S.fetch(changed(lambda reply: reply["positions"].pop()), OLDER_SEARCHED)
    with pytest.raises(SourceError, match="short of its total"):  # a later page with no rows at all
        S.fetch(changed(lambda reply: reply.update(positions=[])), OLDER_SEARCHED)
    with pytest.raises(SourceError, match="short of its total"):  # a later page repeating rows instead
        S.fetch(changed(lambda reply: reply.update(positions=reply["positions"][:1] * 10)), OLDER_SEARCHED)
    with pytest.raises(SourceError, match="changed while it was read"):  # the search grew while it was read
        S.fetch(changed(lambda reply: reply.update(count=reply["count"] + 1)), OLDER_SEARCHED)

    def newer(method, url, headers, body, timeout):  # the newer layout's reply, which wraps the listing
        if "/api/apply/v2/jobs?" in url:
            return 200, {}, json.dumps({"status": 200, "data": {"count": 0, "positions": []}}).encode()
        return sites.transport(method, url, headers, body, timeout)

    with pytest.raises(SourceError, match="not in the expected shape"):
        S.fetch(Client(newer, sleep=lambda seconds: None, retries=0), OLDER_SEARCHED)
    sites.sites[OLDER_HOST][1][790315531300]["row"]["id"] = "../x"
    with pytest.raises(SourceError, match="not a number"):
        S.fetch(client(sites), OLDER_SEARCHED)


def test_older_texts_are_fetched_in_rank_order_within_the_budget_and_a_failed_one_waits():
    sites = Sites(older=[older_position(790315531400 + n) for n in range(5)])
    pending = "790315531401"
    rank = lambda job: S.PENDING if job.source_id == pending else S.NEW  # noqa: E731
    jobs = {job.source_id: job for job in S.fetch(client(sites), dict(OLDER_SEARCHED, max_details=3), rank)}
    assert sites.asked("detail") == [790315531401, 790315531404, 790315531403]  # the one about to be judged first
    assert sorted(jid for jid, job in jobs.items() if job.detail_skipped) == ["790315531400", "790315531402"]
    assert f"https://{OLDER_HOST}/api/apply/v2/jobs/790315531401?domain=acme.com" in sites.urls
    detailed = jobs["790315531401"]
    assert detailed.description.startswith(prose(790315531401 % 997)[:60])
    assert "Three years of research." in detailed.description and not detailed.detail_skipped
    assert (detailed.own_location, detailed.location, detailed.posted_at) == (
        "Helsinki, Finland",  # its record's places, which spell the country out
        "Helsinki, Finland",
        "2026-10-12",
    )

    sites.down = {790315531404}
    jobs = {job.source_id: job for job in S.fetch(client(sites), OLDER_SEARCHED, rank)}
    assert jobs["790315531404"].detail_skipped and not jobs["790315531404"].description  # it waits for a later update
    assert not jobs["790315531403"].detail_skipped and len(sites.asked("detail")) == 5


def test_stage_0_reads_an_older_listing_and_a_record_placing_a_posting_elsewhere_parks_it(repo):
    sites = Sites(
        older=[
            older_position(790315531501, places=("Austin,Texas,United States",)),
            older_position(790315531502),  # in Helsinki, as its listing says: its text is never fetched
            older_position(790315531503, places=(), record=("Espoo,Finland",)),  # only its record names a place
        ]
    )
    root = repo(sources=(OLDER,), criteria={"locations": {"regions": ["US"]}})
    summary = tick(root, sites)
    assert sites.asked("search") == [("UX Researcher", 0)]  # the wanted role, from the criteria
    assert sites.asked("detail") == [790315531503, 790315531501]
    assert (summary["new"], summary["outside_regions"], summary["dropped"]) == (1, 1, {"location": 1})
    state = rows(root, "acme")
    assert sorted(state) == ["790315531501", "790315531503"] and state["790315531501"].status == "P"
    assert state["790315531503"].status == "S" and tag(state["790315531503"], "place") == "FI"
    tick(root, sites)  # the one waiting for triage needs its text again; the parked one and Helsinki's do not
    assert sites.asked("detail") == [790315531501]


@pytest.mark.parametrize(
    "robots, asked, refused",
    [
        ("User-agent: *\nDisallow: /\nAllow: /careers\n", ["robots"], "/api/apply/v2/jobs"),
        (  # the group naming this engine
            "User-agent: automated-job-hunter\nDisallow: /api/apply\n\nUser-agent: *\nAllow: /\n",
            ["robots"],
            "/api/apply/v2/jobs",
        ),
        (  # its listing, but not its texts: refused before any is asked
            "User-agent: *\nDisallow: /\nAllow: /api/apply/v2/jobs?\n",
            ["robots", "search"],
            "/api/apply/v2/jobs/790315531601",
        ),
    ],
)
def test_robots_txt_that_disallows_the_older_api_refuses_the_site_before_it_is_asked(robots, asked, refused):
    sites = Sites(older=[older_position(790315531601), older_position(790315531602)])
    sites.robots = (200, robots)
    message = f"robots.txt on careers.acme.com disallows {refused}, so the site cannot be read"
    with pytest.raises(SourceError, match=re.escape(message)):
        S.fetch(client(sites), OLDER_SEARCHED, lambda job: S.NEW)
    assert [request[0] for request in sites.requests] == asked  # nothing after what was refused


def test_robots_txt_of_an_older_site_that_cannot_be_read_now_reads_nothing_and_none_at_all_disallows_nothing():
    for status, problem in ((403, SourceError), (401, SourceError), (503, HttpError), (429, HttpError)):
        sites = Sites(older=[older_position(790315531701)])
        sites.robots = (status, "")
        with pytest.raises(problem):
            S.fetch(client(sites), OLDER_SEARCHED)
        assert sites.requests == [("robots",)]
    sites.robots = (404, "Not found")  # there is none, so nothing is disallowed (RFC 9309, 2.3.1.3)
    assert [job.source_id for job in S.fetch(client(sites), OLDER_SEARCHED)] == ["790315531701"]


def test_a_source_without_a_layout_reads_the_newer_one_as_before():
    sites = Sites(position(500000000801), position(500000000802), older=[older_position(790315531801)])
    jobs = S.fetch(client(sites), SEARCHED, lambda job: S.NEW)
    assert sorted(job.source_id for job in jobs) == ["500000000801", "500000000802"]
    assert all(not job.detail_skipped and not job.own_location for job in jobs)
    paths = [urllib.parse.urlsplit(url).path for url in sites.urls]  # no robots.txt, nothing under /api/apply
    assert paths == ["/api/pcsx/search", "/api/pcsx/position_details", "/api/pcsx/position_details"]
    sites.urls.clear()
    for bad in ("apply-v3", "pcsx", "", 2):  # a layout this version does not know is not read as the newer one
        with pytest.raises(SourceError, match='"layout" is "apply-v2" for a site in the older layout'):
            S.fetch(client(sites), dict(SEARCHED, layout=bad))
    assert sites.urls == []  # refused before anything is asked
