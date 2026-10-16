"""A feed's own readers: modules in its readers/ folder, each reading a source type of its own."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest
from ajh import collect, registry, scaffold
from ajh import judge as judging
from ajh import sources as S
from ajh import tick as ticking
from ajh.cli import main
from ajh.http import Client
from ajh.layout import Layout
from ajh.sources import own
from helpers import (
    CRITERIA,
    GLOBEX,
    REMOTIVE,
    Boards,
    answer_assess,
    answer_triage,
    judge,
    plan,
    posting,
    prose,
    remotive_posting,
    rows,
    tick,
)

SKILL = Path(scaffold.SCRIPTS).parent
ACME = {"id": "acme", "company": "Acme", "ats": "acme-careers"}
INITECH = {"id": "initech", "company": "Initech", "ats": "initech-careers"}


@pytest.fixture(autouse=True)
def fresh_readers(monkeypatch):
    """Each test loads its own feed's readers, and leaves none behind for the next test."""
    monkeypatch.setattr(own, "_loaded", {})
    monkeypatch.setattr(own, "current", own.Loaded())
    yield
    for name in [name for name in sys.modules if name.startswith(own.PACKAGE + ".")]:
        del sys.modules[name]


class Site(Boards):
    """The fake boards, plus career sites only a feed's own reader reads.

    `sites` holds each site's postings by host and id. A site lists them at /api/jobs (with ?q=,
    those whose title contains it) without their text, which /api/jobs/<id> gives.
    """

    def __init__(self, **boards):
        super().__init__(**boards)
        self.sites, self.asked = {}, []

    def searched(self, host):
        return [value for where, kind, value in self.asked if where == host and kind == "search"]

    def texts(self, host):
        return [value for where, kind, value in self.asked if where == host and kind == "detail"]

    def transport(self, method, url, headers, body, timeout):
        parts = urllib.parse.urlsplit(url)
        if parts.hostname not in self.sites:
            return super().transport(method, url, headers, body, timeout)
        postings = self.sites[parts.hostname]
        if parts.path == "/api/jobs":
            query = dict(urllib.parse.parse_qsl(parts.query)).get("q", "")
            self.asked.append((parts.hostname, "search", query))
            rows = [
                {key: p[key] for key in ("id", "title", "location", "posted")}
                for p in postings.values()
                if query.lower() in p["title"].lower()
            ]
            return 200, {}, json.dumps({"total": len(rows), "jobs": rows}).encode()
        jid = parts.path.rsplit("/", 1)[-1]
        self.asked.append((parts.hostname, "detail", jid))
        if jid not in postings:
            return 404, {}, b"{}"
        return 200, {}, json.dumps({"id": jid, "description": f"<p>{postings[jid]['text']}</p>"}).encode()


def site_posting(jid, title="UX Researcher", posted="2026-10-10", location="Remote, US"):
    return {"id": jid, "title": title, "location": location, "posted": posted, "text": prose(sum(map(ord, jid)))}


# A reader of one fake site (see `Site`). With EVERY_TEXT it returns every posting with its text, as
# a reader that is not DETAILED must; otherwise it fetches texts as `rank` asks, as a detailed one
# does. CALLS records the rank each call was given.
READER = """\
from __future__ import annotations

from dataclasses import replace

from ajh.model import Job, SourceError
from ajh.sources import lazy
from ajh.textutil import html_to_text

KIND = "__KIND__"
HOST = "__HOST__"
EVERY_TEXT = __EVERY_TEXT__
CALLS = []
__FLAGS__


def fetch(client, source, rank=None):
    CALLS.append(rank)
    listed = {}
    for query in source.get("queries") or [""]:
        data = client.get(f"https://{HOST}/api/jobs", params={"q": query} if query else None).raise_for_status().json()
        if data["total"] != len(data["jobs"]):
            raise SourceError("the listing is not complete")
        for row in data["jobs"]:
            job = Job(
                key=f"{source['id']}:{row['id']}", source=source["id"], source_id=row["id"],
                company=source.get("company", ""), title=row["title"], location=row["location"],
                url=f"https://{HOST}/jobs/{row['id']}", posted_at=row["posted"],
            )
            listed.setdefault(job.source_id, (job, None))

    def text(job, _):
        data = client.get(f"https://{HOST}/api/jobs/{job.source_id}").raise_for_status().json()
        return replace(job, description=html_to_text(data["description"]))

    if EVERY_TEXT:
        return [text(job, None) for job, _ in listed.values()]
    return lazy.complete(listed, rank, lazy.budget(source, 100), text)
"""

