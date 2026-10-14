"""Run one judging stage headless: every packet in the manifest goes to its subagent, in batches.

The engine splits the manifest into batches of at most 20 rows and starts one orchestrator per
batch, so no model has to keep count across waves (one that did sometimes dropped the last packet
and reported its own count as complete). Afterwards the engine checks which packets have a result
file, runs one more pass for those that do not, and reports what it counted. The engine never calls
a model API itself: it launches the Claude Code CLI with the tool limits verified in docs/spikes.md.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .layout import Layout
from .packets import AGENTS
from .state import dump_json, write_text

BATCH = 20
STAGES = {agent: stage for stage, agent in AGENTS.items()}
PASSES = 2  # the first pass, then one more for any packet still without a result
TIMEOUT = 1800
CLAUDE = "claude"
PROMPT = (
    "You are the orchestrator of one judging batch. Read {manifest}. Each row after the table header is: "
    "packet | packet file | result file | agent. For every row, start the named subagent, all of them in a single "
    'message, with exactly this instruction: "Process packet <packet file>. Follow the instructions inside it. Write '
    'the result to <result file>. Reply with one line: <packet> done." Do not read the packet files yourself and do '
    "not write any file yourself. If a subagent fails, do not retry it. When every subagent has replied, reply with "
    "one line: batch done."
)

# (feed repo root, manifest path relative to it) -> {"ok", "cost_usd", "denied", optional "detail"}
Invoke = Callable[[Path, str], dict[str, Any]]


def manifest_rows(layout: Layout) -> list[str]:
    lines = layout.manifest.read_text(encoding="utf-8").splitlines() if layout.manifest.exists() else []
    return [line for line in lines if " | " in line and not line.startswith("packet |")]


def missing(layout: Layout, rows: list[str]) -> list[str]:
    """Packets in `rows` without a non-empty result file."""
    packets = [row.split(" | ")[0] for row in rows]
    return [
        p
        for p in packets
        if not (layout.results / f"{p}.txt").is_file() or not (layout.results / f"{p}.txt").stat().st_size
    ]


def claude_invoke(root: Path, manifest: str) -> dict[str, Any]:
    """One headless orchestrator over one batch manifest. Only Agent, Read and Write; writes only to results."""
    command = [
        CLAUDE, "-p", PROMPT.format(manifest=manifest),
        "--model", os.environ.get("HUNTER_ORCHESTRATOR_MODEL", "haiku"),
        "--restricted", "--tools", "Agent,Read,Write", "--agents", ".hunter/agents.json", "--strict-mcp-config",
        "--permission-mode", "dontAsk",
        "--allowedTools", "Agent", "Edit(./.hunter/work/results/**)",
        "--disallowedTools", "Read(./.git/**)",
        "--output-format", "json", "--no-session-persistence",
    ]  # fmt: skip
    try:
        done = subprocess.run(
            command, cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "detail": type(error).__name__}
    try:
        report = json.loads(done.stdout)
    except ValueError:
        return {"ok": False, "detail": (done.stderr.strip().splitlines() or ["no output"])[-1][:200]}
    ok = done.returncode == 0 and not report.get("is_error")
    reply = {
        "ok": ok,
        "cost_usd": report.get("total_cost_usd") or 0.0,
        "denied": len(report.get("permission_denials") or []),
    }
    if not ok:  # Claude Code's own first line, for example "API Error: 400 ..."
        lines = str(report.get("result") or "").strip().splitlines()
        reply["detail"] = (lines or ["the orchestrator reported an error"])[0][:300]
    return reply


def run(root: Path | str, invoke: Invoke | None = None, batch: int = BATCH) -> dict[str, Any]:
    """Judge every packet in the manifest.

    `ok` is true when every packet has a result file, whether the first pass or the retry produced it.
    `failures` lists any orchestrator error along the way, including ones the retry recovered from.
    `judging_failed` is set when not one packet got a result: that fails the command, and the report
    is left in the results folder for `apply` to put in the feed. Some answers are not a failure; the
    packets still missing are issued again next time.
    """
    layout = Layout(root)
    invoke = invoke or claude_invoke
    rows = manifest_rows(layout)
    todo, calls, failures = rows, [], []
    for number in range(1, PASSES + 1):
        if not todo:
            break
        for start in range(0, len(todo), batch):
            # Batch manifests go under results/: outside the seal, and never read as answers.
            name = f"_batch-{number}-{start // batch + 1}.md"
            write_text(
                layout.results / name,
                "packet | packet file | result file | agent\n" + "\n".join(todo[start : start + batch]) + "\n",
            )
            report = invoke(layout.root, (layout.results / name).relative_to(layout.root).as_posix())
            calls.append(report)
            if not report["ok"]:
                failures.append(report.get("detail", "the orchestrator reported an error"))
        left = set(missing(layout, todo))
        todo = [row for row in todo if row.split(" | ")[0] in left]
    unanswered = missing(layout, rows)
    report: dict[str, Any] = {
        "ok": not unanswered,
        "packets": len(rows),
        "results": len(rows) - len(unanswered),
        "missing": unanswered[:20],
        "batches": len(calls),
        "cost_usd": round(sum(c.get("cost_usd", 0.0) for c in calls), 4),
        "denied": sum(c.get("denied", 0) for c in calls),
        **({"failures": failures[:5]} if failures else {}),
    }
    if rows:
        if not report["results"]:
            report["judging_failed"] = True
        stage = STAGES.get(rows[0].rsplit(" | ", 1)[-1].strip(), "judging")
        # Under results/: outside the seal, and not a packet's result file.
        write_text(layout.results / f"_judging-{stage}.json", dump_json(report))
    return report
