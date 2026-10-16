"""Creating, upgrading and running a feed repo from the skill's own files."""

import json
import re
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from ajh import feed, publish, registry, scaffold, tick
from ajh.cli import main
from ajh.http import Client
from ajh.model import Job, SourceError
from helpers import Boards, answer_assess, answer_triage, ashby_posting, lever_posting, posting, prose, wd_posting

SKILL = Path(scaffold.SCRIPTS).parent
REPO = SKILL.parent.parent


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def engine(root, *args):
    done = subprocess.run([sys.executable, ".hunter/engine/hunter.py", *args], cwd=root, capture_output=True, text=True)
    return done.returncode, json.loads(done.stdout)


def test_init_creates_a_feed_repo_that_runs_on_its_own_engine_copy(tmp_path):
    root = tmp_path / "job-feed"
    result = scaffold.init(root, "8h")
    assert result["cadence"] == "8h" and (root / ".git").is_dir()
    for path in ("AGENTS.md", "CLAUDE.md", "HANDOFF.md", ".gitignore", ".hunter/RUNBOOK.md", ".hunter/VERSION",
                 ".hunter/agents.json", ".hunter/engine/hunter.py", ".hunter/engine/judge.sh", ".hunter/engine/ajh/cli.py",
                 ".hunter/engine/ajh/data/places.json", ".claude/agents/hunter-triage.md", ".claude/agents/hunter-assess.md",
                 ".github/workflows/job-hunter.yml", "config/sources.json"):  # fmt: skip
        assert (root / path).is_file(), path
    workflow = (root / ".github/workflows/job-hunter.yml").read_text()
    assert re.search(r'cron: "\d{1,2} \*/8 \* \* \*"', workflow) and "__" not in workflow
    assert f"@anthropic-ai/claude-code@{scaffold.claude_code_version()}" in workflow
    assert ".hunter/work/" in (root / ".gitignore").read_text() and (root / "config/sources.json").read_text() == "[]\n"
    code, status = engine(root, "status")  # the copy runs with nothing but Python
    assert code == 0 and status["engine"] == {
        "vendored": True,
        "engine_version": scaffold.__version__,
        "intact": True,
        "changed": [],
    }
    assert engine(root, "collect") == (
        2,
        {"error": "CriteriaError", "detail": f"{root.resolve()}/profile/criteria.json is missing", "needs": "a person"},
    )
    assert not list(root.rglob("__pycache__"))  # running the copy leaves no stray files in the repo
    git(root, "add", "-A")
    assert git(root, "status", "--porcelain", "--ignored").count("!!") == 0


def test_init_refuses_a_directory_that_holds_something_else(tmp_path):
    (tmp_path / "thesis.tex").write_text("unrelated work")
    with pytest.raises(scaffold.ScaffoldError, match="not empty"):
        scaffold.init(tmp_path)
    assert main(["--repo", str(tmp_path), "init"]) == 2 and not (tmp_path / ".hunter").exists()
    empty = tmp_path / "new"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(empty)], check=True)
    assert scaffold.init(empty)["engine_version"] == scaffold.__version__  # an empty git repo is fine


def test_upgrade_replaces_the_engine_and_leaves_the_users_files_and_schedule_alone(tmp_path):
    root = tmp_path / "feed"
    scaffold.init(root, "1h")
    cron = re.search(r'cron: "([^"]+)"', (root / ".github/workflows/job-hunter.yml").read_text()).group(1)
    (root / "profile").mkdir()
    (root / "profile/profile.md").write_text("mine")
    (root / "config/sources.json").write_text(
        '[{"id": "acme", "company": "Acme", "ats": "greenhouse", "slug": "acme"}]\n'
    )
    (root / ".hunter/engine/ajh/cli.py").write_text("tampered")
    (root / ".hunter/engine/ajh/leftover.py").write_text("stale file from an older version")
    (root / ".hunter/VERSION").write_text("0.0.1\n")
    assert scaffold.verify(root)["changed"] == ["engine/ajh/cli.py"]
    result = scaffold.upgrade(root)
    assert (result["from"], result["to"]) == ("0.0.1", scaffold.__version__) and scaffold.verify(root)["intact"]
    assert not (root / ".hunter/engine/ajh/leftover.py").exists()
    assert (root / "profile/profile.md").read_text() == "mine" and "acme" in (root / "config/sources.json").read_text()
    assert f'cron: "{cron}"' in (root / ".github/workflows/job-hunter.yml").read_text()
    scaffold.upgrade(root, "4h")
    assert re.search(r'cron: "\d{1,2} \*/4 \* \* \*"', (root / ".github/workflows/job-hunter.yml").read_text())
    with pytest.raises(scaffold.ScaffoldError, match="not a feed repo"):
        scaffold.upgrade(tmp_path / "elsewhere")


def test_a_cadence_can_be_set_utc_hours_and_a_bad_one_changes_nothing(tmp_path):
    root = tmp_path / "feed"
    assert main(["--repo", str(root), "init", "--cadence", "20,6,14,10"]) == 0
    path = root / scaffold.WORKFLOW
    minute = re.search(r'cron: "(\d{1,2}) 6,10,14,20 \* \* \*"', path.read_text()).group(1)
    assert 3 <= int(minute) <= 20  # set times stay early in their hour
    assert scaffold.cron_for("8h", 7) == "7 */8 * * *" and scaffold.cron_for("14, 2,14", 7) == "7 2,14 * * *"
    for bad in ("25", "6,,10", "", "every 4h", "6.5", "-1", "2h"):
        with pytest.raises(scaffold.ScaffoldError, match="UTC hours"):
            scaffold.cron_for(bad, 7)
    assert main(["--repo", str(tmp_path / "other"), "init", "--cadence", "24"]) == 2
    assert not (tmp_path / "other").exists()  # refused before anything was created
    path.write_text(
        path.read_text().replace(re.search(r'cron: "([^"]+)"', path.read_text()).group(1), "8 2,12 * * 1-5")
    )
    before = path.read_text()
    assert main(["--repo", str(root), "upgrade", "--cadence", "7,31"]) == 2 and path.read_text() == before
    scaffold.upgrade(root)
    assert 'cron: "8 2,12 * * 1-5"' in path.read_text()  # a hand-edited schedule survives an upgrade
    scaffold.upgrade(root, "0,12")
    assert re.search(r'cron: "\d{1,2} 0,12 \* \* \*"', path.read_text())


