"""Start a feed's workflow from this Mac at the schedule's times, for when GitHub does not.

The timer is a launchd agent that runs `gh workflow run job-hunter.yml --repo <owner>/<repo>` at the
times of the workflow's cron line. The run happens on GitHub as before, with the repo's secrets;
only the start comes from here. The workflow's concurrency group keeps at most one run waiting, so
starts never pile up. launchd reads the times in local time and, unlike cron, starts a job once when
the Mac wakes if it slept through a time (`man launchd.plist`). It starts nothing while the Mac is
off or nobody is logged in.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

from .scaffold import WORKFLOW

WORKFLOW_FILE = Path(WORKFLOW).name
LAUNCHD_ENV = ("HOME", "USER", "TMPDIR", "SHELL")  # what launchd gives an agent, with the PATH below
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
TRIES, DELAY = 10, 30
# One log line per start: when, gh's exit status, how many tries, and what gh said. A start just after
# the Mac wakes can come before the network does, so a failed start is tried again for a few minutes;
# the concurrency group makes a repeated start harmless.
SCRIPT = (
    "tries=0; while :; do tries=$((tries + 1)); "
    'said=$("$0" workflow run "$1" --repo "$2" 2>&1); code=$?; '
    'if [ "$code" -eq 0 ] || [ "$tries" -ge "$3" ]; then break; fi; sleep "$4"; done; '
    'printf "%s exit %s tries %s %s\\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$code" "$tries" '
    '"$(printf %s "$said" | tr "\\n" " ")"'
)
LOG_LINE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) exit (\d+) tries (\d+) ?(.*)")
REMOTE = re.compile(
    r"(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?"
)
NUMBER = re.compile(r"[0-9]{1,2}")


class TimerError(ValueError):
    """The timer cannot be set up as asked; the message says why."""


def repo_slug(root: Path) -> str:
    """`owner/repo` of the feed repo's GitHub remote. The URL itself is never printed: it can hold a token."""
    try:
        found = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True)
    except OSError as error:
        raise TimerError(f"git is not available: {error}") from None
    match = None if found.returncode else REMOTE.fullmatch(found.stdout.strip())
    if not match:
        raise TimerError("The feed repo has no GitHub remote named origin, so there is no workflow to start")
    return match.group(1)


def cron_line(root: Path) -> str:
    path = root / WORKFLOW
    found = re.search(r'cron: "([^"]+)"', path.read_text(encoding="utf-8")) if path.exists() else None
    if not found:
        raise TimerError(f"{WORKFLOW} has no cron line to take the times from")
    return found.group(1)


def utc_times(cron: str) -> list[tuple[int, int]]:
    """The (hour, minute) pairs, in UTC, at which a daily cron line fires.

    Only the forms `init` and `upgrade --cadence` write are understood: a minute, then `*`, `*/N` or a
    list of hours, then `* * *`.
    """
    fields = cron.split()
    hours: list[int] = []
    if len(fields) == 5 and fields[2:] == ["*", "*", "*"] and NUMBER.fullmatch(fields[0]) and int(fields[0]) < 60:
        spec = fields[1]
        if spec == "*":
            hours = list(range(24))
        elif spec.startswith("*/") and NUMBER.fullmatch(spec[2:]) and 0 < int(spec[2:]) < 24:
            hours = list(range(0, 24, int(spec[2:])))
        elif all(NUMBER.fullmatch(part) and int(part) < 24 for part in spec.split(",")):
            hours = sorted({int(part) for part in spec.split(",")})
    if not hours:
        raise TimerError(
            f"The timer follows only the cron lines `init` and `upgrade --cadence` write, not `{cron}`; "
            "write one with `upgrade --cadence`"
        )
    return [(hour, int(fields[0])) for hour in hours]