# A reader with a fixed board, for the engine copy run as its own process, which must not go to
# the network.
STATIC = """\
from ajh.model import Job

KIND = "acme-careers"
HOSTS = ("careers.acme.example",)


def fetch(client, source, rank=None):
    return [
        Job(key=f"{source['id']}:{jid}", source=source["id"], source_id=jid, company=source["company"],
            title="UX Researcher", location="Remote, US", url=f"https://careers.acme.example/jobs/{jid}",
            description="Plan and run studies.")
        for jid in ("A1", "A2")
    ]
"""


def reader(kind, host="careers.acme.example", flags="", every_text=True):
    text = READER.replace("__KIND__", kind).replace("__HOST__", host)
    return text.replace("__EVERY_TEXT__", str(every_text)).replace("__FLAGS__", flags)


def documented_reader():
    """The skeleton in references/own-readers.md, exactly as published."""
    return re.search(r"```python\n(.*?)```", (SKILL / "references" / "own-readers.md").read_text(), re.S).group(1)


def make_feed(root, sources, readers=None, criteria=None):
    """A feed repo at `root` with these sources, and these modules (file name -> text) in readers/."""
    for folder in ("config", "profile"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "config" / "sources.json").write_text(json.dumps(list(sources)))
    (root / "profile" / "criteria.json").write_text(json.dumps(dict(CRITERIA, **(criteria or {}))))
    (root / "profile" / "profile.md").write_text("Five years of mixed-methods research.")
    for name, text in (readers or {}).items():
        path = root / "readers" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def engine(root, *args, env=None):
    """The feed's own engine copy, run as the workflow runs it."""
    done = subprocess.run(
        [sys.executable, ".hunter/engine/hunter.py", *args], cwd=root, capture_output=True, text=True, env=env
    )
    return done.returncode, json.loads(done.stdout)


def test_the_documented_reader_is_read_through_collect_like_a_built_in_one(tmp_path):
    root = make_feed(tmp_path, [dict(ACME, max_details=1), GLOBEX], {"acme.py": documented_reader()})
    site = Site(globex=[posting(1)])
    site.sites["careers.acme.example"] = {jid: site_posting(jid) for jid in ("A1", "A2")}
    summary = tick(root, site)
    assert summary["sources"] == {"fetched": 2, "gated": 0, "failed": 0, "failures": {}}
    assert S.load_own(root).report() == {"kinds": {"acme-careers": "readers/acme.py"}, "errors": {}}
    assert site.texts("careers.acme.example") == ["A2"]  # one text an update (max_details), newest first
    assert set(rows(root, "acme")) == {"A2"} and summary["waiting_for_text"] == 1  # A1 waits for its text
    assert plan(root)["queue"]["triage"] == ["acme:A2", "globex:1"]
    read = json.loads((Layout(root).snapshot / "acme.json").read_text())["A2"]
    assert read["description"] == site.sites["careers.acme.example"]["A2"]["text"]
    assert (read["url"], read["company"], read["source_kind"]) == (
        "https://careers.acme.example/jobs/A2",
        "Acme",
        "official",
    )
    judge(root, "acme", "A2", "D")  # judged, so its text is not needed again
    tick(root, site)
    assert site.texts("careers.acme.example") == ["A2", "A1"] and set(rows(root, "acme")) == {"A1", "A2"}


def test_a_query_scoped_reader_searches_for_the_wanted_roles_unless_its_entry_names_queries(tmp_path):
    roles = {"targets": {"roles": ["UX Researcher"], "adjacent": ["Design Researcher"]}}
    searched = reader("acme-careers", flags="QUERY_SCOPED = True", every_text=False)
    unranked = reader("initech-careers", "jobs.initech.example", "QUERY_SCOPED = True\nDETAILED = False", True)
    root = make_feed(
        tmp_path, [ACME, dict(INITECH, queries=["Analyst"])], {"acme.py": searched, "initech.py": unranked}, roles
    )
    site = Site()
    site.sites["careers.acme.example"] = {
        "A1": site_posting("A1"),
        "A2": site_posting("A2", "Design Researcher"),
        "A3": site_posting("A3", "Accountant"),
    }
    site.sites["jobs.initech.example"] = {"I1": site_posting("I1", "Data Analyst"), "I2": site_posting("I2")}
    tick(root, site)
    assert site.searched("careers.acme.example") == ["UX Researcher", "Design Researcher"]
    assert site.searched("jobs.initech.example") == ["Analyst"]  # the entry's own queries
    assert set(rows(root, "acme")) == {"A1", "A2"} and set(rows(root, "initech")) == {"I1"}
    assert S.query_scoped("acme-careers") and S.detailed("acme-careers")  # DETAILED follows QUERY_SCOPED
    assert S.query_scoped("initech-careers") and not S.detailed("initech-careers")  # unless it says otherwise
    acme, initech = (own.current.readers[kind].module for kind in ("acme-careers", "initech-careers"))
    assert callable(acme.CALLS[0]) and initech.CALLS == [None]