def test_upgrade_with_a_cadence_always_writes_a_new_cron_line(tmp_path, monkeypatch):
    root = tmp_path / "feed"
    path = root / scaffold.WORKFLOW
    monkeypatch.setattr(scaffold.random, "randint", lambda low, high: high)  # every draw is the same minute
    scaffold.init(root, "6,18")
    assert 'cron: "20 6,18 * * *"' in path.read_text()
    scaffold.upgrade(root, "6,18")
    assert 'cron: "3 6,18 * * *"' in path.read_text()  # the next minute in the range, wrapping around
    scaffold.upgrade(root, "4h")
    assert 'cron: "57 */4 * * *"' in path.read_text()  # a different line already, so the draw stands


@pytest.fixture
def no_identity(tmp_path, monkeypatch):
    """Git as on a machine where nobody ever configured it: no identity in any config or variable."""
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(name, raising=False)


def test_a_feed_repo_refuses_commits_under_an_address_git_would_make_up(tmp_path, no_identity):
    root = tmp_path / "feed"
    (warning,) = scaffold.init(root)["warnings"]
    assert "git config user.email" in warning and "noreply" in warning
    git(root, "add", "-A")
    refused = subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "Set up"], capture_output=True, text=True)
    assert refused.returncode and "auto-detection is disabled" in refused.stderr  # not user@laptop.local
    git(root, "config", "user.name", "Owner")
    git(root, "config", "user.email", "1+owner@users.noreply.github.com")
    git(root, "commit", "-q", "-m", "Set up")
    assert "warnings" not in scaffold.upgrade(root)
    owner = scaffold.schedule(root)
    assert (owner["author"], owner["committer"]) == ("1+owner@users.noreply.github.com",) * 2 and "warning" not in owner


def test_status_names_the_commit_that_set_the_schedule_and_flags_a_made_up_address(tmp_path, no_identity, capsys):
    root = tmp_path / "feed"
    scaffold.init(root, "4h")
    assert scaffold.schedule(root)["changed_in"] is None  # nothing committed yet
    git(root, "add", "-A")
    git(root, "-c", "user.name=me", "-c", "user.email=me@laptop.local", "commit", "-q", "-m", "Set up")
    first = git(root, "rev-parse", "HEAD")
    assert main(["--repo", str(root), "status"]) == 0
    printed = capsys.readouterr().out
    owner = json.loads(printed)["schedule"]
    assert (owner["changed_in"], owner["author"], owner["committer"]) == (first, scaffold.MADE_UP, scaffold.MADE_UP)
    assert first[:12] in owner["warning"] and "upgrade --cadence" in owner["warning"]
    upgraded = scaffold.upgrade(root)
    assert owner["warning"] in upgraded["warnings"]  # an upgrade says so too
    assert "laptop" not in printed + json.dumps(upgraded)  # described, never printed: it names the machine
    for made_up in ("me@laptop", "me@host.(none)", "me@laptop.LOCAL"):
        assert scaffold._made_up(made_up)
    assert not scaffold._made_up("1+owner@users.noreply.github.com") and not scaffold._made_up("me@example.com")

    git(root, "config", "user.name", "Owner")
    git(root, "config", "user.email", "1+owner@users.noreply.github.com")
    scaffold.upgrade(root, "4h")
    git(root, "commit", "-qam", "Schedule from my account")
    fixed = scaffold.schedule(root)
    assert (fixed["changed_in"], fixed["author"]) == (
        git(root, "rev-parse", "HEAD"),
        "1+owner@users.noreply.github.com",
    )
    assert "warning" not in fixed
    path = root / scaffold.WORKFLOW
    path.write_text(path.read_text().replace("timeout-minutes: 45", "timeout-minutes: 50"))
    git(root, "-c", "user.email=me@laptop.local", "commit", "-qam", "Longer timeout")
    assert scaffold.schedule(root) == fixed  # it follows the cron line, not other edits to the workflow


def test_the_three_forms_of_the_subagent_definitions_agree():
    agents = scaffold.load_agents()
    assert set(agents) == {"hunter-triage", "hunter-assess"}
    assert (agents["hunter-triage"]["model"], agents["hunter-assess"]["model"]) == ("haiku", "sonnet")
    for name, spec in agents.items():
        assert spec["tools"] == ["Read", "Write"]  # a judging subagent can do nothing else
        assert (REPO / "agents" / f"{name}.md").read_text() == scaffold.agent_markdown(name, spec)


def test_a_whole_tick_runs_from_a_scaffolded_repo(tmp_path, monkeypatch, no_identity):
    root = tmp_path / "feed"
    scaffold.init(root)
    registry.add(root, ["greenhouse:acme:Acme"])
    (root / "profile").mkdir()
    (root / "profile/criteria.json").write_text('{"schema_version": 1, "targets": {"roles": ["UX Researcher"]}}')
    (root / "profile/profile.md").write_text("Three years of user research.")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@users.noreply.github.com", "commit", "-q", "-m", "Set up")
    stages = []

    def scripted(repo):
        stage = (repo / ".hunter/work/manifest.md").read_text().splitlines()[0].split(": ")[1]
        stages.append(stage)
        (answer_triage if stage == "triage" else answer_assess)(repo)
        return {"ok": True}

    monkeypatch.setattr(tick, "judge", scripted)
    board = Boards(acme=[posting(1), posting(2)])
    summary = tick.run(root, push=False, client=Client(board.transport, sleep=lambda s: None, retries=0))
    assert stages == ["triage", "assess"] and summary["apply"]["published"] == 2
    assert summary["publish"]["state"] == "committed" and git(root, "status", "--porcelain") == ""
    assert "Job-Hunter-Publish: auto" in git(root, "log", "-1", "--format=%B")
    assert git(root, "log", "-1", "--format=%ae") == publish.FALLBACK_IDENTITY[1]  # as on a runner, with no identity
    quiet = tick.run(root, push=False, client=Client(board.transport, sleep=lambda s: None, retries=0))
    assert (
        stages == ["triage", "assess"] and quiet["publish"]["state"] == "unchanged"
    )  # nothing to judge, nothing to commit


