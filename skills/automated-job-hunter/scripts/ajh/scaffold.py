"""Create and upgrade feed repos: the template, plus a pinned copy of the engine.

A feed repo carries its own engine under `.hunter/engine/` so a scheduled run needs nothing but
Python and never depends on what is installed where it runs. `init` and `upgrade` copy from the
installed skill, so they only work from there, not from a vendored copy.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import __version__
from .state import write_text

SCRIPTS = Path(__file__).resolve().parents[1]
ASSETS = SCRIPTS.parent / "assets"
CADENCES = {"1h": "{minute} * * * *", "4h": "{minute} */4 * * *", "8h": "{minute} */8 * * *"}
# Engine-owned files: written by `init` and rewritten by `upgrade`. Everything else in a feed repo
# (profile, config, state, the feed itself) belongs to the user and is never touched here.
TEMPLATE = {
    "AGENTS.md": "AGENTS.md",
    "CLAUDE.md": "CLAUDE.md",
    "HANDOFF.md": "HANDOFF.md",
    "RUNBOOK.md": ".hunter/RUNBOOK.md",
    "gitignore": ".gitignore",
}
WORKFLOW = ".github/workflows/job-hunter.yml"


class ScaffoldError(ValueError):
    """The feed repo cannot be created or upgraded as asked; the message says why."""


def engine_files() -> list[Path]:
    """Every file that makes up the engine, relative to the scripts directory."""
    package = SCRIPTS / "ajh"
    found = [p.relative_to(SCRIPTS) for p in package.rglob("*") if p.is_file() and p.suffix in (".py", ".json")]
    return sorted([Path("hunter.py"), Path("judge.sh"), *found])


def agent_markdown(name: str, spec: dict[str, Any]) -> str:
    """A subagent definition in the Markdown form Claude Code reads from an `agents/` directory."""
    return (
        f"---\nname: {name}\ndescription: {spec['description']}\ntools: {', '.join(spec['tools'])}\n"
        f"model: {spec['model']}\n---\n\n{spec['prompt']}\n"
    )


def load_agents() -> dict[str, dict[str, Any]]:
    if not ASSETS.is_dir():
        raise ScaffoldError(
            "Run this from the installed automated-job-hunter skill, not from a feed repo's engine copy"
        )
    return json.loads((ASSETS / "agents.json").read_text(encoding="utf-8"))


def _vendor(root: Path) -> list[str]:
    """Copy the engine and subagent definitions into a feed repo and record what was copied."""
    agents = load_agents()
    engine = root / ".hunter" / "engine"
    if engine.exists():
        shutil.rmtree(engine)
    digests = []
    for relative in engine_files():
        target = engine / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPTS / relative, target)
        digests.append(f"{hashlib.sha256(target.read_bytes()).hexdigest()}  engine/{relative.as_posix()}")
    (engine / "judge.sh").chmod(0o755)
    write_text(root / ".hunter" / "agents.json", (ASSETS / "agents.json").read_text(encoding="utf-8"))
    for name, spec in agents.items():
        write_text(root / ".claude" / "agents" / f"{name}.md", agent_markdown(name, spec))
    write_text(root / ".hunter" / "VERSION", __version__ + "\n")
    write_text(root / ".hunter" / "MANIFEST.sha256", "\n".join(digests) + "\n")
    for source, target in TEMPLATE.items():
        write_text(root / target, (ASSETS / "template" / source).read_text(encoding="utf-8"))
    return [".hunter/engine/", ".hunter/agents.json", ".claude/agents/", ".hunter/VERSION", *TEMPLATE.values()]


def _workflow(root: Path, cadence: str | None) -> bool:
    """Write the scheduled workflow, keeping an existing schedule unless a cadence is given.

    Returns whether the file changed. The workflow is engine-owned: anything but the schedule is
    replaced, so the credential choice is made with repository secrets, never by editing it.
    """
    path = root / WORKFLOW
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    existing = re.search(r'cron: "([^"]+)"', before)
    if cadence is None and existing:
        cron = existing.group(1)
    else:
        # An arbitrary minute keeps many feed repos from all starting on the hour, when GitHub is
        # busiest. Set times of day stay early in their hour, close to the time that was asked for.
        cadence = cadence or "4h"
        low, high = 3, 57 if cadence in CADENCES else 20
        minute = random.randint(low, high)
        if existing and cron_for(cadence, minute) == existing.group(1):
            # Always a new cron line. GitHub ties a schedule to the user who last changed the cron line
            # (see `schedule`), so `upgrade --cadence` must give the user a change to commit.
            minute = low + (minute - low + 1) % (high - low + 1)
        cron = cron_for(cadence, minute)
    template = (ASSETS / "template" / "job-hunter.yml").read_text(encoding="utf-8")
    after = template.replace("__CRON__", cron).replace("__CLAUDE_CODE_VERSION__", claude_code_version())
    write_text(path, after)
    return after != before


def cron_for(cadence: str, minute: int) -> str:
    """The cron line for a cadence: `1h`, `4h` or `8h`, or UTC hours such as `6,10,14,20`.

    GitHub reads the schedule in UTC and has no time zones.
    """
    if cadence in CADENCES:
        return CADENCES[cadence].format(minute=minute)
    try:
        hours = sorted({int(hour) for hour in cadence.split(",")})
    except ValueError:
        hours = []
    if not hours or not 0 <= hours[0] <= hours[-1] <= 23:
        raise ScaffoldError(f"cadence must be {', '.join(CADENCES)}, or UTC hours such as 6,10,14,20; got {cadence!r}")
    return f"{minute} {','.join(map(str, hours))} * * *"


def claude_code_version() -> str:
    """The Claude Code version the headless invocation was verified with."""
    return (ASSETS / "claude-code-version").read_text(encoding="utf-8").strip()


def commit_email(root: Path) -> str | None:
    """The email git puts on commits made in `root`, or None when none is configured."""
    try:
        found = subprocess.run(["git", "-C", str(root), "config", "user.email"], capture_output=True, text=True)
    except OSError:
        return None
    return found.stdout.strip() or None


def _identity(root: Path) -> list[str]:
    """Keep git from making up who commits in a feed repo, and say so when nobody is set.

    With no email configured, git invents one from the machine's name (`user@laptop.local`). No GitHub
    account has it, so the feed's commits belong to nobody, including the one GitHub ties the
    schedule to (see `schedule`). `user.useConfigOnly` makes git refuse such a commit instead.
    `publish` passes its own identity when none is set, so it is unaffected.
    """
    if (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "config", "--local", "user.useConfigOnly", "true"], capture_output=True)
    if commit_email(root):
        return []
    return [
        "git has no email for commits in this repo, so it now refuses to commit rather than make one up from the "
        "machine's name, which no GitHub account has. Before committing, set the user's GitHub identity in this "
        "repo only: `git config user.name <name>` and `git config user.email "
        "<an address on their GitHub account>`; with gh logged in, their noreply address is "
        "`gh api user --jq '\"\\(.id)+\\(.login)@users.noreply.github.com\"'`."
    ]


def _made_up(email: str) -> bool:
    """An address git invented from a machine's name, which no GitHub account can have."""
    domain = email.rpartition("@")[2].lower()
    return "." not in domain or domain.endswith(".local") or "(none)" in domain


