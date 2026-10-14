"""Between the two judging stages: fold in the triage results, then prepare the assessment packets.

Like `collect`, this writes only under `.hunter/work/`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import criteria as C
from . import packets
from .collect import build_queue, feedback_of, job_of, seal_of, split_key
from .layout import Layout
from .model import Job
from .state import State, dump_json, read_json, set_tag, tag, write_text
from .textutil import words_key
from .verdicts import read_triage

CANARY_ONE_IN = 50  # share of triage drops that are assessed anyway, to measure what triage misses


class WorkError(ValueError):
    """The work directory is not what the engine left there. Nothing is folded in; a person looks."""


def check_seal(layout: Layout, seal: str | None) -> None:
    """Refuse work that changed since the engine wrote it.

    Pass the seal the previous step printed when the judging stage could have written the seal
    file; otherwise the copy on disk is used.
    """
    expected = seal or (layout.seal.read_text(encoding="utf-8").strip() if layout.seal.exists() else "")
    if not expected or seal_of(layout.work) != expected:
        raise WorkError("The work directory changed since the engine wrote it, or `collect` has not run")


def load_jobs(layout: Layout) -> dict[str, dict[str, Job]]:
    return {path.stem: {jid: Job.from_dict(data) for jid, data in json.loads(path.read_text(encoding="utf-8")).items()}
            for path in sorted(layout.snapshot.glob("*.json"))}


def is_canary(key: str) -> bool:
    """A fixed one-in-N sample of postings, chosen by the key alone so it needs no stored state."""
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % CANARY_ONE_IN == 0


def run(root: Path | str, seal: str | None = None) -> dict[str, Any]:
    layout = Layout(root)
    check_seal(layout, seal)
    criteria = C.load(layout.criteria, C.Places.bundled())
    profile_text = layout.profile.read_text(encoding="utf-8") if layout.profile.exists() else ""
    plan = read_json(layout.plan, {})
    generations = plan["generations"]
    state = State(layout.state_next)
    jobs = load_jobs(layout)
    index = read_json(layout.packet_index, {})
    max_attempts = criteria["caps"]["max_attempts"]

    counts = {"kept": 0, "dropped": 0, "rejected": 0, "unanswered": 0, "escalated": 0, "canary": 0}
    decided: dict[str, str] = {}
    title_verdicts: dict[str, set[str]] = {}
    for packet, entry in sorted(index.items()):
        if entry["stage"] != "triage":
            continue
        result = layout.results / f"{packet}.txt"
        verdicts, invalid = (read_triage(result.read_text(encoding="utf-8", errors="replace"), packet, entry)
                             if result.is_file() else ({}, {}))
        for local, key in entry["items"].items():
            sid, jid = split_key(key)
            row = state.rows(sid).get(jid)
            if row is None or row.status != "P":  # already folded in by an earlier run of this step
                continue
            if local in verdicts:
                verdict = verdicts[local]
                decided[key] = verdict
                title_verdicts.setdefault(words_key(job_of(jobs, key).title), set()).add(verdict)
                row.tg, row.attempts = generations["triage"], 0
                set_tag(row, "fb", None)
                if verdict == "keep":
                    row.status = "K"
                    counts["kept"] += 1
                else:
                    counts["dropped"] += 1
                    if tag(row, "was"):  # a published posting that no longer fits: its record goes
                        plan["removed"].append(key)
                    row.status, row.note = "D", "-"
                    if is_canary(key):
                        row.status = "K"
                        set_tag(row, "canary", "")
                        counts["canary"] += 1
            elif local in invalid:
                row.attempts += 1
                if row.attempts >= max_attempts:  # triage keeps failing on it: let assessment decide
                    row.status, row.tg, row.attempts = "K", generations["triage"], 0
                    set_tag(row, "fb", None)
                    counts["escalated"] += 1
                else:
                    set_tag(row, "fb", invalid[local])
                    counts["rejected"] += 1
            else:
                counts["unanswered"] += 1

    # A title is remembered as unrelated only when every posting carrying it this tick was judged so.
    for title, verdicts_seen in title_verdicts.items():
        if verdicts_seen == {"drop_title"}:
            state.titles[title] = generations["triage"]

    queue = build_queue(state, jobs, criteria, generations)
    plan["queue"] = queue
    plan["triage"] = counts
    assess_index = packets.write_packets(
        layout, "assess", [(key, job_of(jobs, key)) for key in queue["assess"]], criteria, profile_text,
        feedback=feedback_of(state, queue["assess"]))
    state.save(layout.state_next, plan["as_of"])
    write_text(layout.plan, dump_json(plan))
    new_seal = seal_of(layout.work)
    write_text(layout.seal, new_seal + "\n")
    return {"triage": counts, "assess_queued": len(queue["assess"]), "assess_packets": len(assess_index),
            "deferred": queue["deferred"], "seal": new_seal}