def test_sources_are_added_from_the_registry_or_from_a_probed_board(tmp_path):
    scaffold.init(tmp_path / "feed")
    root = tmp_path / "feed"
    known = registry.listing()
    assert len(known["boards"]) >= 40 and {"id": "figma", "company": "Figma", "ats": "greenhouse"} in known["boards"]
    result = registry.add(root, ["figma", "remotive", "lever:initech:Initech Corp", "nonesuch"])
    assert result == {"added": ["figma", "remotive", "initech"], "unknown": ["nonesuch"], "configured": 3}
    configured = json.loads((root / "config/sources.json").read_text())
    assert [s["id"] for s in configured] == ["figma", "initech", "remotive"]
    assert configured[1] == {"id": "initech", "company": "Initech Corp", "ats": "lever", "slug": "initech"}
    assert registry.add(root, ["figma"])["added"] == [] and registry.remove(root, ["initech"]) == {
        "removed": 1,
        "configured": 2,
    }
    assert registry.hosts(root) == {"hosts": ["boards-api.greenhouse.io", "remotive.com"]}
    with pytest.raises(SourceError):
        registry.add(root, ["greenhouse:../etc:Bad"])
    assert main(["--repo", str(root), "sources", "add", "greenhouse:a b:Bad"]) == 2


def test_probe_finds_a_companys_board_by_trying_likely_slugs():
    assert registry.slug_candidates("Acme Rocket Co.") == ["acmerocket", "acme-rocket", "acme"]

    def transport(method, url, headers, body, timeout):
        if url == "https://api.lever.co/v0/postings/acme-rocket?mode=json":
            row = {
                "id": "a1",
                "text": "Propulsion Engineer",
                "hostedUrl": "https://jobs.lever.co/acme-rocket/a1",
                "description": "Build engines.",
            }
            return 200, {}, json.dumps([row]).encode()
        return 404, {}, b"{}"

    result = registry.probe("Acme Rocket Co.", Client(transport, retries=0))
    assert result["matches"] == [{"ats": "lever", "slug": "acme-rocket", "postings": 1, "sample": ["Propulsion Engineer"],
                                  "add": "lever:acme-rocket:Acme Rocket Co.", "confidence": "low",
                                  "warning": f"Only 1 posting, and {SMALL}"}]  # fmt: skip
    assert registry.probe("Nobody Inc", Client(transport, retries=0))["matches"] == []


SMALL = "a board this small is often another company's with the same name."
PART = "Its slug matches only part of the name asked for."
DOUBT = (
    "A match with a warning may be another company's board. Check that its sample looks like this company's "
    "postings before adding it; if you cannot tell, find the company's careers page, or the link of one of its "
    "postings, and run `sources probe <url> <Company Name>` instead."
)


def serving(replies):
    """A client whose transport answers each URL given with its JSON, and any other with 404."""

    def transport(method, url, headers, body, timeout):
        reply = replies.get(url)
        return (404, {}, b"{}") if reply is None else (200, {}, json.dumps(reply).encode())

    return Client(transport, retries=0)


def offer(jid, company, location="Remote"):
    """A posting as Recruitee's API lists it, which names its company."""
    return {"id": jid, "title": "UX Researcher", "careers_url": f"https://acme.recruitee.com/o/{jid}",
            "description": f"<p>{prose(jid)}</p>", "company_name": company, "location": location}  # fmt: skip


def test_a_name_probe_says_how_sure_it_is_of_each_board_and_warns_of_another_companys():
    web = serving(
        {
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": {
                "jobs": [dict(posting(n), company_name="Acme, Inc.") for n in (1, 2, 3, 4, 5)]
            },
            "https://api.lever.co/v0/postings/acme?mode=json": [lever_posting("a1", "acme", "Social Media Manager")],
            "https://acme.recruitee.com/api/offers/": {"offers": [offer(n, "Initech") for n in (1, 2, 3, 4, 5)]},
            "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=0": {
                "offset": 0,
                "limit": 100,
                "totalFound": 1,
                "content": [{"id": "9", "name": "Test UAT", "company": {"identifier": "acme", "name": "Globex"}}],
            },
        }
    )
    result = registry.probe("Acme", web)
    rated = [(m["ats"], m["postings"], m["confidence"], m.get("warning")) for m in result["matches"]]
    assert rated == [  # every board that answered, none dropped
        ("greenhouse", 5, "high", None),  # its postings name the company asked for
        ("lever", 1, "low", f"Only 1 posting, and {SMALL}"),  # Lever names no company
        ("recruitee", 5, "low", "Its postings name the company Initech, not the one asked for."),
        ("smartrecruiters", 1, "low", f"Only 1 posting, and {SMALL} Its postings name the company Globex, not the one "
         "asked for."),
    ]  # fmt: skip
    assert result["next"] == DOUBT


def test_a_name_probe_does_not_warn_of_boards_that_look_like_the_companys():
    web = serving(
        {
            "https://boards-api.greenhouse.io/v1/boards/globex/jobs?content=true": {
                "jobs": [dict(posting(n), company_name="Globex Corporation") for n in range(1, 6)]
            },
            "https://api.lever.co/v0/postings/globex?mode=json": [
                lever_posting(f"g{n}", "globex") for n in range(1, 6)
            ],
        }
    )
    result = registry.probe("Globex", web)
    rated = [(m["ats"], m["confidence"]) for m in result["matches"]]
    assert rated == [("greenhouse", "high"), ("lever", "medium")]  # Lever names no company: only its address matches
    assert not any("warning" in m for m in result["matches"]) and "next" not in result


def test_a_name_probe_warns_of_a_board_of_fewer_than_five_postings():
    web = serving(
        {
            "https://api.ashbyhq.com/posting-api/job-board/initech": {
                "jobs": [ashby_posting(f"i{n}", "initech") for n in range(1, 4)]  # another company's, as it was met
            },
            "https://api.lever.co/v0/postings/initech?mode=json": [
                lever_posting(f"i{n}", "initech") for n in range(1, 6)
            ],
        }
    )
    result = registry.probe("Initech", web)
    rated = [(m["ats"], m["postings"], m["confidence"], m.get("warning")) for m in result["matches"]]
    assert rated == [("ashby", 3, "low", f"Only 3 postings, and {SMALL}"), ("lever", 5, "medium", None)]
    assert result["next"] == DOUBT