MADE_UP = "made up by git from a machine's name"


def _shown(email: str) -> str:
    """An address as `status` prints it. A made-up one holds the machine's user and host names, which
    nobody chose to show and `status` output can end up in a bug report, so it is only described."""
    return MADE_UP if _made_up(email) else email


def schedule(root: Path) -> dict[str, Any] | None:
    """The workflow's cron line and the commit that last changed it.

    GitHub sends notifications for scheduled workflows to "the user who last modified the cron syntax
    in the workflow file", and when a commit that changes the cron line reactivates a disabled
    schedule, its user becomes the actor of the runs. Addresses no account can have are flagged; for
    any other, GitHub's own answer is `gh api repos/<owner>/<repo>/commits/<sha> --jq .author.login`,
    where null means no account. None of this tells whether GitHub starts the scheduled runs.
    """
    path = root / WORKFLOW
    if not path.exists():
        return None
    found = re.search(r'cron: "([^"]+)"', path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {"cron": found.group(1) if found else None, "changed_in": None}
    try:
        log = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "-G", 'cron: "', "--format=%H%x00%ae%x00%ce", "--", WORKFLOW],
            capture_output=True,
            text=True,
        )
    except OSError:
        return result
    fields = log.stdout.strip().split("\0")
    if log.returncode or len(fields) != 3:
        return result  # not committed yet
    sha, author, committer = fields
    result.update(changed_in=sha, author=_shown(author), committer=_shown(committer))
    if _made_up(author) or _made_up(committer):
        result["warning"] = (
            f"The schedule was last changed in commit {sha[:12]} under an address git made up from a machine's "
            "name. No GitHub account has it, and GitHub's documentation does not say who then gets the schedule's "
            "notifications. Set the user's GitHub identity in this repo, then write the cron line anew with "
            "`upgrade --cadence` and their cadence, commit and push."
        )
    return result


