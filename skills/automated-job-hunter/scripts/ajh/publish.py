"""Commit and push what `apply` produced. The only code that runs git commands that change anything.

It stages only the generated paths, refuses when anything else is staged or the audit finds a
problem, and never forces. When the remote moved ahead during the run, it puts its own commit on
top only if the remote gained nothing but a person's input; otherwise it gives up cleanly, and the
next tick starts from a fresh clone and redoes the work.
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from typing import Any

from . import audit
from .layout import Layout
from .state import read_json

TRAILER = "Job-Hunter-Publish: auto"
FALLBACK_IDENTITY = ("automated-job-hunter", "automated-job-hunter@users.noreply.github.com")
_IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG")


class PublishBlocked(ValueError):
    """Publishing needs a person: the message says why."""


class Git:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run(
        self, *args: str, check: bool = True, config: tuple[str, ...] = (), timeout: int = 180
    ) -> subprocess.CompletedProcess:
        command = ["git", "-C", str(self.root)]
        for setting in config:
            command += ["-c", setting]
        result = subprocess.run([*command, *args], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            last = (result.stderr.strip().splitlines() or [str(result.returncode)])[-1]
            raise PublishBlocked(f"git {args[0]} failed: {last[:300]}")
        return result

    def out(self, *args: str, **options: Any) -> str:
        return self.run(*args, **options).stdout.strip()


def _message(layout: Layout) -> str:
    meta = read_json(layout.feed_meta, {})
    counts = meta.get("counts", {})
    waiting = sum(counts.get(name, 0) for name in ("pending_triage", "pending_assess", "pending_reassess"))
    return f"Update job feed: {counts.get('published', 0)} open matches, {waiting} waiting to be judged\n\n{TRAILER}\n"


def _token_config() -> tuple[str, ...]:
    """Pass a token to this one git command only. It is never written to the repository's config."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return ()
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return (f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}",)


def run(root: Path | str, *, push: bool = True, dry_run: bool = False, remote: str = "origin") -> dict[str, Any]:
    layout = Layout(root)
    git = Git(layout.root)
    if Path(git.out("rev-parse", "--show-toplevel")).resolve() != layout.root:
        raise PublishBlocked(f"{layout.root} is not the root of a git repository")
    branch = git.run("symbolic-ref", "-q", "--short", "HEAD", check=False).stdout.strip()
    if not branch:
        raise PublishBlocked("HEAD is detached; publishing only commits on a branch")
    git_dir = Path(git.out("rev-parse", "--absolute-git-dir"))
    if any((git_dir / name).exists() for name in _IN_PROGRESS):
        raise PublishBlocked("A merge, rebase or similar git operation is in progress")
    problems = audit.check(layout.root)
    if problems:
        raise PublishBlocked("Audit failed: " + "; ".join(problems[:10]))

    paths = [name for name in audit.GENERATED if (layout.root / name).exists()]
    if paths:
        git.run("add", "-A", "--", *paths)
    staged = git.out("diff", "--cached", "--name-only").splitlines()
    foreign = [path for path in staged if path.split("/")[0] not in audit.GENERATED]
    if foreign:
        raise PublishBlocked("Other changes are staged; commit or unstage them first: " + ", ".join(foreign[:8]))
    result: dict[str, Any] = {"state": "unchanged", "commit": None, "pushed": False, "files": len(staged)}
    if dry_run:
        if staged:
            git.run("reset", "-q", "--", *paths)
        result["state"] = "dry_run"
        return result
    identity = (
        ()
        if git.run("config", "user.email", check=False).stdout.strip()
        else (f"user.name={FALLBACK_IDENTITY[0]}", f"user.email={FALLBACK_IDENTITY[1]}")
    )
    if staged:
        git.run("commit", "-q", "--no-gpg-sign", "-m", _message(layout), config=identity)
        result.update(state="committed", commit=git.out("rev-parse", "--short=12", "HEAD"))

    if push and git.run("remote", "get-url", remote, check=False).returncode == 0:
        pushed = _push(git, remote, branch)
        refused = None
        if _rejected(pushed):
            gained, refused = _replay(git, remote, branch, identity)
            if gained is not None:
                result.update(replayed=gained, commit=git.out("rev-parse", "--short=12", "HEAD"))
                pushed = _push(git, remote, branch)
        if pushed.returncode == 0:
            result["pushed"] = True
            if result["state"] == "committed":
                result["state"] = "published"
        else:
            result["state"] = "stale_base" if _rejected(pushed) else "push_failed"
            result["detail"] = refused or (pushed.stderr.strip().splitlines() or ["push failed"])[-1][:300]
    return result


def _push(git: Git, remote: str, branch: str) -> subprocess.CompletedProcess:
    return git.run("push", "--porcelain", remote, f"HEAD:refs/heads/{branch}", check=False, config=_token_config())


def _rejected(pushed: subprocess.CompletedProcess) -> bool:
    """The remote has commits this one is not built on."""
    return pushed.returncode != 0 and (
        "[rejected]" in pushed.stdout or "non-fast-forward" in pushed.stderr or "fetch first" in pushed.stderr
    )


def _replay(git: Git, remote: str, branch: str, identity: tuple[str, ...]) -> tuple[list[str] | None, str | None]:
    """Put this engine's commit on top of a remote that moved, when nothing it was made from is lost.

    Only when the commit is one this engine made and the one thing the remote lacks, and everything
    the remote gained since is a person's input (profile/ or config/), as when two edits are merged
    seconds apart and each starts a run. A person's own commit is never moved. The verdicts were made
    from the earlier input; the next run judges again whatever the new input changes, exactly as
    after any edit to the profile. Returns the paths the remote gained, or None and the reason the
    run is left for the next one to redo.
    """
    if TRAILER not in git.out("log", "-1", "--format=%B"):
        return None, "the remote has commits this repository lacks, and the commit to push is not one this engine made"
    if git.run("fetch", "-q", remote, branch, check=False, config=_token_config()).returncode:
        return None, "the remote moved, and fetching it failed"
    ours = git.out("rev-parse", "HEAD")
    if git.out("rev-list", "FETCH_HEAD..HEAD").split() != [ours]:
        return None, "the remote moved, and this repository has other commits it lacks"
    base = git.out("merge-base", "HEAD", "FETCH_HEAD")
    gained = git.out("diff", "--name-only", base, "FETCH_HEAD").splitlines()
    outside = [path for path in gained if path.split("/")[0] not in audit.HAND_WRITTEN]
    if outside:
        return None, "the remote moved, with changes outside profile/ and config/: " + ", ".join(outside[:5])
    if git.run("reset", "-q", "--keep", "FETCH_HEAD", check=False).returncode:
        return None, "the remote moved, and the working tree could not follow it"
    if git.run("cherry-pick", ours, check=False, config=(*identity, "commit.gpgsign=false")).returncode:
        git.run("cherry-pick", "--abort", check=False)
        git.run("reset", "-q", "--keep", ours, check=False)
        return None, "the remote moved, and this run's commit does not apply on top of it"
    return gained, None