def test_a_name_probe_warns_when_a_boards_slug_matches_only_part_of_the_name():
    def named(company, count):  # a Greenhouse board whose postings name their company
        return {"jobs": [dict(posting(n), company_name=company) for n in range(1, count + 1)]}

    web = serving(
        {  # Capital One's name run together or hyphenated finds nothing; its first word finds three boards
            "https://boards-api.greenhouse.io/v1/boards/capital/jobs?content=true": named("Capital", 10),
            "https://api.lever.co/v0/postings/capital?mode=json": [
                lever_posting(f"c{n}", "capital") for n in range(50)
            ],
            "https://capital.recruitee.com/api/offers/": {"offers": [offer(n, "Capital One") for n in range(1, 9)]},
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": named("Acme", 6),
        }
    )
    capital = registry.probe("Capital One", web)
    rated = [(m["ats"], m["slug"], m["confidence"], m.get("warning")) for m in capital["matches"]]
    assert rated == [
        ("greenhouse", "capital", "medium", PART),  # its postings name only that word
        ("lever", "capital", "medium", PART),  # Lever names no company
        ("recruitee", "capital", "high", None),  # its postings name the whole company
    ]
    assert capital["next"] == DOUBT
    for name in ("Acme Inc", "Acme Labs"):  # a legal suffix, or a generic word at the end, is not a part to match
        found = registry.probe(name, web)
        assert [(m["slug"], m["confidence"], m.get("warning")) for m in found["matches"]] == [("acme", "high", None)]
        assert "next" not in found


ABROAD = "None of its 61 postings is in the wanted regions (DE, GB)."


def test_a_name_probe_in_a_feed_warns_when_none_of_a_boards_postings_is_in_its_regions(repo, monkeypatch, capsys):
    root = repo(criteria={"locations": {"regions": ["US"]}})
    stage0 = registry.wanted(root)
    assert stage0 is not None and registry.wanted(root / "elsewhere") is None  # outside a feed nothing is checked

    def row(jid, place, company="Globex"):  # a SmartRecruiters posting, which names its company
        return {"id": jid, "name": "UX Researcher", "company": {"identifier": company.lower(), "name": company},
                "location": {"fullLocation": place}}  # fmt: skip

    abroad = [posting(n, location="London, United Kingdom" if n % 3 == 0 else "Berlin, Germany") for n in range(1, 62)]
    replies = {
        "https://boards-api.greenhouse.io/v1/boards/globex/jobs?content=true": {  # a company of that name abroad
            "jobs": [dict(job, company_name="Globex") for job in abroad]
        },
        "https://api.lever.co/v0/postings/globex?mode=json": [  # sales agents in another country, some placed vaguely
            lever_posting(f"g{n}", "globex", location="Remote" if n < 3 else "Lagos, Nigeria") for n in range(39)
        ],
        "https://globex.recruitee.com/api/offers/": {  # one of its postings is in the wanted regions
            "offers": [offer(n, "Globex", "Austin, TX" if n == 1 else "London") for n in range(1, 7)]
        },
        "https://api.smartrecruiters.com/v1/companies/globex/postings?limit=100&offset=0": {
            "offset": 0,
            "limit": 100,
            "totalFound": 5,
            "content": [row(str(n), "Munich, Bavaria, Germany") for n in range(5)],
        },
    }
    found = registry.probe("Globex", serving(replies), stage0=stage0)
    rated = [(m["ats"], m["confidence"], m.get("warning")) for m in found["matches"]]
    assert rated == [
        ("greenhouse", "medium", ABROAD),  # though its postings name the company asked for
        ("lever", "medium", "None of its 36 postings with a known place is in the wanted regions (NG)."),
        ("recruitee", "high", None),
        ("smartrecruiters", "medium", "None of its 5 postings is in the wanted regions (DE)."),
    ]
    assert found["next"] == DOUBT
    plain = registry.probe("Globex", serving(replies))  # no feed, no regions
    assert [m.get("warning") for m in plain["matches"]] == [None] * 4 and "next" not in plain
    page = [row(str(n), "Munich, Bavaria, Germany", "Initech") for n in range(100)]  # one page of more postings
    more = {"https://api.smartrecruiters.com/v1/companies/initech/postings?limit=100&offset=0": {
        "offset": 0, "limit": 100, "totalFound": 120, "content": page}}  # fmt: skip
    assert registry.probe("Initech", serving(more), stage0=stage0)["matches"][0]["confidence"] == "high"  # unread
    monkeypatch.setattr(registry, "Client", lambda **settings: serving(replies))  # what the command reads with
    assert main(["--repo", str(root), "sources", "probe", "Globex"]) == 0
    assert json.loads(capsys.readouterr().out)["matches"][0]["warning"] == ABROAD


def test_a_name_probe_does_not_warn_of_a_board_whose_postings_are_in_the_wanted_regions(repo):
    stage0 = registry.wanted(repo(criteria={"locations": {"regions": ["US"]}}))
    places = ("Austin, TX", "New York, NY", "Remote", "San Francisco, CA", "London, United Kingdom")
    jobs = [dict(posting(n, location=places[n % len(places)]), company_name="Initech") for n in range(1, 23)]
    web = serving({"https://boards-api.greenhouse.io/v1/boards/initech/jobs?content=true": {"jobs": jobs}})
    found = registry.probe("Initech", web, stage0=stage0)
    assert [(m["postings"], m["confidence"], m.get("warning")) for m in found["matches"]] == [(22, "high", None)]
    assert "next" not in found


@pytest.mark.parametrize(
    ("named", "company", "same"),
    [
        ("ACME, Inc.", "Acme", True),
        ("Acme", "Acme Rocket Co.", True),  # one begins the other
        ("The Acme Company", "Acme", True),
        ("Acme Technologies", "Acme Labs", True),  # generic words at the end tell nothing apart
        ("Backmarket", "Back Market", True),
        ("Initech", "Acme", False),
        ("Acmeville Bakery", "Acme", False),  # a longer word is another name
        ("Acme Dental", "Acme Rocket", False),
    ],
)
def test_a_name_a_board_gives_is_compared_word_by_word(named, company, same):
    assert registry._same_company(named, company) is same


def test_an_ashby_board_whose_name_has_a_space_or_a_dot_is_added_and_its_host_named(repo):
    root = repo(sources=())
    assert main(["--repo", str(root), "sources", "add", "ashby:Acme AI:Acme", "ashby:acme.com:Acme"]) == 0
    assert json.loads((root / "config/sources.json").read_text()) == [
        {"id": "acme-ai", "company": "Acme", "ats": "ashby", "slug": "Acme AI"},
        {"id": "acme-com", "company": "Acme", "ats": "ashby", "slug": "acme.com"},
    ]
    assert registry.add(root, ["ashby:Acme%20AI:Acme"])["added"] == []  # the name as its link writes it: the same board
    assert registry.hosts(root) == {"hosts": ["api.ashbyhq.com"]}
    for bad in ("ashby:Acme/AI:Acme", "ashby:..:Acme", "ashby:Acme%2FAI:Acme", "greenhouse:Acme AI:Acme",
                "lever:acme.com:Acme"):  # fmt: skip
        with pytest.raises(SourceError):
            registry.add(root, [bad])


