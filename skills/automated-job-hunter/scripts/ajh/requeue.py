"""Ask for postings to be judged again: the parked ones, or every assessed one.

Parked postings (older than `backfill.days` when their board was first read) go to triage.
Assessed postings get a stale assessment generation, the same state a profile edit leaves them in:
the next updates assess them again, at most `caps.max_assess` at a time, and a listed posting stays
listed meanwhile. A posting the person declined, or applied to and already judged, is left alone.
The change is to `state/`: commit it, and the next update picks it up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import decisions
from .layout import Layout
from .state import State, read_json

# Claude Code's list-price estimates measured in docs/spikes.md, for the summary only.
COST_PER = {"triage": 0.00014, "assess": 0.02}


def run(root: Path | str, what: str) -> dict[str, Any]:
    layout = Layout(root)
    state = State(layout.state)
    decided = decisions.load(layout.decisions)
    stage, count = ("triage", 0) if what == "parked" else ("assess", 0)
    for sid in state.source_ids():
        rows = state.rows(sid)
        for jid, row in rows.items():
            if decisions.never_judge(decided.get(f"{sid}:{jid}"), row):
                continue
            if what == "parked" and row.status == "S":
                row.status = "P"
                count += 1
            elif what == "assessed" and row.status in "ARU" and row.ag:
                row.ag = 0  # older than any generation: assessed again, listed meanwhile
                count += 1
    if count:
        state.save(layout.state, read_json(layout.state / "meta.json", {}).get("as_of", ""))
    return {
        "requeued": count,
        "stage": stage,
        "estimated_cost_usd": round(count * COST_PER[stage], 2),
        "next": "Commit state/ and push; the next updates judge them, within the per-update caps."
        if count
        else "Nothing to requeue.",
    }
