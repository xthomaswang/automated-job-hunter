"""The single step that changes tracked files: fold in assessments, update the feed, adopt the state.

It runs even when a judging stage was cut off, so whatever was judged still lands. It refuses to
run when the work directory or the tracked tree is not what the engine left, because between
`collect` and here a model had write access to a results folder and nothing else.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import __version__, decisions, feed, usage
from . import criteria as C
from .advance import WorkError, check_seal, load_jobs
from .collect import job_of, split_key
from .layout import Layout
from .state import State, dump_json, read_json, set_tag, tag, write_text
from .textutil import simhash64
from .verdicts import clean_text, read_assess


def check_tree(layout: Layout) -> None:
    """Refuse to build on a tracked tree with uncommitted changes."""
    if not (layout.root / ".git").exists():
        return
    status = subprocess.run(
        ["git", "-C", str(layout.root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if status.returncode:
        raise WorkError(f"git status failed: {status.stderr.strip()[:200]}")
    dirty = [line[3:] for line in status.stdout.splitlines()]
    if dirty:
        raise WorkError(
            "The tracked tree has uncommitted changes (commit or discard them first): " + ", ".join(dirty[:8])
        )


def _counts(state: State, assess_generation: int, decided: decisions.Decided) -> dict[str, int]:
    tally = dict.fromkeys("PDKARUVSX", 0)
    stale = 0  # assessed under an earlier profile and queued to be assessed again; still listed meanwhile
    mine = {"applied": 0, "declined": 0}
    for sid in state.source_ids():
        for jid, row in state.rows(sid).items():
            decision = decided.get(f"{sid}:{jid}")
            if decision:
                mine[decision["decision"]] += 1
                if decision["decision"] == "declined":
                    continue  # counted only as declined: never listed, never waiting
            settled = decisions.never_judge(decision, row)  # the same rule the queue follows
            if row.status in "PK" and settled:
                continue
            tally[row.status] += 1
            stale += row.status in "ARU" and row.ag < assess_generation and not settled
    return {
        **mine,
        "published": tally["A"] + tally["U"],
        "pending_triage": tally["P"],
        "pending_assess": tally["K"],
        "pending_reassess": stale,
        "below_threshold": tally["R"],
        "dropped": tally["D"],
        "variants": tally["V"],
        "parked": tally["S"],
    }


def run(root: Path | str, seal: str | None = None) -> dict[str, Any]:
    layout = Layout(root)
    check_seal(layout, seal)
    check_tree(layout)
    criteria = C.load(layout.criteria, C.Places.bundled())
    plan = read_json(layout.plan, {})
    as_of, generations = plan["as_of"], plan["generations"]
    state = State(layout.state_next)
    jobs = load_jobs(layout)
    index = read_json(layout.packet_index, {})
    records = feed.load_positions(layout.positions)
    decided = decisions.load(layout.decisions)
    old_meta = read_json(layout.feed_meta, {})
    canary = dict(old_meta.get("canary") or {"sampled": 0, "missed": 0})
    counts = {"published": 0, "below_threshold": 0, "rejected": 0, "unscored": 0, "unanswered": 0}
    events: list[dict[str, Any]] = []
    touched: set[str] = set()

    def publish(key: str, assessment: dict[str, Any] | None) -> None:
        sid, jid = split_key(key)
        record = feed.build_record(
            job_of(jobs, key), state.rows(sid)[jid].first_seen, assessment, criteria, generations["assess"]
        )
        previous = records.get(key)
        if previous is None:
            events.append(feed.event("added", as_of, record))
        elif (previous["fit"], previous["tier"]) != (record["fit"], record["tier"]):
            events.append(feed.event("rescored", as_of, record))
        records[key] = record
        touched.add(key)

    for packet, entry in sorted(index.items()):
        if entry["stage"] != "assess":
            continue
        result = layout.results / f"{packet}.txt"
        by_local = {local: job_of(jobs, key) for local, key in entry["items"].items()}
        accepted, rejected = (
            read_assess(result.read_text(encoding="utf-8", errors="replace"), packet, entry, by_local)
            if result.is_file()
            else ({}, {})
        )
        for local, key in entry["items"].items():
            sid, jid = split_key(key)
            row = state.rows(sid).get(jid)
            if row is None or row.status not in "KARU":
                continue
            if local in accepted:
                assessment = accepted[local]
                sampled = tag(row, "canary") is not None
                text = by_local[local].description
                row.fit, row.ag, row.attempts, row.note = str(assessment["fit"]), generations["assess"], 0, "-"
                row.tg = row.tg or generations["triage"]
                row.dsh = f"{simhash64(text):016x}" if text else "-"  # frozen here: later edits are measured against it
                if feed.tier_of(assessment, criteria["thresholds"]):
                    row.status = "A"
                    publish(key, assessment)
                    counts["published"] += 1
                else:
                    row.status = "R"
                    counts["below_threshold"] += 1
                if sampled:
                    canary["sampled"] += 1
                    canary["missed"] += row.status == "A"
            elif local in rejected:
                row.attempts += 1
                if row.attempts >= criteria["caps"]["max_attempts"]:
                    # Repeatedly unusable answers: publish the facts alone rather than lose the posting.
                    row.status, row.fit, row.ag, row.attempts, row.note = "U", "-", generations["assess"], 0, "-"
                    publish(key, None)
                    counts["unscored"] += 1
                else:
                    set_tag(row, "fb", ",".join(rejected[local]))
                    counts["rejected"] += 1
            else:
                counts["unanswered"] += 1

    # Records follow the state: a posting stays published while it is A or U, or is being re-judged.
    keep = set()
    declined = {key for key, decision in decided.items() if decision["decision"] == "declined"}
    for sid in state.source_ids():
        for jid, row in state.rows(sid).items():
            key = f"{sid}:{jid}"
            if key in declined:  # its record goes, and it is never assessed again
                continue
            if row.status in "AU" or tag(row, "was"):
                if key in records:
                    keep.add(key)
                elif row.status in "AU":  # the record is gone: assess it again rather than guess
                    row.status, row.ag, row.fit, row.dsh = "K", 0, "-", "-"
    closed = set(plan.get("closed", []))
    for key in sorted(set(records) - keep):
        record = records.pop(key)
        kind = "declined" if key in declined else "closed" if key in closed else "removed"
        events.append(feed.event(kind, as_of, record))
        feed.remove_brief(layout.root, record)

    # Other listings of the same opening, refreshed for every source read this tick.
    listed: dict[str, dict[str, list[dict[str, str]]]] = {}
    for sid, items in jobs.items():
        for jid, job in items.items():
            row = state.rows(sid).get(jid)
            if row is not None and row.status == "V" and row.note in records:
                kind = "also_on" if job.source_kind == "aggregator" else "variants"
                entry = (
                    {"key": job.key, "label": job.source_label or sid, "url": job.url}
                    if kind == "also_on"
                    else {"key": job.key, "location": job.location, "url": job.url}
                )
                listed.setdefault(row.note, {}).setdefault(kind, []).append(entry)
    for key, record in records.items():
        for kind in ("variants", "also_on"):
            # a source that was not read this tick says nothing about its listings, so those stay
            unread = [item for item in record[kind] if split_key(item["key"])[0] not in jobs]
            current = sorted([*unread, *listed.get(key, {}).get(kind, [])], key=lambda item: item["key"])
            if current != record[kind]:
                record[kind] = current
                touched.add(key)

    for key in sorted(touched):
        sid, jid = split_key(key)
        job = jobs.get(sid, {}).get(jid)
        if key in records and job is not None:  # without this tick's text the brief cannot be rewritten
            feed.write_brief(layout.root, records[key], job.description)
    applied = {key: decision["date"] for key, decision in decided.items() if decision["decision"] == "applied"}
    for key, record in records.items():  # the person's own mark, kept apart from the assessment
        if key in applied and record.get("applied") != applied[key]:
            record["applied"] = applied[key]
        elif key not in applied:
            record.pop("applied", None)
    write_text(layout.positions, feed.positions_text(records))
    order = {"added": 0, "rescored": 1, "closed": 2, "removed": 3, "declined": 4}
    events.sort(key=lambda item: (order[item["event"]], item["key"]))
    if events:
        feed.append_events(layout.feed, events)
        write_text(layout.latest, feed.latest_text(as_of, events, applied))

    state.save(layout.state_next, as_of)
    failing = {sid: info["fail_since"] for sid, info in state.sources.items() if info.get("fail_since")}
    asked = _asked(plan.get("triage") or {}, counts)
    judging_failed = _judging_failed(layout, asked, old_meta.get("judging_failed"), as_of)
    log = usage.load(layout.usage)
    reports = {stage: read_json(layout.results / f"_judging-{stage}.json", {}) for stage in usage.STAGES}
    reports = {stage: report for stage, report in reports.items() if report}
    unanswered = _unanswered(asked)
    if reports or unanswered:  # an update that judged, or tried to; one that only read the boards adds nothing
        line = usage.entry(
            reports,
            {stage: count for stage, (count, _) in asked.items()},
            sum(item["event"] == "added" for item in events),
            judging_failed["detail"] if unanswered and judging_failed else None,
            __version__,
        )
        usage.append(layout.usage, line)
        log.append(line)
    used = usage.summary(log, as_of)
    budget = usage.budget(criteria["caps"]["max_usd_per_month"], log, as_of)
    if budget:
        used = {**(used or {}), "budget": {**budget, "paused": plan.get("budget", {}).get("reached", False)}}
    meta = {
        "schema_version": feed.SCHEMA_VERSION,
        "as_of": as_of,
        "counts": _counts(state, generations["assess"], decided),
        "canary": canary,
        "generations": generations,
        "sources": {"total": len(state.sources), "failing": failing},
        **({"judging_failed": judging_failed} if judging_failed else {}),
        **({"usage": used} if used else {}),
    }
    write_text(layout.feed_meta, dump_json(meta))
    write_text(layout.readme, feed.readme_text(meta, records))
    if layout.state.exists():
        shutil.rmtree(layout.state)
    shutil.copytree(layout.state_next, layout.state)

    return {
        **counts,
        "open_matches": len(records),
        **{kind: sum(item["event"] == kind for item in events) for kind in order},
    }


def _asked(triage: dict[str, int], assess: dict[str, int]) -> dict[str, tuple[int, int]]:
    """Per stage: how many postings this tick asked to be judged, and how many got no answer."""
    return {
        "triage": (
            sum(triage.get(name, 0) for name in ("kept", "dropped", "rejected", "escalated", "unanswered")),
            triage.get("unanswered", 0),
        ),
        "assess": (
            sum(assess[name] for name in ("published", "below_threshold", "rejected", "unscored", "unanswered")),
            assess["unanswered"],
        ),
    }


def _unanswered(asked: dict[str, tuple[int, int]]) -> list[str]:
    """The stages that were asked for verdicts and got none back."""
    return [stage for stage, (count, missing) in asked.items() if count and missing == count]


def _judging_failed(
    layout: Layout, asked: dict[str, tuple[int, int]], previous: dict[str, Any] | None, as_of: str
) -> dict[str, Any] | None:
    """The judging stages that were asked for verdicts this tick and got none back, or None.

    The detail is the first line of the error `hunter judge` recorded, made safe to publish. A tick
    that asked for no verdicts says nothing new, so an earlier failure stays reported until a stage
    gets answers again.
    """
    if not any(count for count, _ in asked.values()):
        return previous
    stages = _unanswered(asked)
    if not stages:
        return None
    record = read_json(layout.results / f"_judging-{stages[0]}.json", {})
    detail = clean_text(next(iter(record.get("failures") or []), ""), 200)
    return {
        "since": previous["since"] if previous else as_of,
        "stages": stages,
        "detail": detail or "No answers came back; the judging step's log says why.",
    }