def test_probe_reads_an_ashby_board_whose_name_has_a_space_or_a_dot_from_its_link():
    uuid = "1a2b3c4d-5555-6666-7777-888899990000"
    board = Boards()
    board.ashby = {"Acme%20AI": [ashby_posting(uuid, board="Acme%20AI")],  # asked for by its percent-encoded name
                   "acme.com": [ashby_posting(uuid, board="acme.com", title="Data Analyst")]}  # fmt: skip
    web = Client(board.transport, retries=0)
    assert registry.probe("https://jobs.ashbyhq.com/Acme%20AI Acme", web)["matches"] == [
        {"system": "Ashby", "ats": "ashby", "slug": "Acme AI", "supported": True, "postings": 1,
         "sample": ["UX Researcher"], "add": "ashby:Acme AI:Acme"}
    ]  # fmt: skip
    dotted = registry.probe(f"https://jobs.ashbyhq.com/acme.com/{uuid} Acme", web)["matches"]  # one posting's link
    assert [(m["add"], m["postings"], m["sample"]) for m in dotted] == [("ashby:acme.com:Acme", 1, ["Data Analyst"])]
    assert board.requests == [("board", "Acme%20AI"), ("board", "acme.com")]


ICIMS = "iCIMS (its terms forbid automated reading of its career sites)"  # docs/spikes.md, E10


def test_probe_reads_a_careers_url_names_systems_it_cannot_read_and_follows_a_page_to_its_board():
    board = Boards()
    board.workday = {jid: wd_posting(jid, title) for jid, title in (("R1", "UX Researcher"), ("R2", "Analyst"))}
    page = (
        '<a href="https://initech.wd1.myworkdayjobs.com/en-US/External">Jobs</a> '
        '<a href="https://careers-initech.icims.com/jobs/intro">Campus</a> '
        '<footer><a href="https://www.linkedin.com/company/initech">Follow us</a></footer>'
    )

    def transport(method, url, headers, body, timeout):
        if url == "https://www.initech.example/careers":
            return 200, {}, page.encode()
        return board.transport(method, url, headers, body, timeout)

    web = Client(transport, retries=0)
    found = registry.probe("https://initech.wd1.myworkdayjobs.com/en-US/External/job/x_R1 Initech", web)
    assert found["matches"][0]["add"] == "workday:initech.wd1.myworkdayjobs.com/External:Initech"
    assert (found["matches"][0]["postings"], found["unsupported"]) == (2, [])
    other = registry.probe("https://careers-initech.icims.com/jobs", web)
    assert (other["matches"], other["unsupported"]) == ([], [ICIMS]) and "cannot read" in other["next"]
    assert found["matches"][0]["categories"] == [{"name": "Research", "postings": 2}]  # the site's own job categories
    own = registry.probe("https://www.initech.example/careers", web)  # one request, then each board it links to
    assert [m["system"] for m in own["matches"]] == ["Workday"] and own["unsupported"] == [ICIMS]  # not the footer
    assert own["matches"][0]["add"].endswith(":<Company Name>") and "name" in own["next"]
    assert "LinkedIn" in registry.probe("https://www.linkedin.com/jobs/view/1", web)["unsupported"][0]  # only directly
    bare = registry.probe("https://initech.wd1.myworkdayjobs.com/", web)["matches"][0]
    assert "no site name" in bare["error"] and "add" not in bare
    shared = registry.probe("https://wd1.myworkdaysite.com/recruiting/initech/External/job/x_R1 Initech", web)
    assert shared["matches"][0]["add"] == "workday:wd1.myworkdaysite.com/initech/External:Initech"
    assert "sources probe <url>" in registry.probe("Nobody Inc", Client(lambda *a: (404, {}, b"{}"), retries=0))["next"]


PHENOM = '<script src="https://cdn.phenompeople.com/CareerConnectResources/pp/ACME/en_us/desktop/js/main.js"></script>'


def test_probe_points_to_a_postings_apply_link_when_it_finds_only_a_front_end():
    board = Boards()
    board.workday = {"R1": wd_posting("R1")}
    workday = '<a href="https://globex.wd1.myworkdayjobs.com/en-US/External">Search all jobs</a>'
    pages = {
        "https://careers.acme.example/": PHENOM,  # a Phenom site, whose Apply buttons lead to another system
        "https://careers.globex.example/": PHENOM + workday,
        "https://careers.initech.example/": '<a href="https://careers-initech.icims.com/jobs/intro">Jobs</a>',
    }

    def transport(method, url, headers, body, timeout):
        if url in pages:
            return 200, {}, pages[url].encode()
        if ".example/" in url:  # robots.txt, or a Phenom site's search-results page: none here
            return 404, {}, b"Not found"
        return board.transport(method, url, headers, body, timeout)

    web = Client(transport, sleep=lambda seconds: None, retries=0)
    hint = (
        "may be only a front end for another system: open one posting and probe its Apply link, which can lead to "
        "a system this version reads, such as Workday."
    )
    front = registry.probe("https://careers.acme.example/ Acme", web)
    assert (front["matches"], front["unsupported"]) == ([], ["Phenom"])
    assert front["next"] == f"This company uses Phenom, which this version cannot read. Phenom {hint}"
    both = registry.probe("https://careers.globex.example/ Globex", web)  # the page links the system behind it too
    assert [m["add"] for m in both["matches"]] == ["workday:globex.wd1.myworkdayjobs.com/External:Globex"]
    assert both["unsupported"] == ["Phenom"] and "next" not in both
    own = registry.probe("https://careers.initech.example/ Initech", web)  # a system that holds the postings itself
    assert own["next"] == f"This company uses {ICIMS}, which this version cannot read. There is no board to add."
    direct = registry.probe("https://acme.avature.net/careers Acme", web)
    assert direct["next"] == f"This company uses Avature, which this version cannot read. Avature {hint}"
    assert set(registry.FRONT_ENDS) <= {system for _, system in registry.UNSUPPORTED}  # named as the probe names them


