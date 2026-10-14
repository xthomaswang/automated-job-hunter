"""Stage 0: read every source, compare with committed state, and propose the next state.

`run` writes only under `.hunter/work/`. The proposed state goes to `work/state-next/`; `apply` is
the one step that moves it into the tracked tree. So a tick that dies part-way changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import criteria as C
from . import packets, usage
from . import sources as S
from .http import Client, HttpError
from .layout import Layout
from .model import Job, digest
from .rules import age_days, parse_date
from .state import SOURCE_ID, Row, State, dump_json, read_json, set_tag, tag, write_text
from .textutil import company_key, hamming, simhash64, words_key

MISS_LIMIT = 2  # consecutive absences before a posting is closed
TOMBSTONE_DAYS = 14  # how long a closed posting's verdict is kept in case it reappears
ROLLING_TTL_DAYS = 45  # rolling feeds list only recent postings, so theirs expire by age
SHRINK_RATIO, SHRINK_FLOOR = 0.6, 20  # a board that loses over 40% of its postings is held one tick
NEAR = 12  # simhash bits: 99.9% of measured cosmetic edits are at or under this (docs/spikes.md, E5)
WORKERS = 4
JUDGED = "DKARU"
PUBLISHED = "AU"


class ConfigError(ValueError):
    """config/sources.json needs a person."""


@dataclass
class Seen:
    """A posting from this tick's snapshot that passed stage 0."""

    job: Job
    gk: str
    tfp: str
    simhash: int | None


def load_sources(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, None)
    if not isinstance(data, list):
        raise ConfigError(f"{path} must be a JSON list of sources")
    problems, ids = [], set()
    for entry in data:
        sid = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(sid, str) or not SOURCE_ID.match(sid):
            problems.append(f"source id {sid!r} must be letters, digits, - or _")
            continue
        if sid in ids:
            problems.append(f"source id {sid!r} is repeated")
        ids.add(sid)
        if entry.get("ats") not in S.KINDS:
            problems.append(f"{sid}: unknown source type {entry.get('ats')!r}")
        company = entry.get("company")
        if entry.get("ats") != "aggregator" and not (isinstance(company, str) and company.strip()):
            problems.append(f"{sid}: company is required")
        if entry.get("membership_mode", "snapshot") not in ("snapshot", "rolling"):
            problems.append(f"{sid}: membership_mode must be snapshot or rolling")
        interval = entry.get("min_fetch_interval_seconds", 0)
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval < 0:
            problems.append(f"{sid}: min_fetch_interval_seconds must be a non-negative number")
    if problems:
        raise ConfigError("; ".join(problems))
    return [entry for entry in data if entry.get("enabled", True)]


def fingerprints(job: Job) -> tuple[str, str]:
    """(gk, tfp): postings that may be the same opening, and this posting's title/company/location."""
    company, title = company_key(job.company), words_key(job.title)
    return digest([company, title])[:10], digest([company, title, words_key(job.location)])[:12]


def _day(value: str) -> date:
    return date.fromisoformat(value)


def _tombstone(row: Row, today: date) -> None:
    row.note, row.status, row.miss = f"{row.status}@{today.isoformat()}", "X", 0