def test_a_detailed_reader_gets_a_rank_and_each_posting_it_lists_is_held_to_the_backfill_limit(tmp_path):
    readers = {
        "acme.py": reader("acme-careers", flags="DETAILED = True", every_text=False),
        "initech.py": reader("initech-careers", "jobs.initech.example"),
    }
    root = make_feed(tmp_path, [ACME, INITECH], readers, {"backfill": {"days": 45}})
    site = Site()
    site.sites["careers.acme.example"] = {"A1": site_posting("A1"), "A2": site_posting("A2", posted="2025-01-15")}
    site.sites["jobs.initech.example"] = {"I1": site_posting("I1"), "I2": site_posting("I2", posted="2025-01-15")}
    summary = tick(root, site)
    assert summary["parked"] == 2  # the first read parks old postings on every source
    assert {jid: row.status for jid, row in rows(root, "acme").items()} == {"A1": "P", "A2": "S"}
    assert {jid: row.status for jid, row in rows(root, "initech").items()} == {"I1": "P", "I2": "S"}
    site.sites["careers.acme.example"]["A3"] = site_posting("A3", posted="2025-02-01")
    site.sites["jobs.initech.example"]["I3"] = site_posting("I3", posted="2025-02-01")
    tick(root, site)
    assert rows(root, "acme")["A3"].status == "S"  # a detail source's postings arrive over several updates
    assert rows(root, "initech")["I3"].status == "P"  # elsewhere only the first read parks
    acme, initech = (own.current.readers[kind].module for kind in ("acme-careers", "initech-careers"))
    assert len(acme.CALLS) == 2 and all(callable(rank) for rank in acme.CALLS)
    assert initech.CALLS == [None, None]  # a reader that is not detailed is never given a rank


def test_sources_hosts_lists_what_the_readers_name_and_which_sources_it_cannot_list(tmp_path, capsys):
    named = "def hosts(source):\n    return [source['host'], 'cdn.initech.example']"
    root = make_feed(
        tmp_path,
        [
            ACME,
            dict(INITECH, host="jobs.initech.example"),
            dict(INITECH, id="initech-old"),
            GLOBEX,
            {"id": "hooli", "company": "Hooli", "ats": "hooli-careers"},
            {"id": "globex-own", "company": "Globex", "ats": "globex-careers"},
        ],
        {
            "acme.py": reader("acme-careers", flags='HOSTS = ("careers.acme.example",)'),
            "initech.py": reader("initech-careers", flags=named),
            "hooli.py": "raise RuntimeError('half written')\n",
            "globex.py": reader("globex-careers", "jobs.globex.example"),  # it names no hosts
        },
    )
    expected = {
        "hosts": ["boards-api.greenhouse.io", "careers.acme.example", "cdn.initech.example", "jobs.initech.example"],
        "unlisted": {
            "hooli": "No reader for source type 'hooli-careers'; readers/hooli.py failed to load: RuntimeError: half written",
            "initech-old": "readers/initech.py: hosts() failed: KeyError: 'host'",
            "globex-own": "readers/globex.py names no hosts: set HOSTS, or a hosts(source) function",
        },
    }
    assert registry.hosts(root) == expected
    assert main(["--repo", str(root), "sources", "hosts"]) == 0 and json.loads(capsys.readouterr().out) == expected


