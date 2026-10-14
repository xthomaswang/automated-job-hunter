"""Read and validate what judging subagents wrote. Result files are untrusted input.

Nothing a model wrote is published as it wrote it. A quote is located in the posting and replaced
by the posting's own wording; a quote that cannot be located is discarded; free text is stripped of
links and secret-shaped strings and cut to length. A verdict missing its essentials is rejected and
the posting is judged again with a fixed-wording note about what was wrong.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .model import Job
from .rules import explicit_zero_experience, numbers_in, preference_only
from .textutil import find_span, norm_ws

TRIAGE_VERDICTS = ("keep", "drop", "drop_title")
ROLE_MATCH = ("direct", "adjacent", "unrelated")
SENIORITY = ("intern", "junior", "mid", "senior", "staff", "unknown")
LIMITS = {"match": 4, "gap": 4, "note": 3}
QUOTE_MIN, QUOTE_MAX, LABEL_MAX, WHY_MAX = 5, 300, 80, 240

_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_SECRET_SHAPED = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_NONE = {"", "none", "null", "n/a", "na", "-", "unknown", "not stated"}


def clean_text(value: str, limit: int) -> str:
    """Model-written prose made safe to publish: no links, no token-like strings, one line, bounded."""
    text = norm_ws(_SECRET_SHAPED.sub("[removed]", _URL.sub("[link removed]", value)))
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _body(text: str, packet: str, nonce: str) -> list[str] | None:
    """The lines after a correct header, or None when the header does not prove this packet was read."""
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    lines = [line for line in lines if line and not line.startswith("```")]
    if not lines or norm_ws(lines[0]).lower() != f"packet {packet} nonce {nonce}".lower():
        return None
    return lines[1:]


def read_triage(text: str, packet: str, entry: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """(verdicts, invalid) by packet-local id. An id in neither was not answered and is simply asked again."""
    ids = entry["items"]
    body = _body(text, packet, entry["nonce"])
    if body is None:
        return {}, dict.fromkeys(ids, "header")
    verdicts: dict[str, str] = {}
    invalid: dict[str, str] = {}
    for line in body:
        found = re.match(r"[-*]?\s*([A-Za-z]\d+)\s*[:=]?\s*(\S*)", line)
        if not found or found.group(1).upper() not in ids:
            continue
        local, verdict = found.group(1).upper(), found.group(2).lower().strip("`.,")
        if verdict not in TRIAGE_VERDICTS:
            invalid.setdefault(local, "verdict")
        elif verdicts.get(local, verdict) != verdict:
            invalid[local] = "conflict"
        else:
            verdicts[local] = verdict
    for local in invalid:
        verdicts.pop(local, None)
    return verdicts, invalid


def _years(value: str) -> float | None:
    if value.strip().lower() in _NONE:
        return None
    found = re.search(r"\d+(?:\.\d+)?", value)
    return float(found.group()) if found else None


def _quote(source: str, quote: str) -> str | None:
    cleaned = quote.strip().strip('"“”')
    for complete in (True, False):  # prefer the whole sentence; fall back to the exact words if that is too long
        span = find_span(source, cleaned, complete=complete)
        if span is not None and QUOTE_MIN <= len(norm_ws(span)) <= QUOTE_MAX:
            return span
    return None


def check_assessment(fields: Mapping[str, list[str]], job: Job) -> tuple[dict[str, Any] | None, list[str]]:
    """One posting's assessment made publishable, or None with the reasons it was rejected.

    Reasons are codes from `rubric.FEEDBACK`. A discarded piece (a quote not found, a years value
    without support) is counted in `unverified` and does not by itself reject the assessment.
    """
    source = job.title + "\n" + job.description
    codes: list[str] = []

    def first(name: str) -> str:
        return (fields.get(name) or [""])[0]

    fit_text = re.match(r"\s*(\d{1,3})(?!\d)", first("fit"))
    fit = int(fit_text.group(1)) if fit_text else None
    if fit is None or fit > 100:
        codes.append("fit")
    role_match = first("role_match").lower().strip("`. ")
    if role_match not in ROLE_MATCH:
        codes.append("role_match")
    why = clean_text(first("why"), WHY_MAX)
    if not why:
        codes.append("why")
    seniority = first("seniority").lower().strip("`. ")

    unverified = 0
    quoted: dict[str, list[dict[str, str]]] = {}
    for kind, limit in LIMITS.items():
        quoted[kind] = []
        for line in fields.get(kind, [])[:limit]:
            label, separator, quote = line.partition("||")
            span = _quote(source, quote) if separator else None
            label = clean_text(label, LABEL_MAX)
            if span is None or not label:
                unverified += 1
                if "quote" not in codes:
                    codes.append("quote")
                continue
            quoted[kind].append({"label": label, "quote": span})

    years: dict[str, float | None] = {}
    for name in ("required_years", "preferred_years"):
        value = _years(first(name))
        if value is not None:
            span = _quote(source, first(name + "_quote"))
            required = name == "required_years"
            problem = (
                "years_quote"
                if span is None
                else "years_number"
                if value != 0 and value not in numbers_in(span)
                else "years_preference"
                if required and preference_only(span)
                else "years_zero"
                if required and value == 0 and not explicit_zero_experience(span)
                else None
            )
            if problem:  # an unsupported number becomes unknown, never a guess
                codes.append(problem)
                unverified += 1
                value = None
        years[name] = value

    if role_match in ("direct", "adjacent") and not quoted["match"] and "no_match" not in codes:
        codes.append("no_match")
    if any(code in codes for code in ("fit", "role_match", "why", "no_match")):
        return None, codes
    assessment = {
        "fit": fit,
        "role_match": role_match,
        "seniority": seniority if seniority in SENIORITY else "unknown",
        "required_years": years["required_years"],
        "preferred_years": years["preferred_years"],
        "why": why,
        "matches": quoted["match"],
        "gaps": quoted["gap"],
        "notes": quoted["note"],
        "unverified": unverified,
    }
    return assessment, codes


def read_assess(
    text: str, packet: str, entry: Mapping[str, Any], jobs: Mapping[str, Job]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """(accepted, rejected) by packet-local id. `jobs` maps each local id to its posting."""
    ids = entry["items"]
    body = _body(text, packet, entry["nonce"])
    if body is None:
        return {}, {local: ["header"] for local in ids}
    sections: dict[str, dict[str, list[str]]] = {}
    repeated: set[str] = set()
    current: dict[str, list[str]] | None = None
    for line in body:
        heading = re.match(r"#{1,4}\s*([A-Za-z]\d+)\b", line)
        if heading:
            local = heading.group(1).upper()
            if local in sections:
                repeated.add(local)
            current = sections.setdefault(local, {}) if local in ids else None
            continue
        name, separator, value = line.partition(":")
        name = name.strip().lstrip("-* ").lower().replace(" ", "_")
        if current is not None and separator and re.fullmatch(r"[a-z_]+", name):
            current.setdefault(name, []).append(value.strip())
    accepted: dict[str, dict[str, Any]] = {}
    rejected: dict[str, list[str]] = {}
    for local, fields in sections.items():
        if local in repeated:
            rejected[local] = ["conflict"]
            continue
        assessment, codes = check_assessment(fields, jobs[local])
        if assessment is None:
            rejected[local] = codes
        else:
            accepted[local] = assessment
    return accepted, rejected