def init(directory: Path | str, cadence: str = "4h") -> dict[str, Any]:
    """Create a feed repo. Refuses a directory that already holds something else."""
    root = Path(directory).expanduser().resolve()
    cron_for(cadence, 0)  # refuse a bad cadence before creating anything
    load_agents()
    if root.exists() and any(root.iterdir()) and not (root / ".hunter").exists():
        entries = sorted(p.name for p in root.iterdir() if p.name != ".git")
        if entries:
            raise ScaffoldError(f"{root} is not empty and is not a feed repo ({', '.join(entries[:5])})")
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        made = subprocess.run(["git", "init", "-q", "-b", "main", str(root)], capture_output=True, text=True)
        if made.returncode:
            raise ScaffoldError(f"git init failed: {made.stderr.strip()[:200]}")
    created = _vendor(root)
    _workflow(root, cadence)
    if not (root / "config" / "sources.json").exists():
        write_text(root / "config" / "sources.json", "[]\n")
        created.append("config/sources.json")
    warnings = _identity(root)
    return {
        "root": str(root),
        "engine_version": __version__,
        "cadence": cadence,
        "created": [*created, WORKFLOW],
        **({"warnings": warnings} if warnings else {}),
    }


def upgrade(directory: Path | str, cadence: str | None = None) -> dict[str, Any]:
    """Replace a feed repo's engine and engine-owned files with this version's."""
    root = Path(directory).expanduser().resolve()
    version = root / ".hunter" / "VERSION"
    if not version.exists():
        raise ScaffoldError(f"{root} is not a feed repo (no .hunter/VERSION); use `init` first")
    if cadence is not None:
        cron_for(cadence, 0)  # refuse a bad cadence before changing anything
    previous = version.read_text(encoding="utf-8").strip()
    updated = _vendor(root)
    result = {"root": str(root), "from": previous, "to": __version__, "updated": [*updated, WORKFLOW]}
    if _workflow(root, cadence):
        result["workflow_changed"] = True
        result["note"] = (
            f"{WORKFLOW} was rewritten from this version's template; only its schedule was kept. "
            "Review `git diff` before committing."
        )
    warnings = _identity(root)
    owner = schedule(root) if cadence is None else None  # a new cron line gets its owner when it is committed
    if owner and owner.get("warning"):
        warnings.append(owner["warning"])
    if warnings:
        result["warnings"] = warnings
    return result


def verify(directory: Path | str) -> dict[str, Any]:
    """Whether a feed repo's engine copy is exactly what was vendored."""
    hunter = Path(directory).expanduser().resolve() / ".hunter"
    manifest = hunter / "MANIFEST.sha256"
    if not manifest.exists():
        return {"vendored": False}
    changed = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, _, relative = line.partition("  ")
        path = hunter / relative
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(relative)
    version = (hunter / "VERSION").read_text(encoding="utf-8").strip() if (hunter / "VERSION").exists() else None
    return {"vendored": True, "engine_version": version, "intact": not changed, "changed": changed}
