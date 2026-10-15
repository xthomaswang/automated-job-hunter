"""Eightfold career sites: the JSON their own pages load, read through searches with text fetched on demand."""

import json
import urllib.parse

import pytest
from ajh import registry, scaffold
from ajh import sources as S
from ajh.http import Client
from ajh.model import SourceError
from helpers import full_tick, prose, rows

ACME = {"id": "acme", "company": "Acme", "ats": "eightfold", "host": "acme.eightfold.ai", "domain": "acme.example"}
SEARCHED = dict(ACME, queries=["UX Researcher"])
GLOBEX = "careers.globex.example"  # the same app on a company's own host
OCT_12 = 1791763200  # 2026-10-12T00:00:00Z, as the listing gives it: seconds since the epoch


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


class Sites:
    """Fake Eightfold career sites by host, each with the domain its pages name and its postings.

    A search matches a title containing the query, 10 a page, with its total on every page. A request
    without the site's domain is refused, as there; `down` lists postings whose detail fails.
    """

    def __init__(self, *postings, globex=()):
        self.sites = {
            "acme.eightfold.ai": ("acme.example", {p["row"]["id"]: p for p in postings}),
            GLOBEX: ("globex.example", {p["row"]["id"]: p for p in globex}),
        }
        self.down, self.requests = set(), []

    def transport(self, method, url, headers, body, timeout):
        parts = urllib.parse.urlsplit(url)
        ask = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
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


def test_probe_finds_a_site_by_its_careers_or_posting_url_and_adds_it(tmp_path):
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
