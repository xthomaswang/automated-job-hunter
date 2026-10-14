"""Write the packets judging subagents read, and the manifest that says who reads which.

Packets are sized by bytes because a subagent's file read is capped (about 25,000 tokens) and an
over-cap read silently returns a partial page. Posting text is indented four spaces: that marks it
as data, keeps a posting from imitating packet structure, and costs nothing when a quote is matched
because matching ignores whitespace layout.
"""
from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import rubric
from .layout import Layout
from .model import Job
from .state import dump_json, read_json, write_text
from .textutil import norm_ws, snippet, wrap

MAX_BYTES = 48_000
MAX_LINES = 1_500
WIDTH = 100
PER_PACKET = {"triage": 45, "assess": 5}
SNIPPET_CHARS = 1_200
DESCRIPTION_MAX = 16_000
PROFILE_MAX = 12_000
AGENTS = {"triage": "hunter-triage", "assess": "hunter-assess"}
_PREFIX = {"triage": "T", "assess": "A"}


class ProfileTooLong(ValueError):
    """profile.md does not fit in a packet next to a posting; a person has to shorten it."""


def _level(seniority: Mapping[str, Any]) -> str:
    low, high, years = seniority["min"], seniority["max"], seniority["years"]
    span = (f"{low} to {high}" if low and high and low != high else low if low and high
            else f"{low} or above" if low else f"up to {high}" if high else "")
    experience = f"about {years:g} years of experience" if years is not None else ""
    return ", ".join(part for part in (span, experience) if part)


def seeker_brief(criteria: Mapping[str, Any]) -> str:
    """What triage needs to know: the kinds of work wanted. Built only from the triage-hashed fields."""
    targets = criteria["targets"]
    lines = ["Wanted roles: " + "; ".join(targets["roles"])]
    if targets["adjacent"]:
        lines.append("Also open to: " + "; ".join(targets["adjacent"]))
    if targets["avoid"]:
        lines.append("Not wanted: " + "; ".join(targets["avoid"]))
    if _level(criteria["seniority"]):
        lines.append("Level: " + _level(criteria["seniority"]))
    return "\n".join(lines)


def seeker_profile(criteria: Mapping[str, Any], profile_text: str) -> str:
    """What assessment needs to know: the brief, the stated preferences, and the profile itself."""
    profile = profile_text.strip()
    if len(profile) > PROFILE_MAX:
        raise ProfileTooLong(f"profile/profile.md is {len(profile)} characters; keep it under {PROFILE_MAX}")
    lines = [seeker_brief(criteria)]
    if criteria["employment"]:
        lines.append("Employment wanted: " + ", ".join(kind.replace("_", " ") for kind in criteria["employment"]))
    locations = criteria["locations"]
    if locations["regions"]:
        lines.append("Countries: " + ", ".join(locations["regions"]))
    if locations["cities"]:
        lines.append("Preferred cities: " + "; ".join(locations["cities"]))
    if locations["remote"] != "any":
        lines.append("Remote work: " + ("remote only" if locations["remote"] == "only" else "not remote"))
    sponsorship = criteria["eligibility"]["needs_sponsorship"]
    if sponsorship is not None:
        lines.append("Visa sponsorship: " + ("needs it" if sponsorship else "does not need it"))
    if profile:
        lines += ["", "Profile:", wrap(profile, WIDTH)]
    return "\n".join(lines)


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in wrap(text, WIDTH - 4).split("\n"))


def _feedback(codes: Sequence[str]) -> list[str]:
    reasons = "; ".join(rubric.FEEDBACK[code] for code in codes if code in rubric.FEEDBACK)
    if not reasons:
        return []
    return [wrap(f"An earlier answer for this posting was rejected: {reasons}. Answer it again.", WIDTH)]


def _section(stage: str, local: str, job: Job, codes: Sequence[str], snippet_chars: int) -> tuple[str, bool]:
    """One posting's block and whether its text had to be cut."""
    lines = [f"### {local}", f"title: {norm_ws(job.title)}", f"company: {norm_ws(job.company)}",
             f"location: {norm_ws(job.location)}"]
    if job.department:
        lines.append(f"department: {norm_ws(job.department)}")
    cut = False
    if stage == "triage":
        lines += _feedback(codes)
        shown = snippet(job.description, snippet_chars)
        if shown:
            lines += ["snippet:", _indent(shown)]
    else:
        if job.employment_type and job.employment_type != "unknown":
            lines.append(f"employment: {norm_ws(job.employment_type)}")
        if job.posted_at:
            lines.append(f"posted: {norm_ws(job.posted_at)}")
        lines += _feedback(codes)
        text = job.description
        if len(text) > DESCRIPTION_MAX:
            text, cut = text[:DESCRIPTION_MAX].rsplit(None, 1)[0], True
        lines += ["text:", _indent(text)]
        if cut:
            lines.append("[the posting continues; the rest was left out for length]")
    return "\n".join(lines) + "\n", cut


def write_packets(layout: Layout, stage: str, entries: Sequence[tuple[str, Job]], criteria: Mapping[str, Any],
                  profile_text: str = "", *, feedback: Mapping[str, Sequence[str]] | None = None,
                  snippet_chars: int = SNIPPET_CHARS, token: Callable[[int], str] = secrets.token_hex
                  ) -> dict[str, Any]:
    """Write one stage's packets and manifest. Returns that stage's index: packet -> nonce and items."""
    template = rubric.TRIAGE if stage == "triage" else rubric.ASSESS
    seeker = seeker_brief(criteria) if stage == "triage" else seeker_profile(criteria, profile_text)
    head_room = len(template.encode("utf-8")) + len(seeker.encode("utf-8")) + 200
    head_lines = template.count("\n") + seeker.count("\n") + 10
    prefix = _PREFIX[stage]

    groups: list[list[tuple[str, str, str, bool]]] = [[]]
    size, lines = head_room, head_lines
    for number, (key, job) in enumerate(entries, 1):
        local = f"{prefix}{number:04d}"
        section, cut = _section(stage, local, job, (feedback or {}).get(key, ()), snippet_chars)
        cost, height = len(section.encode("utf-8")) + 1, section.count("\n") + 1
        full = len(groups[-1]) >= PER_PACKET[stage] or size + cost > MAX_BYTES or lines + height > MAX_LINES
        if groups[-1] and full:
            groups.append([])
            size, lines = head_room, head_lines
        groups[-1].append((local, key, section, cut))
        size, lines = size + cost, lines + height

    index: dict[str, Any] = {}
    for number, group in enumerate((g for g in groups if g), 1):
        packet = f"{prefix}-{number:03d}"
        nonce = token(2)
        head = template.format(packet=packet, nonce=nonce, count=len(group), example=group[0][0], seeker=seeker)
        write_text(layout.packets / f"{packet}.md", head + "\n" + "\n".join(section for _, _, section, _ in group))
        index[packet] = {"stage": stage, "nonce": nonce, "items": {local: key for local, key, _, _ in group},
                         "cut": [local for local, _, _, cut in group if cut]}

    merged = {name: entry for name, entry in read_json(layout.packet_index, {}).items() if entry["stage"] != stage}
    merged.update(index)
    write_text(layout.packet_index, dump_json(merged))
    rows = [f"{packet} | {(layout.packets / (packet + '.md')).relative_to(layout.root).as_posix()} | "
            f"{(layout.results / (packet + '.txt')).relative_to(layout.root).as_posix()} | {AGENTS[stage]}"
            for packet in index]
    write_text(layout.manifest, "\n".join([f"# Judging manifest: {stage}", "",
                                           "packet | packet file | result file | agent", *rows]) + "\n")
    return index
