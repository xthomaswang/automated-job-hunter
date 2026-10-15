"""Fixed rules over posting text and titles: eligibility flags, experience bins, dates.

None of this is model output. The flag rules prefer a missed flag to a wrong one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

EXPERIENCE_LEVELS = {"junior_max": 3, "senior_min": 5, "staff_min": 8}


def explicit_zero_experience(text: str) -> bool:
    return bool(
        re.search(
            r"\b0\s*(?:[-–]\s*\d+\s*)?(?:years?|months?)"
            r"|\bno\s+(?:(?:prior|previous|industry|professional|work)\s+)*experience\s+(?:is\s+)?required",
            text,
            re.I,
        )
    )


def experience_level(years: float | None, levels: Mapping[str, float] | None = None) -> str:
    """Mutually exclusive bins from explicit mandatory years, never from a title or a preference."""
    if years is None:
        return "unknown"
    levels = levels or EXPERIENCE_LEVELS
    if years <= levels["junior_max"]:
        return "junior"
    if years < levels["senior_min"]:
        return "mid"
    if years < levels["staff_min"]:
        return "senior"
    return "staff"


_TITLE_SENIORITY = [
    ("staff", r"\b(?:staff|principal|distinguished)\b"),
    ("senior", r"\b(?:senior|sr\.?)\b"),
    ("mid", r"\bmid[ -]?level\b"),
    ("junior", r"\b(?:junior|jr\.?|entry[ -]?level|intern(?:ship)?)\b"),
    ("lead", r"\b(?:lead|head|director|manager)\b"),
    ("associate", r"\bassociate\b"),
]


def title_seniority(title: str) -> str:
    """The seniority a title states, kept separate from the numeric experience bin."""
    return next((label for label, pattern in _TITLE_SENIORITY if re.search(pattern, title, re.I)), "unknown")


def experience_metadata(
    title: str, required_years: float | None, levels: Mapping[str, float] | None = None
) -> dict[str, Any]:
    level = experience_level(required_years, levels)
    title_level = title_seniority(title)
    conflict = title_level in {"junior", "mid", "senior", "staff"} and level != "unknown" and title_level != level
    note = (
        f"Title label is {title_level}; explicit mandatory years map to {level}. Both source facts are retained."
        if conflict
        else None
    )
    return {
        "experience_level": level,
        "title_seniority": title_level,
        "seniority_conflict": conflict,
        "seniority_note": note,
    }


_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_PREFERENCE = re.compile(r"preferred|preferably|ideally|desirable|nice.to.have", re.I)
_MANDATORY = re.compile(r"required|requires|minimum|must|at least", re.I)


def numbers_in(text: str) -> list[float]:
    return [float(n) for n in _NUMBER.findall(text)]


def preference_only(quote: str) -> bool:
    """True when a quote states a preference and nothing mandatory, so it cannot support required years."""
    return bool(_PREFERENCE.search(quote)) and not _MANDATORY.search(quote)


_ALTERNATIVE = re.compile(
    r"\bor\s+(?:an?\s+|the\s+)?equivalent\b|\bequivalent\s+(?:\w+\s+){0,2}?experience\b|\bin\s+lieu\s+of\b"
    r"|\bor\s+(?:\w+\s+){0,4}?(?:related|relevant|comparable)\s+(?:\w+\s+){0,2}?experience\b",
    re.I,
)


def accepts_alternative(quote: str) -> bool:
    """True when a quoted requirement offers another way to meet it, such as equivalent experience.

    Such a requirement is not a hard one, because the model cannot be checked on whether the person
    has the alternative. Erring this way only ever lists a posting the person can rule out themselves.
    """
    return bool(_ALTERNATIVE.search(quote))


_US = r"(?-i:U\.?\s?S\.?)|United\s+States"
_NO_SPONSORSHIP = [
    re.compile(pattern, re.I)
    for pattern in (
        r"\b(?:(?:will|would|do|does|can|could|is|are)\s+not|(?:won|don|doesn|couldn|wouldn|isn|aren)['’]t)\s+(?:\w+\s+){0,4}?sponsor",
        r"\b(?:cannot|can['’]t|unable\s+to|not\s+able\s+to|not\s+in\s+a\s+position\s+to)\s+(?:\w+\s+){0,4}?sponsor",
        r"\bno\s+(?:visa\s+|immigration\s+|work\s+)?sponsorship\b",
        r"\bwithout\s+(?:visa\s+|employer\s+|company\s+)?sponsorship\b",
        r"\bsponsorship\s+(?:is\s+)?(?:not\s+(?:available|offered|provided)|unavailable)\b",
    )
]
_US_CITIZENSHIP = [
    re.compile(pattern, re.I)
    for pattern in (
        rf"\b(?:must|need\s+to|required\s+to|have\s+to)\s+be\s+(?:an?\s+)?(?:{_US})\s+citizen",
        rf"\b(?:{_US})\s+citizenship\s+(?:is\s+)?(?:required|mandatory|needed)",
        rf"\brequires?\s+(?:{_US})\s+citizenship",
        rf"\b(?:{_US})\s+citizens?\s+only",
    )
]
_ALTERNATIVE_STATUS = re.compile(
    r"\bor\b[^.]{0,60}(?:permanent\s+resident|green\s+card|lawful|authoriz|national\b)", re.I
)
_CANDIDATE_NEED = re.compile(r"\b(?:require|requires|required|need|needs)\b", re.I)
_AFFIRMATIVE_SPONSORSHIP = re.compile(
    r"\bwe\s+(?:do|can|will|are\s+able\s+to)\s+sponsor\b|\bsponsorship\s+(?:is\s+)?available\b"
    r"|\bwe\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship\b",
    re.I,
)


def _no_sponsorship(text: str) -> bool:
    """A statement that visas are not sponsored. A candidate who does not *need* sponsorship, or a
    nearby "we do sponsor", is not a hit."""
    return any(
        not _CANDIDATE_NEED.search(m.group(0))
        and not _AFFIRMATIVE_SPONSORSHIP.search(text[max(0, m.start() - 200) : m.end() + 200])
        for rx in _NO_SPONSORSHIP
        for m in rx.finditer(text)
    )


def _us_citizenship(text: str) -> bool:
    """A statement that U.S. citizenship is required, unless the same sentence admits permanent
    residents or other authorization."""
    return any(
        not _ALTERNATIVE_STATUS.search(text[m.end() : m.end() + 90])
        for rx in _US_CITIZENSHIP
        for m in rx.finditer(text)
    )


def posting_flags(description: str) -> tuple[bool, bool]:
    """(sponsorship not offered, U.S. citizenship required), read from the posting text itself.

    False means no such statement was found, not that sponsorship is offered.
    """
    text = " ".join(description.split())
    return _no_sponsorship(text), _us_citizenship(text)


def parse_date(value: Any) -> date | None:
    """The calendar date in a source's posting-date value, or None when absent or unreadable.

    Sources use ISO dates and timestamps, "January 2, 2020", RFC 2822 dates, and epoch seconds or
    milliseconds.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        return parse_date(int(text))
    # Python 3.9's fromisoformat accepts neither a "Z" or " UTC" suffix nor fractions other than 3 or 6 digits.
    iso = re.sub(r"(?<=\d)(?:[Zz]|\s+(?:UTC|GMT))$", "+00:00", text)
    iso = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], iso, count=1)
    for parse in (
        datetime.fromisoformat,
        lambda t: datetime.strptime(t, "%B %d, %Y"),
        lambda t: datetime.strptime(t, "%b %d, %Y"),
        parsedate_to_datetime,
    ):
        try:
            return parse(iso).date()
        except (ValueError, TypeError, IndexError):
            continue
    return None


def age_days(value: Any, today: date) -> int | None:
    posted = parse_date(value)
    return None if posted is None else max(0, (today - posted).days)
