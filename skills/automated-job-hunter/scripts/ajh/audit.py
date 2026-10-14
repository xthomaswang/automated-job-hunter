"""Checks run before anything is committed to a feed repo.

The profile must be contact-free, the files a person writes must not hold credentials, and no file
about to be committed may contain a secret that this process can see in its environment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import decisions
from .layout import Layout

GENERATED = ("README.md", "positions.jsonl", "jobs", "feed", "state")
HAND_WRITTEN = ("profile", "config")
SECRET_ENV = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
)

_CONTACT = [
    ("an email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # A run of digits that is not an ISO date: a date like 2026-10-14 is ten digits and dashes too.
    ("a phone number", re.compile(r"(?<![\w.])(?!\d{4}-\d{2}-\d{2}(?![\d-]))\+?\d[\d\s().-]{8,}\d(?![\w.])")),
    ("a personal profile link", re.compile(r"(?:linkedin\.com/in/|github\.com/|twitter\.com/|x\.com/)[\w.-]+", re.I)),
]
_CREDENTIAL = [
    ("an Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("a GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("an OpenAI key", re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("an AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def _files(root: Path, names: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for name in names:
        path = root / name
        found += (
            [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
        )
    return found


def check(root: Path | str) -> list[str]:
    """Problems that must be fixed before committing; empty when there are none."""
    layout = Layout(root)
    problems: list[str] = []
    for path in _files(layout.root, HAND_WRITTEN):
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(layout.root).as_posix()
        if path == layout.decisions:  # keys, decisions and dates only, so its structure is what to check
            problems += [f"{relative} {why}" for why in decisions.read(path)[1]]
        elif path.parts[-2] == "profile":
            problems += [f"{relative} contains {what}" for what, pattern in _CONTACT if pattern.search(text)]
        problems += [f"{relative} contains {what}" for what, pattern in _CREDENTIAL if pattern.search(text)]
    secrets = {name: os.environ[name] for name in SECRET_ENV if len(os.environ.get(name, "")) >= 12}
    # Denied terms (a name, an employer to keep quiet) are checked only in files a person writes.
    # Posting text is other people's words: a term turning up there must not block the whole feed.
    lines = os.environ.get("AJH_DENYLIST", "").split("\n")
    denied = [(number, term.strip()) for number, term in enumerate(lines, 1) if len(term.strip()) >= 4]
    for path in _files(layout.root, (*GENERATED, *HAND_WRITTEN)) if secrets or denied else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(layout.root).as_posix()
        problems += [f"{relative} contains the value of ${name}" for name, value in secrets.items() if value in text]
        if relative.split("/")[0] in HAND_WRITTEN:
            lowered = text.casefold()
            problems += [
                f"{relative} contains a denied term (entry {number})"
                for number, term in denied
                if term.casefold() in lowered
            ]
    return problems


def warnings(root: Path | str) -> list[str]:
    """Worth knowing, not worth blocking: decisions about postings this feed does not track."""
    layout = Layout(root)
    return [
        f"profile/decisions.jsonl names {key}, which this feed does not track (closed, or on a board it does not "
        "watch); the decision waits for it"
        for key in decisions.unknown(layout, decisions.load(layout.decisions))
    ]