SITE = "https://careers.acme.example"
HOME = (  # a Phenom site's home page: the site's settings and the vendor's assets, and no posting's link
    '<script>var phApp = phApp || {"widgetApiEndpoint":"https://careers.acme.example/widgets","country":"us",'
    '"locale":"en_us","baseUrl":"https://careers.acme.example/us/en/","pageName":"home"};</script>' + PHENOM
)
FRONT = (
    "This company uses Phenom, which this version cannot read. Phenom may be only a front end for another system: "
    "open one posting and probe its Apply link, which can lead to a system this version reads, such as Workday."
)
CXS = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs"  # where the Workday site is searched


def search_page(*links):
    """A Phenom site's search-results page: its first postings, each with the link it is applied through."""
    jobs = [{"jobSeqNo": f"ACMEUS{n}", "title": "UX Researcher", "applyUrl": link} for n, link in enumerate(links)]
    return f'<script>phApp.ddo = {{"eagerLoadRefineSearch":{{"data":{{"jobs":{json.dumps(jobs)}}}}}}};</script>{PHENOM}'


def apply_at(jid):
    """A posting's Apply link, on the employer's Workday site."""
    return f"https://acme.wd5.myworkdayjobs.com/External/job/San-Jose/UX-Researcher_{jid}"


def phenom_site(files, board=None):
    """A client for fake sites serving `files` by URL (a page, or a whole reply) and 404 for anything else,
    with `board`'s fake Workday site; and the list of every URL asked for."""
    asked = []

    def transport(method, url, headers, body, timeout):
        asked.append(url)
        if board is not None and "/wday/cxs/" in url:
            return board.transport(method, url, headers, body, timeout)
        reply = files.get(url)
        if isinstance(reply, tuple):
            return reply
        return (404, {}, b"Not found") if reply is None else (200, {}, reply.encode())

    return Client(transport, sleep=lambda seconds: None, retries=0), asked


def test_probe_reads_a_phenom_sites_search_results_page_for_the_board_behind_it():
    board = Boards()
    board.workday = {jid: wd_posting(jid) for jid in ("R1", "R2")}
    files = {
        f"{SITE}/": HOME,  # what people paste: no posting's link
        f"{SITE}/robots.txt": "User-agent: *\nDisallow: */apply\nDisallow: /chatbot/\n",  # as the sites checked
        f"{SITE}/us/en/search-results": search_page(*[apply_at(jid) for jid in ("R1", "R2", "R1")]),
    }
    web, asked = phenom_site(files, board)
    found = registry.probe(f"{SITE}/ Acme", web)
    assert [(m["add"], m["postings"]) for m in found["matches"]] == [
        ("workday:acme.wd5.myworkdayjobs.com/External:Acme", 2)
    ]
    assert found["unsupported"] == ["Phenom"] and "next" not in found
    assert asked == [f"{SITE}/", f"{SITE}/robots.txt", f"{SITE}/us/en/search-results", CXS]  # the board read once
    asked.clear()
    files[f"{SITE}/ca/fr/c/design"] = PHENOM  # a page without the site's settings: its own path says where
    files[f"{SITE}/ca/fr/search-results"] = search_page(apply_at("R1"))
    anonymous = registry.probe(f"{SITE}/ca/fr/c/design", web)
    assert [m["add"] for m in anonymous["matches"]] == ["workday:acme.wd5.myworkdayjobs.com/External:<Company Name>"]
    assert anonymous["next"].startswith("Put the company's name after the URL")
    assert asked == [f"{SITE}/ca/fr/c/design", f"{SITE}/robots.txt", f"{SITE}/ca/fr/search-results", CXS]


def test_probe_keeps_its_advice_when_a_phenom_sites_search_results_page_links_no_board_it_reads():
    own = [f"{SITE}/us/en/apply?jobSeqNo=ACMEUS{n}" for n in range(3)]  # applied for on the site itself
    files = {f"{SITE}/": HOME, f"{SITE}/us/en/search-results": search_page(*own)}
    web, asked = phenom_site(files)
    found = registry.probe(f"{SITE}/ Acme", web)
    assert (found["matches"], found["unsupported"], found["next"]) == ([], ["Phenom"], FRONT)
    assert asked == [f"{SITE}/", f"{SITE}/robots.txt", f"{SITE}/us/en/search-results"]
    asked.clear()
    itself = registry.probe(f"{SITE}/us/en/search-results Acme", web)  # that page probed: not asked for again
    assert (itself["next"], asked) == (FRONT, [f"{SITE}/us/en/search-results"])
    away = (200, {}, search_page(apply_at("R1")).encode(), "https://login.globex.example/")
    for reply in ((404, {}, b"Not found"), away):  # no such page, or a redirect off the site: nothing read
        files[f"{SITE}/us/en/search-results"] = reply
        asked.clear()
        found = registry.probe(f"{SITE}/ Acme", web)
        assert (found["matches"], found["next"]) == ([], FRONT)
        assert asked == [f"{SITE}/", f"{SITE}/robots.txt", f"{SITE}/us/en/search-results"]


def test_probe_does_not_ask_for_a_search_results_page_that_robots_txt_disallows():
    files = {f"{SITE}/": HOME, f"{SITE}/us/en/search-results": search_page(apply_at("R1"))}
    disallowing = (
        "User-agent: *\nDisallow: */search-results\n",
        "User-agent: *\nDisallow:\n\nUser-agent: automated-job-hunter\nDisallow: /us/\n",  # this engine by name
        (403, {}, b"Forbidden"),  # which disallows the whole site
        (503, {}, b"Unavailable"),  # which says nothing now, so nothing is read
    )
    for robots_txt in disallowing:
        files[f"{SITE}/robots.txt"] = robots_txt
        web, asked = phenom_site(files, Boards())
        found = registry.probe(f"{SITE}/ Acme", web)
        assert (found["matches"], found["unsupported"], found["next"]) == ([], ["Phenom"], FRONT)
        assert asked == [f"{SITE}/", f"{SITE}/robots.txt"]


def test_probe_does_not_follow_a_phenom_sites_base_url_to_another_host():
    elsewhere = "https://careers.globex.example/us/en/"
    files = {
        f"{SITE}/": HOME.replace('"https://careers.acme.example/us/en/"', f'"{elsewhere}"'),
        f"{elsewhere}search-results": search_page(apply_at("R1")),
    }
    web, asked = phenom_site(files, Boards())
    found = registry.probe(f"{SITE}/ Acme", web)
    assert (found["matches"], found["next"]) == ([], FRONT)
    assert asked == [f"{SITE}/", f"{SITE}/robots.txt", f"{SITE}/global/en/search-results"]  # this host's own