def test_a_reader_that_breaks_fails_only_its_own_sources_which_keep_their_last_snapshot(tmp_path, capsys):
    readers = {"acme.py": reader("acme-careers"), "initech.py": reader("initech-careers", "jobs.initech.example")}
    first = make_feed(tmp_path / "first", [ACME, INITECH, GLOBEX], readers)
    site = Site(globex=[posting(1)])
    site.sites["careers.acme.example"] = {"A1": site_posting("A1")}
    site.sites["jobs.initech.example"] = {"I1": site_posting("I1")}
    tick(first, site)
    assert (set(rows(first, "acme")), set(rows(first, "initech"))) == ({"A1"}, {"I1"})
    broken = tmp_path / "broken"  # the same feed after its readers broke (a new root: each loads once)
    shutil.copytree(first, broken)
    (broken / "readers/acme.py").write_text('KIND = "acme-careers"\ndef fetch(client, source, rank=None)\n')
    (broken / "readers/initech.py").write_text('KIND = "initech-careers"\n')
    site.jobs["globex"][2] = posting(2)
    before = {sid: rows(broken, sid) for sid in ("acme", "initech")}
    for _ in range(3):
        summary = tick(broken, site)
    assert (summary["sources"]["fetched"], summary["sources"]["failed"]) == (1, 2)
    failures = summary["sources"]["failures"]
    assert failures["acme"].startswith(
        "SourceError: No reader for source type 'acme-careers'; readers/acme.py failed to load: SyntaxError"
    )
    assert failures["initech"] == "SourceError: readers/initech.py has no fetch function"
    assert {sid: rows(broken, sid) for sid in before} == before  # nothing missed, nothing closed
    assert set(rows(broken, "globex")) == {"1", "2"}  # every other source reads as before
    info = json.loads((Layout(broken).state / "sources.json").read_text())
    assert info["acme"]["error"] == info["initech"]["error"] == "SourceError" and "error" not in info["globex"]

    tested = registry.test(broken, Client(site.transport, sleep=lambda s: None, retries=0))
    assert tested["failing"] == ["acme", "initech"] and tested["sources"]["globex"] == {"postings": 2}
    assert tested["readers"]["kinds"] == {}
    assert tested["readers"]["errors"]["readers/initech.py"] == "has no fetch function"
    assert main(["--repo", str(broken), "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["readers"] == tested["readers"]
    assert status["readers"]["errors"]["readers/acme.py"].startswith("failed to load: SyntaxError")


def test_a_reader_may_not_take_a_built_in_type_or_one_another_reader_declares(tmp_path):
    readers = {
        "greenhouse.py": reader("greenhouse"),
        "acme.py": reader("acme-careers"),
        "acme_copy.py": reader("acme-careers"),
        "odd.py": reader("Odd Careers"),
        "initech.py": reader("initech-careers", "jobs.initech.example"),
    }
    odd = {"id": "odd", "company": "Odd", "ats": "Odd Careers"}
    root = make_feed(tmp_path / "feed", [GLOBEX, REMOTIVE, ACME, odd, INITECH], readers)
    site = Site(globex=[posting(1)])
    site.remotive = [remotive_posting(5)]
    site.sites["careers.acme.example"] = {"A1": site_posting("A1")}
    site.sites["jobs.initech.example"] = {"I1": site_posting("I1")}
    summary = tick(root, site)
    assert summary["sources"]["failures"] == {
        "globex": "SourceError: readers/greenhouse.py declares KIND 'greenhouse', a built-in source type; give the "
        "reader a type of its own",
        "acme": "SourceError: readers/acme.py and readers/acme_copy.py both declare KIND 'acme-careers'",
        "odd": "SourceError: readers/odd.py declares KIND 'Odd Careers', which is not a lowercase letter followed by "
        "up to 39 lowercase letters, digits or hyphens",
    }
    assert summary["sources"]["fetched"] == 2 and set(rows(root, "initech")) == {"I1"}  # the rest read as before
    assert S.load_own(root).report()["errors"]["readers/acme_copy.py"] == (
        "declares KIND 'acme-careers', as readers/acme.py also does"
    )
    # With every reader fine, a type that none of them declares is still a mistake in the configuration.
    other = make_feed(tmp_path / "other", [dict(INITECH, ats="initech-carers")], {"initech.py": readers["initech.py"]})
    with pytest.raises(collect.ConfigError, match="unknown source type 'initech-carers'"):
        collect.run(other, Client(site.transport, retries=0))


SHADOW = """\
import json
import sys

from ajh.model import Job

KIND = "json-site"
PATH = list(sys.path)
FOUND = json


def fetch(client, source, rank=None):
    data = json.loads(client.get("https://careers.acme.example/api/jobs").text)
    return [
        Job(key=f"{source['id']}:{row['id']}", source=source["id"], source_id=row["id"], company="Acme",
            title=row["title"], location=row["location"], url=f"https://careers.acme.example/jobs/{row['id']}",
            description="Plan and run studies.")
        for row in data["jobs"]
    ]
"""


def test_a_reader_named_like_a_standard_module_hides_nothing(tmp_path):
    root = make_feed(tmp_path, [dict(ACME, ats="json-site")], {"json.py": SHADOW})
    path = list(sys.path)
    site = Site()
    site.sites["careers.acme.example"] = {"A1": site_posting("A1")}
    summary = tick(root, site)
    assert summary["sources"]["fetched"] == 1 and set(rows(root)) == {"A1"}  # the file json.py is a reader
    module = sys.modules[f"{own.PACKAGE}.json"]
    folder = (root / "readers").resolve()
    assert Path(module.__file__).resolve() == folder / "json.py"
    assert sys.path == path and str(folder) not in module.PATH  # the folder is never on the path
    assert module.FOUND is json and sys.modules["json"] is json  # its `import json` is the standard library's
    assert Path(importlib.util.find_spec("json").origin).parent != folder


def test_readers_are_loaded_by_path_in_order_once_and_only_from_python_files(tmp_path, monkeypatch):
    marker = tmp_path / "loaded"
    note = f'\nwith open({str(marker)!r}, "a") as handle:\n    handle.write(__name__ + "\\n")\n'
    shaped = "\nfrom dataclasses import dataclass\n\n\n@dataclass\nclass Row:\n    id: str\n    title: str\n"
    never = note + "raise RuntimeError('never loaded')\n"
    root = make_feed(
        tmp_path / "feed",
        [ACME],
        {
            "zeta.py": reader("zeta-careers") + note,
            "acme.py": reader("acme-careers") + shaped + note,  # a dataclass in a reader works
            "_draft.py": never,
            ".hidden.py": never,
            "notes.txt": never,
            "acme.py.orig": never,
            "old/acme.py": never,
        },
    )
    monkeypatch.setattr(sys, "dont_write_bytecode", False)  # as for any caller: still nothing written to the feed
    loaded = S.load_own(root)
    assert S.load_own(root) is loaded  # once per process
    assert marker.read_text().split() == [f"{own.PACKAGE}.acme", f"{own.PACKAGE}.zeta"]
    assert loaded.report() == {
        "kinds": {"acme-careers": "readers/acme.py", "zeta-careers": "readers/zeta.py"},
        "errors": {},
    }
    assert sys.modules[f"{own.PACKAGE}.acme"] is loaded.readers["acme-careers"].module
    assert not list((root / "readers").rglob("__pycache__")) and sys.dont_write_bytecode is False


def test_a_reader_whose_postings_are_not_its_sources_own_fails_alone(tmp_path):
    def module(kind, result):
        return (
            f'from ajh.model import Job\n\nKIND = "{kind}"\n\n\ndef fetch(client, source, rank=None):\n'
            f'    job = Job(key=source["id"] + ":1", source=source["id"], source_id="1", company="Acme", '
            f'title="UX Researcher", location="Remote, US", url="https://careers.acme.example/jobs/1")\n'
            f"    return {result}\n"
        )

    readers = {
        "elsewhere.py": module(
            "elsewhere-careers", '[job.__class__(**dict(job.to_dict(), key="acme:1", source="acme"))]'
        ),
        "twice.py": module("twice-careers", "[job, job]"),
        "stream.py": module("stream-careers", "(each for each in [job])"),
    }
    configured = [
        {"id": kind.split("-")[0], "ats": kind} for kind in ("elsewhere-careers", "twice-careers", "stream-careers")
    ]
    root = make_feed(tmp_path, [*configured, GLOBEX], readers)
    summary = tick(root, Site(globex=[posting(1)]))
    assert summary["sources"]["failures"] == {
        "elsewhere": "SourceError: readers/elsewhere.py returned something other than a posting of source 'elsewhere'",
        "twice": "SourceError: readers/twice.py returned posting '1' twice",
        "stream": "SourceError: readers/stream.py returned generator, not a list of postings",
    }
    assert summary["sources"]["fetched"] == 1 and set(rows(root, "globex")) == {"1"}


BOOM = """\
with open({marker!r}, "a") as handle:  # what any process that runs this file leaves behind
    handle.write("ran\\n")
raise RuntimeError("feed code ran")
"""


def test_judging_runs_no_code_from_the_feed(tmp_path):
    marker, root = tmp_path / "ran", tmp_path / "feed"
    scaffold.init(root)
    make_feed(root, [dict(ACME, ats="boom-careers"), GLOBEX], {"boom.py": BOOM.format(marker=str(marker))})
    layout = Layout(root)
    packets = [
        f"T-00{n} | .hunter/work/packets/T-00{n}.md | .hunter/work/results/T-00{n}.txt | hunter-triage" for n in (1, 2)
    ]
    layout.work.mkdir(parents=True)
    header = "# Judging manifest: triage\n\npacket | packet file | result file | agent\n"
    layout.manifest.write_text(header + "\n".join(packets) + "\n")  # as collect leaves it for the judging step

    def answer(repo, manifest):
        for row in (repo / manifest).read_text().splitlines()[1:]:
            (Layout(repo).results / f"{row.split(' | ')[0]}.txt").write_text("answered\n")
        return {"ok": True, "cost_usd": 0.0, "denied": 0}

    assert judging.run(root, answer)["results"] == 2
    # The judging step as the workflow runs it, in a process of its own, with no Claude Code to find.
    nowhere = {"PATH": str(tmp_path / "no-bin"), "PYTHONDONTWRITEBYTECODE": "1"}
    shutil.rmtree(layout.results)
    code, report = engine(root, "judge", env=nowhere)
    assert (code, report["packets"], report["results"]) == (1, 2, 0) and "FileNotFoundError" in report["failures"]
    assert not marker.exists()  # neither judged with the feed's readers loaded
    summary = tick(root, Site(globex=[posting(1)]))  # whereas collect does load them
    assert marker.read_text() == "ran\n" and summary["sources"]["fetched"] == 1
    assert summary["sources"]["failures"]["acme"].endswith(
        "readers/boom.py failed to load: RuntimeError: feed code ran"
    )


def test_init_and_upgrade_leave_the_readers_alone_and_the_engine_copy_reads_with_them(tmp_path):
    root = tmp_path / "feed"
    scaffold.init(root)
    files = {
        "acme.py": STATIC,
        "_shared.py": "raise RuntimeError('never loaded')\n",
        "notes.md": "What each reader reads.\n",
        "old/acme.py": "raise RuntimeError('never loaded')\n",
    }
    make_feed(root, [ACME], files)
    (root / "readers" / ".keep").write_text("")

    def contents():
        found = sorted(p for p in (root / "readers").rglob("*") if p.is_file())
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in found}

    before = contents()
    scaffold.init(root, "8h")  # init run again on the feed repo
    workflow = root / scaffold.WORKFLOW
    cron = re.search(r'cron: "([^"]+)"', workflow.read_text()).group(1)
    workflow.write_text(workflow.read_text().replace(cron, "8 2,12 * * 1-5"))  # a schedule edited by hand
    scaffold.upgrade(root)
    assert contents() == before and len(before) == 5
    text = workflow.read_text()
    assert 'cron: "8 2,12 * * 1-5"' in text and 'paths: ["profile/**", "config/**", "readers/**"]' in text
    # The engine copy, run as the workflow runs it, imports `ajh` for the reader and reads with it.
    assert engine(root, "sources", "hosts") == (0, {"hosts": ["careers.acme.example"]})
    code, tested = engine(root, "sources", "test")
    assert code == 0 and tested == {
        "sources": {"acme": {"postings": 2}},
        "failing": [],
        "readers": {"kinds": {"acme-careers": "readers/acme.py"}, "errors": {}},
    }
    assert contents() == before and not list(root.rglob("__pycache__"))


def test_a_committed_readers_folder_passes_apply_and_publish(tmp_path, monkeypatch):
    root = tmp_path / "feed"
    scaffold.init(root)
    make_feed(root, [ACME], {"acme.py": documented_reader()})
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@users.noreply.github.com", "commit", "-q", "-m", "Set up")

    def scripted(repo):
        stage = (repo / ".hunter/work/manifest.md").read_text().splitlines()[0].split(": ")[1]
        (answer_triage if stage == "triage" else answer_assess)(repo)
        return {"ok": True}

    monkeypatch.setattr(ticking, "judge", scripted)
    site = Site()
    site.sites["careers.acme.example"] = {jid: site_posting(jid) for jid in ("A1", "A2")}
    summary = ticking.run(root, push=False, client=Client(site.transport, sleep=lambda s: None, retries=0))
    assert summary["apply"]["published"] == 2 and summary["publish"]["state"] == "committed"
    assert git(root, "status", "--porcelain", "--untracked-files=all") == ""  # the tree stayed pristine
    committed = git(root, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "jobs/acme/A1.md" in committed and not [path for path in committed if path.startswith("readers/")]