def calendar(times: list[tuple[int, int]], offset: int) -> list[dict[str, int]]:
    """launchd's `StartCalendarInterval` for UTC times, on a clock `offset` minutes ahead of UTC."""
    local = sorted({divmod((hour * 60 + minute + offset) % 1440, 60) for hour, minute in times})
    if len(local) == 24 and len({minute for _, minute in local}) == 1:
        return [{"Minute": local[0][1]}]  # every hour
    return [{"Hour": hour, "Minute": minute} for hour, minute in local]


def label(slug: str) -> str:
    return "automated-job-hunter." + re.sub(r"[^A-Za-z0-9._-]", "-", slug.replace("/", "."))


def _paths(slug: str) -> tuple[Path, Path]:
    home, name = Path.home(), label(slug)
    return home / "Library" / "LaunchAgents" / f"{name}.plist", home / "Library" / "Logs" / f"{name}.log"


def _shown(path: Path) -> str:
    """A path under the home directory as `~/...`, since `status` output can end up in a bug report."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def definition(slug: str, cron: str, gh: str, offset: int) -> dict[str, Any]:
    """The launchd agent: what it runs, when, and where it logs."""
    log = _paths(slug)[1]
    return {
        "Label": label(slug),
        "ProgramArguments": ["/bin/sh", "-c", SCRIPT, gh, WORKFLOW_FILE, slug, str(TRIES), str(DELAY)],
        # Read by `show`, not by the script: the cron line the times were made from. The times stay in
        # local time when the clocks change, so they cannot be compared with the cron line itself.
        "EnvironmentVariables": {"HUNTER_CRON": cron},
        "StartCalendarInterval": calendar(utc_times(cron), offset),
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def _macos() -> bool:
    return sys.platform == "darwin"


def _zone() -> tuple[str, int]:
    """This machine's time zone and its offset from UTC now, in minutes."""
    now = datetime.now(timezone.utc).astimezone()
    offset = now.utcoffset()
    return now.tzname() or "local time", int(offset.total_seconds()) // 60 if offset else 0


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _starts(entries: list[dict[str, int]]) -> list[str]:
    return [
        f"{entry['Hour']:02d}:{entry['Minute']:02d}" if "Hour" in entry else f"every hour at :{entry['Minute']:02d}"
        for entry in entries
    ]


def _last_start(log: Path) -> dict[str, Any] | None:
    if not log.exists():
        return None
    for line in reversed(log.read_text(encoding="utf-8", errors="replace").splitlines()):
        found = LOG_LINE.fullmatch(line.strip())
        if found:
            at, code, tries, said = found.groups()
            return {"at": at, "exit": int(code), "tries": int(tries), "said": said.strip()[:300]}
    return None