def test_probe_asks_a_site_for_more_only_when_its_page_is_phenom_and_links_no_board():
    board = Boards()
    board.workday = {"R1": wd_posting("R1")}
    pages = {
        "https://careers.initech.example/": '<a href="https://careers-initech.icims.com/jobs/intro">Jobs</a>',
        "https://careers.hooli.example/": f'<a href="{apply_at("R1")}">Jobs</a>',  # not Phenom, linking a board
        "https://careers.globex.example/": "<p>Our career site moved off Phenom last year.</p>",  # only the word
        "https://careers.umbrella.example/": PHENOM + f'<a href="{apply_at("R1")}">Jobs</a>',  # Phenom, with a board
        f"{SITE}/": HOME,
    }
    web, asked = phenom_site(pages, board)
    for url, extra in [
        ("https://careers.initech.example/", []),
        ("https://careers.hooli.example/", [CXS]),
        ("https://careers.globex.example/", []),
        ("https://careers.umbrella.example/", [CXS]),
        (f"{SITE}/", [f"{SITE}/robots.txt", f"{SITE}/us/en/search-results"]),  # the one page that is asked for more
    ]:
        asked.clear()
        registry.probe(f"{url} Acme", web)
        assert asked == [url, *extra]


def test_probe_knows_a_board_that_an_old_careers_address_moved_to():
    board = Boards()
    board.workday = {jid: wd_posting(jid, title) for jid, title in (("R1", "UX Researcher"), ("R2", "Analyst"))}
    landing = "https://initech.wd1.myworkdayjobs.com/en-US/External"

    def transport(method, url, headers, body, timeout):
        if url == "https://jobs.initech.example/":  # redirects to the board, whose page is all script
            return 200, {}, b'<html><body><div id="root"></div></body></html>', landing
        return board.transport(method, url, headers, body, timeout)

    found = registry.probe("https://jobs.initech.example/ Initech", Client(transport, retries=0))
    assert found["resolved"] == landing
    assert [m["add"] for m in found["matches"]] == ["workday:initech.wd1.myworkdayjobs.com/External:Initech"]


def test_probe_says_what_to_try_when_a_careers_page_cannot_be_read():
    def timing_out(method, url, headers, body, timeout):
        raise socket.timeout("timed out")  # named timeout on Python 3.9, TimeoutError from 3.10

    def refusing(method, url, headers, body, timeout):  # as a bot filter answers
        return 403, {}, b"<html><body>Access denied</body></html>"

    for transport in (timing_out, refusing):
        found = registry.probe("https://careers.acme.example/ Acme", Client(transport, retries=0))
        assert (found["matches"], found["unsupported"]) == ([], [])
        said = found["next"]
        assert said.startswith("The page could not be read: ")  # with the error's name
        assert "seldom helps" in said and "Apply link" in said and "probe the company's name" in said
        assert "Greenhouse, Ashby, Lever, Recruitee, Gem and SmartRecruiters" in said  # what a name probe tries
    assert said.startswith("The page could not be read: HttpError. ")  # the last one, the 403


def test_probe_says_a_workday_sites_http_422_means_a_wrong_site_name():
    answers = {  # what each jobs API answers: a Workday site answers 422 for a site name it does not have
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/careers/jobs": 422,
        "https://wd1.myworkdaysite.com/wday/cxs/acme/careers/jobs": 422,
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs": 404,
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": 422,
    }

    def transport(method, url, headers, body, timeout):
        return answers[url], {}, b'{"errorCode":"HTTP_422"}'

    web = Client(transport, retries=0)
    hint = (
        "HTTP 422 from this Workday site: the site name 'careers' is probably wrong, or the site is not public. "
        "Open one of the company's postings and probe its URL, which carries the right tenant and site."
    )
    guessed = registry.probe("https://acme.wd1.myworkdayjobs.com/en-US/careers Acme", web)
    assert guessed["matches"] == [{"system": "Workday", "ats": "workday", "host": "acme.wd1.myworkdayjobs.com",
                                   "site": "careers", "supported": True, "error": hint}]  # fmt: skip
    shared = registry.probe("https://wd1.myworkdaysite.com/recruiting/acme/careers Acme", web)["matches"][0]
    assert (shared["tenant"], shared["error"]) == ("acme", hint)
    other = registry.probe("https://acme.wd1.myworkdayjobs.com/External Acme", web)["matches"][0]  # as it was
    assert other["error"] == "HttpError: HTTP 404 for https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs"
    board = registry.probe("https://boards.greenhouse.io/acme Acme", web)["matches"][0]  # not a Workday site
    assert board["error"] == "HttpError: HTTP 422 for https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"


def test_probe_reads_an_http_careers_address_over_https():
    asked = []

    def page(method, url, headers, body, timeout):
        asked.append(url)
        return 200, {}, b'<a href="https://boards.greenhouse.io/acme">Jobs</a>'

    def board(method, url, headers, body, timeout):
        asked.append(url)
        if url.startswith("https://careers.acme.example"):
            return page(method, url, headers, body, timeout)
        return 200, {}, json.dumps({"jobs": []}).encode()

    found = registry.probe("http://careers.acme.example/jobs Acme", Client(board, retries=0))
    assert asked[0] == "https://careers.acme.example/jobs" and found["url"] == "https://careers.acme.example/jobs"
    assert [m["ats"] for m in found["matches"]] == ["greenhouse"]  # not the advice for a page that refuses


def test_a_workday_site_is_added_by_host_and_site(tmp_path):
    scaffold.init(tmp_path / "feed")
    root = tmp_path / "feed"
    assert registry.add(root, ["workday:initech.wd1.myworkdayjobs.com/External:Initech"])["added"] == ["initech"]
    entry = json.loads((root / "config/sources.json").read_text())[0]
    assert entry == {
        "id": "initech",
        "company": "Initech",
        "ats": "workday",
        "host": "initech.wd1.myworkdayjobs.com",
        "site": "External",
    }
    assert registry.add(root, ["workday:initech.wd1.myworkdayjobs.com/Campus:Initech"])["added"] == ["initech-campus"]
    assert registry.add(root, ["workday:wd1.myworkdaysite.com/globex/Careers:Globex"])["added"] == ["globex"]
    shared = next(e for e in json.loads((root / "config/sources.json").read_text()) if e["id"] == "globex")
    assert (shared["host"], shared["tenant"], shared["site"]) == ("wd1.myworkdaysite.com", "globex", "Careers")
    assert registry.hosts(root) == {"hosts": ["initech.wd1.myworkdayjobs.com", "wd1.myworkdaysite.com"]}
    with pytest.raises(SourceError):
        registry.add(root, ["workday:initech.example.com/External:Initech"])


