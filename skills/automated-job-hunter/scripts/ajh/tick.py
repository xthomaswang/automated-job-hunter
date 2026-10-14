"""One whole tick on this machine: collect, judge, advance, judge, apply, publish.

For a person's terminal and for local timers. A scheduled GitHub workflow runs the same steps as
separate workflow steps instead, so the judging stage never holds the push credential.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import advance, apply, collect, publish
from . import judge as judging
from .http import Client
from .layout import Layout


def judge(root: Path) -> dict[str, Any]:
    """Run one headless judging stage. A failure is reported, not raised: `apply` still runs."""
    return judging.run(root)


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
