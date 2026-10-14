"""One whole tick on this machine: collect, judge, advance, judge, apply, publish.

For a person's terminal and for local timers. A scheduled GitHub workflow runs the same steps as
separate workflow steps instead, so the judging stage never holds the push credential.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from . import advance, apply, collect, publish
from .http import Client
from .layout import Layout

JUDGE = Path(__file__).resolve().parents[1] / "judge.sh"
JUDGE_TIMEOUT = 1800


def judge(root: Path) -> dict[str, Any]:
    """Run one headless judging stage. A failure here is reported, not raised: `apply` still runs."""
    try:
        done = subprocess.run(["sh", str(JUDGE)], cwd=root, capture_output=True, text=True, timeout=JUDGE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "detail": type(error).__name__}
    try:
        report = json.loads(done.stdout)
    except ValueError:
        return {"ok": False, "detail": (done.stderr.strip().splitlines() or ["no output"])[-1][:200]}
    return {
        "ok": done.returncode == 0 and not report.get("is_error"),
        "result": str(report.get("result", ""))[:80],
        "cost_usd": report.get("total_cost_usd"),
        "denied": len(report.get("permission_denials") or []),
    }


def run(root: Path | str, *, push: bool = True, client: Client | None = None) -> dict[str, Any]:
    layout = Layout(root)
    summary: dict[str, Any] = {"collect": collect.run(layout.root, client)}
    seal = summary["collect"]["seal"]
    if summary["collect"]["judge_needed"]:
        if summary["collect"]["triage_packets"]:
            summary["judge_triage"] = judge(layout.root)
        summary["advance"] = advance.run(layout.root, seal)
        seal = summary["advance"]["seal"]
        if summary["advance"]["assess_packets"]:
            summary["judge_assess"] = judge(layout.root)
    summary["apply"] = apply.run(layout.root, seal)
    summary["publish"] = publish.run(layout.root, push=push)
    return summary
