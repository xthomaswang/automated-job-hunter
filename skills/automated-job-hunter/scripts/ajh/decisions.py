"""The person's own decisions about postings: applied to, or declined.

profile/decisions.jsonl holds one line per decision, exactly {"key", "decision", "date"}, written
by `hunter decide` or by the person's agent; the last line for a key wins. A declined posting is
never judged or listed again. One applied to is judged once, like any posting, never again after
that, and listed with a mark. The file is in neither judging hash, so a decision re-judges nothing,
and it holds no free text, so nothing personal beyond the keys and dates can get into it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .layout import Layout
from .state import Row, State, StateError, tag, write_text

KINDS = ("applied", "declined")
FIELDS = ("key", "decision", "date")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

Decided = Mapping[str, Mapping[str, str]]  # key -> {"decision", "date"}


class DecisionError(ValueError):
    """A decision that cannot be recorded; the message says why."""


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def problem(line: Any) -> str | None:
    """Why a line is not a valid decision, or None when it is one."""
    if not isinstance(line, dict):
        return "is not a JSON object"
    extra = sorted(set(line) - set(FIELDS))
    if extra:
        return f"has fields other than key, decision and date: {', '.join(map(str, extra))[:80]}"
    if set(line) != set(FIELDS):
        return "needs key, decision and date"
    key = line["key"]
    source, _, job = key.partition(":") if isinstance(key, str) else ("", "", "")
    if not source or not job:
        return f"has a key that is not <source>:<id>: {str(key)[:60]!r}"
    if line["decision"] not in KINDS:
        return f"has a decision other than applied or declined: {str(line['decision'])[:30]!r}"
    if not _is_date(line["date"]):
        return f"has a date that is not YYYY-MM-DD: {str(line['date'])[:30]!r}"
    return None


def read(path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """The decisions by key, the last line for a key winning, and the problems found by line."""
    decided: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines() if path.exists() else [], 1):
        if not text.strip():
            continue
        try:
            line = json.loads(text)
        except ValueError:
            problems.append(f"line {number} is not JSON")
            continue
        why = problem(line)
        if why:
            problems.append(f"line {number} {why}")
            continue
        decided[line["key"]] = {"decision": line["decision"], "date": line["date"]}
    return decided, problems


def load(path: Path) -> dict[str, dict[str, str]]:
    """The valid decisions. A line `audit` would reject is left out rather than stopping the feed."""
    return read(path)[0]


def never_judge(decision: Mapping[str, str] | None, row: Row) -> bool:
    """Whether a posting must not be queued for judging, nor counted as waiting to be judged.

    Declined: never again. Applied: judged once like any posting, then never again, neither after a
    search change (an assessment from an earlier profile) nor after its text changed (a published
    posting sent back to triage carries `was`).
    """
    if decision is None:
        return False
    if decision["decision"] == "declined":
        return True
    return row.status in "ARU" or tag(row, "was") is not None


def unknown(layout: Layout, decided: Decided) -> list[str]:
    """Decided keys the feed does not track: closed long ago, or on a board it does not watch."""
    state = State(layout.state)
    missing = []
    for key in decided:
        source, _, job = key.partition(":")
        try:
            known = job in state.rows(source)
        except StateError:
            known = False
        if not known:
            missing.append(key)
    return missing


def record(root: Path | str, decision: str, key: str, day: str | None = None) -> dict[str, Any]:
    """Append one decision to profile/decisions.jsonl. The person commits it; the next update applies it."""
    layout = Layout(root)
    line = {"key": key, "decision": decision, "date": day or datetime.now(timezone.utc).date().isoformat()}
    why = problem(line)
    if why:
        raise DecisionError(f"The decision {why}")
    existing = layout.decisions.read_text(encoding="utf-8") if layout.decisions.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    write_text(layout.decisions, existing + json.dumps(line) + "\n")
    known = not unknown(layout, {key: line})
    return {
        "recorded": line,
        "known": known,
        "next": "Commit profile/decisions.jsonl; the next update applies it."
        + ("" if known else " This feed does not track that key (yet); the decision waits for it."),
    }
