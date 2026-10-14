"""Creating, upgrading and running a feed repo from the skill's own files."""

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from ajh import feed, registry, scaffold, tick
from ajh.cli import main
from ajh.http import Client
from ajh.model import Job, SourceError
from helpers import Boards, answer_assess, answer_triage, posting, wd_posting

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


def test_the_three_forms_of_the_subagent_definitions_agree():
    agents = scaffold.load_agents()
    assert set(agents) == {"hunter-triage", "hunter-assess"}
    assert (agents["hunter-triage"]["model"], agents["hunter-assess"]["model"]) == ("haiku", "sonnet")
    for name, spec in agents.items():
        assert spec["tools"] == ["Read", "Write"]  # a judging subagent can do nothing else
        assert (REPO / "agents" / f"{name}.md").read_text() == scaffold.agent_markdown(name, spec)


def test_a_whole_tick_runs_from_a_scaffolded_repo(tmp_path, monkeypatch):
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
                                  "add": "lever:acme-rocket:Acme Rocket Co."}]  # fmt: skip
    assert registry.probe("Nobody Inc", Client(transport, retries=0))["matches"] == []


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
    assert (other["matches"], other["unsupported"]) == ([], ["iCIMS"]) and "cannot read" in other["next"]
    assert found["matches"][0]["categories"] == [{"name": "Research", "postings": 2}]  # the site's own job categories
    own = registry.probe("https://www.initech.example/careers", web)  # one request, then each board it links to
    assert [m["system"] for m in own["matches"]] == ["Workday"] and own["unsupported"] == ["iCIMS"]  # not the footer
    assert own["matches"][0]["add"].endswith(":<Company Name>") and "name" in own["next"]
    assert "LinkedIn" in registry.probe("https://www.linkedin.com/jobs/view/1", web)["unsupported"][0]  # only directly
    bare = registry.probe("https://initech.wd1.myworkdayjobs.com/", web)["matches"][0]
    assert "no site name" in bare["error"] and "add" not in bare
    shared = registry.probe("https://wd1.myworkdaysite.com/recruiting/initech/External/job/x_R1 Initech", web)
    assert shared["matches"][0]["add"] == "workday:wd1.myworkdaysite.com/initech/External:Initech"
    assert "sources probe <url>" in registry.probe("Nobody Inc", Client(lambda *a: (404, {}, b"{}"), retries=0))["next"]


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
