"""Token use and cost of judging, as Claude Code reports them.

`hunter judge` keeps what each orchestrator call reported. `apply` appends one line to
feed/usage.jsonl for each update that judged, or tried to, and sums the log up for feed/meta.json
and the README. The costs are Claude Code's own estimates at list prices: an API key's actual bill
is in the Claude Console, and a subscription draws on plan usage instead. Days and months are UTC.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .state import write_text

SCHEMA_VERSION = 1
STAGES = ("triage", "assess")
TOKENS = ("in", "out", "thinking", "cache_read", "cache_write")
_REPORTED = {
    "in": "inputTokens",
    "out": "outputTokens",
    "thinking": "thinkingTokens",
    "cache_read": "cacheReadInputTokens",
    "cache_write": "cacheCreationInputTokens",
}


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def from_reply(reply: Mapping[str, Any]) -> dict[str, Any]:
    """The usage in one `claude -p --output-format json` reply, per model."""
    models: dict[str, dict[str, Any]] = {}
    for model, numbers in (reply.get("modelUsage") or {}).items():
        if isinstance(numbers, Mapping):
            entry: dict[str, Any] = {name: int(_number(numbers.get(field))) for name, field in _REPORTED.items()}
            entry["cost_usd"] = _number(numbers.get("costUSD"))
            models[str(model)] = entry
    return {
        "models": models,
        "seconds": _number(reply.get("duration_ms")) / 1000,
        "api_seconds": _number(reply.get("duration_api_ms")) / 1000,
        "turns": int(_number(reply.get("num_turns"))),
    }


def combine(parts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Several calls' usage added up, per model."""
    models: dict[str, dict[str, Any]] = {}
    seconds = api_seconds = 0.0
    turns = 0
    for part in parts:
        for model, numbers in (part.get("models") or {}).items():
            entry = models.setdefault(model, {**dict.fromkeys(TOKENS, 0), "cost_usd": 0.0})
            for name in (*TOKENS, "cost_usd"):
                entry[name] += numbers.get(name, 0)
        seconds += part.get("seconds", 0.0)
        api_seconds += part.get("api_seconds", 0.0)
        turns += part.get("turns", 0)
    for entry in models.values():
        entry["cost_usd"] = round(entry["cost_usd"], 4)
    return {
        "models": dict(sorted(models.items())),
        "seconds": round(seconds, 1),
        "api_seconds": round(api_seconds, 1),
        "turns": turns,
    }


def credential() -> str:
    """Which kind of Claude credential this process has, never its value.

    `login` means neither variable is set, so Claude Code uses the account it is logged in with on
    this machine, which may be either kind.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api_key"
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "subscription"
    return "login"


def _where() -> dict[str, str]:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return {"where": "actions", "run": os.environ.get("GITHUB_RUN_ID", "")}
    return {"where": "local"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _per(cost: float, count: int) -> float | None:
    return round(cost / count, 6) if count else None


def entry(
    reports: Mapping[str, Mapping[str, Any]],
    asked: Mapping[str, int],
    new_matches: int,
    error: str | None,
    engine: str,
) -> dict[str, Any]:
    """One line of the log: what this update's judging was asked, got back and cost.

    `reports` are the `hunter judge` reports by stage, `asked` the postings each stage was asked to
    judge. A stage that was asked but left no report, because its step never started, is listed at
    no cost.
    """
    stages: dict[str, Any] = {}
    for stage in STAGES:
        report = reports.get(stage)
        if report is None and not asked.get(stage):
            continue
        report = report or {}
        stages[stage] = {
            "postings": asked.get(stage, 0),
            "packets": report.get("packets", 0),
            "results": report.get("results", 0),
            "missing": report.get("packets", 0) - report.get("results", 0),
            "cost_usd": round(_number(report.get("cost_usd")), 4),
            **(report.get("usage") or combine([])),
        }
    total: dict[str, Any] = dict.fromkeys(TOKENS, 0)
    for stage in stages.values():
        for numbers in stage["models"].values():
            for name in TOKENS:
                total[name] += numbers[name]
    total["cost_usd"] = round(sum(stage["cost_usd"] for stage in stages.values()), 4)
    triage, assess = stages.get("triage", {}), stages.get("assess", {})
    line: dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "at": now(),
        **_where(),
        "credential": next((r["credential"] for r in reports.values() if r.get("credential")), None),
        "engine": engine,
        "stages": stages,
        "total": total,
        "per_posting_triaged_usd": _per(triage.get("cost_usd", 0.0), triage.get("postings", 0)),
        "per_posting_assessed_usd": _per(assess.get("cost_usd", 0.0), assess.get("postings", 0)),
        "per_new_match_usd": _per(total["cost_usd"], new_matches),
    }
    if error:
        line["error"] = error
    return line


def load(path: Path) -> list[dict[str, Any]]:
    """The log, oldest first. A line that does not parse is skipped rather than stopping the feed."""
    lines = []
    for text in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            line = json.loads(text)
        except ValueError:
            continue
        if isinstance(line, dict):
            lines.append(line)
    return lines


def append(path: Path, line: Mapping[str, Any]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    write_text(path, existing + json.dumps(line, ensure_ascii=False) + "\n")


def _tally(lines: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"updates": 0, **dict.fromkeys(TOKENS, 0), "cost_usd": 0.0}
    for line in lines:
        total = line.get("total") or {}
        out["updates"] += 1
        out["cost_usd"] += _number(total.get("cost_usd"))
        for name in TOKENS:
            out[name] += int(_number(total.get(name)))
    out["cost_usd"] = round(out["cost_usd"], 4)
    return out


def _day(line: Mapping[str, Any]) -> str:
    return str(line.get("at", ""))[:10]


def summary(lines: list[Mapping[str, Any]], as_of: str) -> dict[str, Any] | None:
    """Totals for the last update that judged, the last 7 days, the month so far and all time."""
    if not lines:
        return None
    week_from = (date.fromisoformat(as_of) - timedelta(days=6)).isoformat()
    return {
        "last_update": {"at": lines[-1].get("at"), **_tally(lines[-1:])},
        "last_7_days": _tally(line for line in lines if _day(line) >= week_from),
        "month": {"name": as_of[:7], **_tally(line for line in lines if _day(line)[:7] == as_of[:7])},
        "all_time": _tally(lines),
    }