def _check(gh: str, slug: str) -> None:
    """Ask GitHub for the workflow's state with gh run as launchd will run it: without the shell's
    environment, so a login that lives only in a variable such as GH_TOKEN shows up now."""
    env = {name: os.environ[name] for name in LAUNCHD_ENV if name in os.environ}
    try:
        done = subprocess.run(
            [gh, "api", f"repos/{slug}/actions/workflows/{WORKFLOW_FILE}", "--jq", ".state"],
            capture_output=True,
            text=True,
            env={**env, "PATH": LAUNCHD_PATH},
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TimerError(f"gh could not be asked about the workflow: {error}") from None
    if done.returncode:
        raise TimerError(
            f"gh, run as the timer will run it, could not read {WORKFLOW_FILE} in {slug}: "
            f"{(done.stderr or done.stdout).strip()[:300]}. A login kept in a variable such as GH_TOKEN does not "
            "reach the timer; `gh auth login` keeps it where the timer finds it"
        )
    state = done.stdout.strip()
    if state != "active":
        raise TimerError(
            f"The workflow is {state or 'in an unknown state'}, so a start would fail; "
            f"`gh workflow enable {WORKFLOW_FILE} --repo {slug}` first"
        )


def show(root: Path | str) -> dict[str, Any]:
    """When the timer starts the workflow, and how its last start went. Changes nothing."""
    root = Path(root).expanduser().resolve()
    slug, cron = repo_slug(root), cron_line(root)
    plist, log = _paths(slug)
    zone, offset = _zone()
    result: dict[str, Any] = {
        "repo": slug,
        "cron": cron,
        "command": f"gh workflow run {WORKFLOW_FILE} --repo {slug}",
        "time_zone": f"{zone}, UTC{'-' if offset < 0 else '+'}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}",
        "installed": plist.exists(),
        "plist": _shown(plist),
        "log": _shown(log),
    }
    if not plist.exists():
        result["starts"] = _starts(calendar(utc_times(cron), offset))
        return result
    agent = plistlib.loads(plist.read_bytes())
    result["starts"] = _starts(agent.get("StartCalendarInterval") or [])
    warnings = []
    if _macos():
        result["loaded"] = not _launchctl("print", f"gui/{os.getuid()}/{label(slug)}").returncode
        if not result["loaded"]:
            warnings.append("launchd has not loaded the timer, so it starts nothing; `timer install` loads it again")
    made_for = (agent.get("EnvironmentVariables") or {}).get("HUNTER_CRON")
    if made_for != cron:
        warnings.append(
            f"The timer was made for the cron line `{made_for}`, and the workflow now has `{cron}`; "
            "`timer install` again to follow it"
        )
    result["last_start"] = last = _last_start(log)
    if last and last["exit"]:
        said = last["said"] or "gh said nothing"
        warnings.append(
            f"The last start, at {last['at']}, failed after {last['tries']} tries: {said}. "
            "`gh auth status` shows whether gh is still logged in"
        )
    if warnings:
        result["warnings"] = warnings
    return result


def install(root: Path | str) -> dict[str, Any]:
    """Write this feed's launchd agent and load it, replacing one already there."""
    root = Path(root).expanduser().resolve()
    if not _macos():
        raise TimerError(
            "The timer is a launchd agent, and only macOS has launchd. Elsewhere, start the workflow from "
            'another scheduler: see "Start the workflow from elsewhere" in references/schedule.md'
        )
    slug, cron = repo_slug(root), cron_line(root)
    utc_times(cron)  # refuse a cron line it cannot follow before asking GitHub anything
    gh = shutil.which("gh")
    if not gh:
        raise TimerError("gh is not installed; step 7 of references/setup.md says how to install it and log in")
    _check(gh, slug)
    plist, log = _paths(slug)
    for folder in (plist.parent, log.parent):  # launchd creates the log, not its folder
        folder.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(definition(slug, cron, gh, _zone()[1])))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{label(slug)}")  # the copy loaded before, if any
    for _ in range(5):
        loaded = _launchctl("bootstrap", domain, str(plist))
        if not loaded.returncode:
            return show(root)
        time.sleep(1)  # launchd can take a moment to let go of the copy it unloaded
    plist.unlink()
    raise TimerError(f"launchctl could not load the timer: {(loaded.stderr or loaded.stdout).strip()[:200]}")


def remove(root: Path | str) -> dict[str, Any]:
    """Unload this feed's timer and delete it. Its log stays."""
    slug = repo_slug(Path(root).expanduser().resolve())
    plist, log = _paths(slug)
    unloaded = _macos() and not _launchctl("bootout", f"gui/{os.getuid()}/{label(slug)}").returncode
    found = plist.exists()
    if found:
        plist.unlink()
    return {"removed": bool(unloaded or found), "label": label(slug), "log": _shown(log)}


def summary(root: Path | str) -> dict[str, Any] | None:
    """`show` for `status`: only on a Mac where this feed has a timer."""
    if not _macos():
        return None
    try:
        plist = _paths(repo_slug(Path(root).expanduser().resolve()))[0]
    except TimerError:
        return None
    if not plist.exists():
        return None
    try:
        return show(root)
    except TimerError as error:  # a cron line it cannot follow
        problem = str(error)
    except (ValueError, ExpatError) as error:
        problem = f"The timer's file cannot be read ({error}); `timer install` writes it anew"
    return {"installed": True, "plist": _shown(plist), "warnings": [problem]}