def _near(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and hamming(a, b) <= NEAR


def reconcile(
    source: Mapping[str, Any],
    jobs: list[Job],
    rows: dict[str, Row],
    info: dict[str, Any],
    stage0: C.Stage0,
    today: date,
    backfill_days: int | None,
) -> tuple[dict[str, Seen], dict[str, Any]]:
    """Fold one source's successful snapshot into its rows. Returns what was seen and what changed."""
    sid = source["id"]
    first_snapshot = not info.get("ok_day")
    rolling = source.get("membership_mode", "snapshot") == "rolling"
    events: dict[str, Any] = {
        "new": 0,
        "parked": 0,
        "changed": [],
        "revived": [],
        "closed": [],
        "removed": [],
        "dropped": {},
        "held": False,
    }

    seen: dict[str, Seen] = {}
    listed = set()
    for job in jobs:
        listed.add(job.source_id)
        reason = stage0.check(job)
        if reason:
            events["dropped"][reason] = events["dropped"].get(reason, 0) + 1
            continue
        gk, tfp = fingerprints(job)
        seen[job.source_id] = Seen(job, gk, tfp, simhash64(job.description) if job.description else None)

    # Hold closures for one tick when the board shrank sharply or its search scope changed: a
    # half-working feed looks exactly like many postings closing at once.
    scope = digest(source.get("queries"))[:12] if source.get("queries") else None
    rebaselined = not first_snapshot and info.get("scope") != scope
    if scope:
        info["scope"] = scope
    else:
        info.pop("scope", None)
    previous = info.get("count")
    shrank = (
        not rolling and isinstance(previous, int) and previous > SHRINK_FLOOR and len(jobs) < previous * SHRINK_RATIO
    )
    if shrank and not info.get("shrink_seen"):
        info["shrink_seen"] = True
        events["held"] = True
    else:
        info["count"] = len(jobs)
        info.pop("shrink_seen", None)
    hold = events["held"] or rebaselined

    for jid, item in seen.items():
        row = rows.get(jid)
        if row is None:
            parked = (
                first_snapshot
                and backfill_days is not None
                and (age_days(item.job.posted_at, today) or 0) > backfill_days
            )
            rows[jid] = Row(jid, "S" if parked else "P", today.isoformat(), item.gk, item.tfp)
            events["parked" if parked else "new"] += 1
            continue
        if row.status == "X":  # back within the tombstone window: its triage verdict is still good
            row.status, row.note = row.note.split("@")[0] or "P", "-"
            if row.status in PUBLISHED:  # the published details went with the record, so assess again
                row.status, row.dsh, row.fit, row.ag, row.attempts = "K", "-", "-", 0, 0
            events["revived"].append(f"{sid}:{jid}")
        row.miss = 0
        rewritten = row.dsh != "-" and item.simhash is not None and hamming(int(row.dsh, 16), item.simhash) > NEAR
        if row.tfp != item.tfp or rewritten:
            was = row.status
            row.gk, row.tfp = item.gk, item.tfp
            if was in JUDGED:  # judge it again as if new; a published record is refreshed, not dropped
                row.status, row.dsh, row.fit, row.tg, row.ag, row.attempts = "P", "-", "-", 0, 0, 0
                row.note = "-"
                if was in PUBLISHED:
                    set_tag(row, "was", was)
                events["changed"].append(f"{sid}:{jid}")

    for jid in [j for j in rows if j not in seen]:
        row = rows[jid]
        if jid in listed:  # still listed, but it no longer passes the hard constraints
            if row.status in PUBLISHED or tag(row, "was"):
                events["removed"].append(f"{sid}:{jid}")
            del rows[jid]
        elif row.status == "X":
            if (today - _day(row.note.split("@")[1])).days > TOMBSTONE_DAYS:
                del rows[jid]
        elif rolling:
            if (today - _day(row.first_seen)).days > ROLLING_TTL_DAYS:
                _close(row, sid, today, events)
        elif not hold:
            row.miss += 1
            if row.miss >= MISS_LIMIT:
                _close(row, sid, today, events)
    return seen, events


def _close(row: Row, sid: str, today: date, events: dict[str, Any]) -> None:
    if row.status in PUBLISHED or tag(row, "was"):
        events["closed"].append(f"{sid}:{row.id}")
    _tombstone(row, today)


def retire(sid: str, rows: dict[str, Row], today: date) -> list[str]:
    """A source removed from the configuration: close what it had, keeping tombstones briefly."""
    closed = []
    for jid in list(rows):
        row = rows[jid]
        if row.status == "X":
            if (today - _day(row.note.split("@")[1])).days > TOMBSTONE_DAYS:
                del rows[jid]
            continue
        if row.status in PUBLISHED or tag(row, "was"):
            closed.append(f"{sid}:{jid}")
        _tombstone(row, today)
    return closed


def assign_variants(state: State, seen: Mapping[str, Mapping[str, Seen]], kinds: Mapping[str, str]) -> int:
    """Mark postings that need no judgment of their own. Returns how many are variants.

    An aggregator copy of an opening an employer's own board lists (same company and title) defers
    to the official posting. Within one source, postings with the same company and title and
    near-identical text (one opening listed per location) defer to one representative.
    Recomputed from scratch for every source read this tick, so it never drifts.
    """
    for sid in seen:
        for row in state.rows(sid).values():
            if row.status == "V":
                row.status, row.note = "P", "-"
    official: dict[str, str] = {}
    for sid in state.source_ids():
        if kinds.get(sid) == "official":
            for row in state.rows(sid).values():
                if row.status not in "XVS":
                    key = f"{sid}:{row.id}"
                    official[row.gk] = min(key, official.get(row.gk, key))
    count = 0
    for sid, items in seen.items():
        rows = state.rows(sid)
        if kinds.get(sid) == "aggregator":
            for jid in items:
                row = rows[jid]
                if row.status == "P" and row.note == "-" and row.gk in official:
                    row.status, row.note = "V", official[row.gk]
                    count += 1
            continue
        groups: dict[str, list[str]] = {}
        for jid, item in items.items():
            groups.setdefault(item.gk, []).append(jid)
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort()
            judged = [m for m in members if rows[m].status in JUDGED]
            pending = [m for m in members if rows[m].status == "P"]
            lead = (judged or pending or [None])[0]
            for jid in pending:
                if jid != lead and rows[jid].note == "-" and _near(items[jid].simhash, items[lead].simhash):
                    rows[jid].status, rows[jid].note = "V", f"{sid}:{lead}"
                    count += 1
    return count


def advance_generations(state: State, hashes: Mapping[str, str]) -> dict[str, int]:
    """Bump a stage's generation when its inputs changed. A new triage generation re-opens drops."""
    generations = {}
    for stage in ("triage", "assess"):
        entry = state.profile.get(stage) or {}
        generation = entry.get("gen", 0)
        if entry.get("hash") != hashes[stage]:
            generation += 1
            state.profile[stage] = {"hash": hashes[stage], "gen": generation}
            if stage == "triage" and generation > 1:
                state.titles.clear()
                for sid in state.source_ids():
                    for row in state.rows(sid).values():
                        if row.status == "D":
                            row.status, row.tg = "P", 0
        generations[stage] = generation
    return generations


def build_queue(
    state: State, jobs: Mapping[str, Mapping[str, Job]], criteria: Mapping[str, Any], generations: Mapping[str, int]
) -> dict[str, Any]:
    """What to judge this tick, most valuable first, within the per-tick caps.

    Only postings read this tick can be queued, because their text is not stored anywhere else.
    """
    preferred = {company_key(name) for name in criteria["companies"]["prefer"]}
    triage, assess = [], []
    waiting = {"triage": 0, "assess": 0}
    for sid in state.source_ids():
        listed = jobs.get(sid, {})
        for jid, row in state.rows(sid).items():
            stage = (
                "triage"
                if row.status == "P"
                else "assess"
                if row.status == "K" or (row.status in "ARU" and row.ag < generations["assess"])
                else None
            )
            if stage is None:
                continue
            job = listed.get(jid)
            if job is None or (stage == "assess" and not job.description):
                waiting[stage] += 1
                continue
            posted = parse_date(job.posted_at)
            # refresh a published posting first; spend what is left of the cap on canaries last
            rank = (
                tag(row, "was") is None,
                tag(row, "canary") is not None,
                company_key(job.company) not in preferred,
                -(posted.toordinal() if posted else 0),
                f"{sid}:{jid}",
            )
            (triage if stage == "triage" else assess).append(rank)
    caps = criteria["caps"]
    triage.sort()
    assess.sort()
    return {
        "triage": [rank[-1] for rank in triage[: caps["max_triage"]]],
        "assess": [rank[-1] for rank in assess[: caps["max_assess"]]],
        "deferred": {
            "triage": max(0, len(triage) - caps["max_triage"]) + waiting["triage"],
            "assess": max(0, len(assess) - caps["max_assess"]) + waiting["assess"],
        },
    }


def split_key(key: str) -> tuple[str, str]:
    sid, _, jid = key.partition(":")
    return sid, jid


def job_of(jobs: Mapping[str, Mapping[str, Job]], key: str) -> Job:
    sid, jid = split_key(key)
    return jobs[sid][jid]


def feedback_of(state: State, keys: list[str]) -> dict[str, list[str]]:
    """Why each posting's last answer was rejected, for the ones that have such a note."""
    out = {}
    for key in keys:
        sid, jid = split_key(key)
        codes = tag(state.rows(sid)[jid], "fb")
        if codes:
            out[key] = codes.split(",")
    return out


def seal_of(work: Path) -> str:
    """Digest of everything the engine wrote for this tick. `apply` refuses work that fails it."""
    digest_ = hashlib.sha256()
    for path in sorted(p for p in work.rglob("*") if p.is_file()):
        relative = path.relative_to(work)
        if relative.parts[0] == "results" or relative.name == "seal":
            continue
        digest_.update(relative.as_posix().encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest_.hexdigest()[:32]


def _failure(error: Exception) -> str:
    """Short and stable, so a source that keeps failing the same way does not churn the state file."""
    return f"HTTP {error.status}" if isinstance(error, HttpError) else type(error).__name__


def run(root: Path | str, client: Client | None = None, now: datetime | None = None) -> dict[str, Any]:
    layout = Layout(root)
    now = now or datetime.now(timezone.utc)
    today = now.date()
    places = C.Places.bundled()
    criteria = C.load(layout.criteria, places)
    profile_text = layout.profile.read_text(encoding="utf-8") if layout.profile.exists() else ""
    sources = load_sources(layout.sources)
    state = State(layout.state)
    client = client or Client()

    generations = advance_generations(state, C.profile_hashes(criteria, profile_text))
    stage0 = C.Stage0(criteria, places)
    kinds = {s["id"]: "aggregator" if s["ats"] == "aggregator" else "official" for s in sources}

    def due(source: Mapping[str, Any]) -> bool:
        after = state.sources.get(source["id"], {}).get("next_after")
        return not after or now >= datetime.fromisoformat(after)

    to_fetch = [s for s in sources if due(s)]

    def fetch(source: Mapping[str, Any]) -> list[Job] | Exception:
        try:
            return S.fetch(client, source)
        except Exception as error:  # any failure keeps this source's previous snapshot in force
            return error

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fetched = dict(zip((s["id"] for s in to_fetch), pool.map(fetch, to_fetch)))

    summary: dict[str, Any] = {
        "sources": {"fetched": 0, "gated": len(sources) - len(to_fetch), "failed": 0, "failures": {}},
        "new": 0,
        "parked": 0,
        "changed": 0,
        "closed": 0,
        "revived": 0,
        "held": [],
        "dropped": {},
    }
    seen: dict[str, dict[str, Seen]] = {}
    plan: dict[str, Any] = {
        "as_of": today.isoformat(),
        "generations": generations,
        "changed": [],
        "revived": [],
        "closed": [],
        "removed": [],
    }
    for source in to_fetch:
        sid = source["id"]
        info = state.sources.setdefault(sid, {})
        interval = source.get("min_fetch_interval_seconds", 0)
        if interval:  # a failed attempt uses up the interval too
            info["next_after"] = (now + timedelta(seconds=interval)).replace(second=0, microsecond=0).isoformat()
        result = fetched[sid]
        if isinstance(result, Exception):
            info["error"] = _failure(result)
            info.setdefault("fail_since", today.isoformat())
            summary["sources"]["failed"] += 1
            summary["sources"]["failures"][sid] = f"{type(result).__name__}: {str(result)[:200]}"
            continue
        seen[sid], events = reconcile(
            source, result, state.rows(sid), info, stage0, today, criteria["backfill"]["days"]
        )
        info["ok_day"] = today.isoformat()
        info.pop("error", None)
        info.pop("fail_since", None)
        summary["sources"]["fetched"] += 1
        for name in ("new", "parked"):
            summary[name] += events[name]
        for name in ("changed", "revived", "closed"):
            summary[name] += len(events[name])
            plan[name] += events[name]
        plan["removed"] += events["removed"]
        if events["held"]:
            summary["held"].append(sid)
        for reason, count in events["dropped"].items():
            summary["dropped"][reason] = summary["dropped"].get(reason, 0) + count

    configured = {s["id"] for s in sources}
    for sid in state.source_ids():
        if sid not in configured:
            plan["closed"] += retire(sid, state.rows(sid), today)
            state.sources.pop(sid, None)

    summary["variants"] = assign_variants(state, seen, kinds)
    for sid, items in seen.items():  # titles already judged unrelated whatever the duties
        for jid, item in items.items():
            row = state.rows(sid)[jid]
            if row.status == "P" and state.titles.get(words_key(item.job.title)) == generations["triage"]:
                row.status, row.tg = "D", generations["triage"]
    jobs = {sid: {jid: item.job for jid, item in items.items()} for sid, items in seen.items()}
    plan["queue"] = build_queue(state, jobs, criteria, generations)
    budget = usage.budget(criteria["caps"]["max_usd_per_month"], usage.load(layout.usage), today.isoformat())
    if budget:
        plan["budget"] = budget  # apply reports it, even for an update that judged nothing
    paused = bool(budget and budget["reached"])  # postings stay queued until next month; nothing is lost

    if layout.work.exists():
        shutil.rmtree(layout.work)
    for sid, items in seen.items():
        write_text(
            layout.snapshot / f"{sid}.json",
            json.dumps({jid: item.job.to_dict() for jid, item in sorted(items.items())}, ensure_ascii=False),
        )
    state.save(layout.state_next, today.isoformat())
    write_text(layout.plan, dump_json(plan))
    index = packets.write_packets(
        layout,
        "triage",
        [] if paused else [(key, job_of(jobs, key)) for key in plan["queue"]["triage"]],
        criteria,
        feedback=feedback_of(state, plan["queue"]["triage"]),
    )
    seal = seal_of(layout.work)
    write_text(layout.seal, seal + "\n")

    queue = plan["queue"]
    summary.update(
        live=sum(row.status != "X" for sid in state.source_ids() for row in state.rows(sid).values()),
        queued={"triage": len(queue["triage"]), "assess": len(queue["assess"])},
        deferred=queue["deferred"],
        judge_needed=bool(queue["triage"] or queue["assess"]) and not paused,
        **({"budget": budget} if budget else {}),
        triage_packets=len(index),
        seal=seal,
    )
    return summary