def test_upgrade_refuses_to_install_an_engine_older_than_the_feeds(tmp_path):
    root = tmp_path / "feed"
    scaffold.init(root)
    (root / "state").mkdir(exist_ok=True)
    (root / "state/meta.json").write_text('{"schema_version": 9, "engine_version": "9.0.0", "as_of": "2026-10-13"}')
    with pytest.raises(scaffold.ScaffoldError, match=r"state is schema 9, written by engine 9\.0\.0"):
        scaffold.upgrade(root)
    (root / "state/meta.json").write_text('{"schema_version": 2, "engine_version": "0.2.0", "as_of": "2026-10-13"}')
    (root / ".hunter/VERSION").write_text("0.10.0\n")  # numerically newer than 0.2.0
    with pytest.raises(scaffold.ScaffoldError, match=r"runs engine 0\.10\.0, newer than this copy"):
        scaffold.upgrade(root)
    (root / ".hunter/VERSION").write_text(scaffold.__version__ + "\n")  # the same version again is fine
    assert scaffold.upgrade(root)["to"] == scaffold.__version__


def test_the_skill_file_is_portable_and_its_links_resolve():
    text = (SKILL / "SKILL.md").read_text()
    front, body = text.split("---\n", 2)[1:]
    fields = dict(line.split(": ", 1) for line in front.strip().splitlines())
    assert set(fields) == {"name", "description"}  # only the keys every agent's skill loader understands
    assert fields["name"] == SKILL.name == "automated-job-hunter" and len(fields["description"]) <= 1024
    assert len(body.splitlines()) < 500
    for link in re.findall(r"\]\((references/[^)]+)\)", text):
        assert (SKILL / link).is_file(), link
    for reference in (SKILL / "references").glob("*.md"):
        for link in re.findall(r"\]\(([a-z-]+\.md)\)", reference.read_text()):
            assert (SKILL / "references" / link).is_file(), f"{reference.name} -> {link}"


def test_shipped_text_names_no_person_or_machine_and_the_contract_documents_every_field():
    shipped = [
        *SKILL.rglob("*.md"),
        *SKILL.rglob("*.json"),
        *SKILL.rglob("*.yml"),
        *SKILL.rglob("*.sh"),
        *(REPO / "agents").glob("*.md"),
    ]
    assert len(shipped) > 15
    for path in shipped:
        text = path.read_text()
        assert "/Users/" not in text and "@gmail" not in text, path
    job = Job(key="a:1", source="a", source_id="1", company="A", title="T", location="L", url="https://example.com/1")
    criteria = {"eligibility": {"rule_packs": ["us"]}, "thresholds": {"strong": 75, "good": 55, "publish_min": 40}}
    handoff = (SKILL / "assets/template/HANDOFF.md").read_text()
    for field in feed.build_record(job, "2026-10-13", None, criteria, 1):
        assert f"`{field}`" in handoff or field in ("v", "source", "source_label"), field


def run_judging_step(tmp_path, step, **secrets):
    """Run one judging step's script from a fresh workflow, with a stand-in judge.sh that lists the
    credential variables it received. GitHub passes a secret that is not set as an empty string."""
    root = tmp_path / "feed"
    if not root.exists():
        scaffold.init(root)
    workflow = (root / scaffold.WORKFLOW).read_text()
    script = textwrap.dedent(workflow.split(f"id: {step}\n")[1].split("\n      - name:")[0].split("run: |\n")[1])
    stub = tmp_path / "stub" / ".hunter" / "engine" / "judge.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("env | grep -E '^(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|OAUTH_TOKEN|API_KEY)=' | cut -d= -f1\n")
    env = {"PATH": "/usr/bin:/bin", "OAUTH_TOKEN": "", "API_KEY": "", **secrets}
    return subprocess.run(["bash", "-e", "-c", script], cwd=stub.parents[2], env=env, capture_output=True, text=True)


@pytest.mark.parametrize("step", ["judge_triage", "judge_assess"])
def test_a_judging_step_uses_whichever_secret_is_set(tmp_path, step):
    subscription = run_judging_step(tmp_path, step, OAUTH_TOKEN="token")
    assert subscription.returncode == 0 and subscription.stdout.split() == ["CLAUDE_CODE_OAUTH_TOKEN"]
    api_key = run_judging_step(tmp_path, step, API_KEY="key")
    assert api_key.returncode == 0 and api_key.stdout.split() == ["ANTHROPIC_API_KEY"]
    both = run_judging_step(tmp_path, step, OAUTH_TOKEN="token", API_KEY="key")
    assert both.returncode == 0 and both.stdout.split() == ["ANTHROPIC_API_KEY"]
    neither = run_judging_step(tmp_path, step)
    assert neither.returncode == 1 and neither.stdout.startswith("::error::No Claude secret is set")
    assert "ANTHROPIC_API_KEY\n" not in neither.stdout  # judge.sh never ran


def test_a_failed_judging_step_fails_the_run_after_the_work_is_published(tmp_path):
    scaffold.init(tmp_path / "feed")
    workflow = (tmp_path / "feed" / scaffold.WORKFLOW).read_text()
    for step in ("judge_triage", "judge_assess"):
        block = workflow.split(f"id: {step}\n")[1].split("\n      - name:")[0]
        assert "OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in block
        assert "API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}" in block
        assert "continue-on-error: true" in block  # so that apply and publish still run
    report = workflow.split("- name: Report a failed judging step")[1]
    assert "steps.judge_triage.outcome == 'failure'" in report and "steps.judge_assess.outcome == 'failure'" in report
    assert "always()" in report and "exit 1" in report
    assert workflow.index("hunter.py publish") < workflow.index("Report a failed judging step")  # the work lands first


def test_upgrade_says_when_it_rewrote_the_workflow(tmp_path):
    root = tmp_path / "feed"
    scaffold.init(root)
    assert "workflow_changed" not in scaffold.upgrade(root)
    path = root / scaffold.WORKFLOW
    path.write_text(path.read_text().replace("timeout-minutes: 45", "timeout-minutes: 90"))
    result = scaffold.upgrade(root)
    assert result["workflow_changed"] is True and "git diff" in result["note"]
    assert "timeout-minutes: 45" in path.read_text()
