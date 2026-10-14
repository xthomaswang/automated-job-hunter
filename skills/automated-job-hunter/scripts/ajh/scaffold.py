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
        if (cadence or "4h") not in CADENCES:
            raise ScaffoldError(f"cadence must be one of {', '.join(CADENCES)}")
        # an arbitrary minute keeps many feed repos from all starting on the hour
        cron = CADENCES[cadence or "4h"].format(minute=random.randint(3, 57))
    template = (ASSETS / "template" / "job-hunter.yml").read_text(encoding="utf-8")
    after = template.replace("__CRON__", cron).replace("__CLAUDE_CODE_VERSION__", claude_code_version())
    write_text(path, after)
    return after != before


def claude_code_version() -> str:
    """The Claude Code version the headless invocation was verified with."""
    return (ASSETS / "claude-code-version").read_text(encoding="utf-8").strip()


def init(directory: Path | str, cadence: str = "4h") -> dict[str, Any]:
    """Create a feed repo. Refuses a directory that already holds something else."""
    root = Path(directory).expanduser().resolve()
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
    return {"root": str(root), "engine_version": __version__, "cadence": cadence, "created": [*created, WORKFLOW]}


def upgrade(directory: Path | str, cadence: str | None = None) -> dict[str, Any]:
    """Replace a feed repo's engine and engine-owned files with this version's."""
    root = Path(directory).expanduser().resolve()
    version = root / ".hunter" / "VERSION"
    if not version.exists():
        raise ScaffoldError(f"{root} is not a feed repo (no .hunter/VERSION); use `init` first")
    previous = version.read_text(encoding="utf-8").strip()
    updated = _vendor(root)
    result = {"root": str(root), "from": previous, "to": __version__, "updated": [*updated, WORKFLOW]}
    if _workflow(root, cadence):
        result["workflow_changed"] = True
        result["note"] = (
            f"{WORKFLOW} was rewritten from this version's template; only its schedule was kept. "
            "Review `git diff` before committing."
        )
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
