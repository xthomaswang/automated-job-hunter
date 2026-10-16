"""The user's search criteria: validation, the stage-0 hard constraints, and profile hashes.

Stage 0 drops a posting only on positive evidence that it breaks an explicit constraint. Anything
it cannot read (an unknown employment label, a place it does not recognize) passes.
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .model import Job, digest
from .textutil import company_key

EMPLOYMENT_KINDS = ("full_time", "part_time", "contract", "internship", "temporary")
SENIORITY = ("intern", "junior", "mid", "senior", "staff")
UNMET_KINDS = ("degree", "enrollment", "credential", "license")
RULE_PACKS = ("us",)

DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "targets": {"roles": [], "adjacent": [], "avoid": []},
    "seniority": {"min": None, "max": None, "years": None},
    "employment": [],  # empty means any
    "locations": {"remote": "any", "regions": [], "cities": []},  # empty regions means anywhere
    "eligibility": {"needs_sponsorship": None, "rule_packs": ["us"]},
    "companies": {"prefer": [], "exclude": []},
    # Listing only, never a verdict, so in neither judging hash. direct_min: the lowest fit at which a
    # wanted role is still listed (null: always). max_required_years, max_seniority and
    # unmet_requirements are hard requirements: a posting stating more years, assessed as more senior,
    # or quoted as requiring something of a listed kind that the profile does not meet, is left out.
    "thresholds": {
        "strong": 75,
        "good": 55,
        "publish_min": 40,
        "direct_min": None,
        "max_required_years": None,
        "max_seniority": None,
        "unmet_requirements": [],
    },
    # max_usd_per_month: judging pauses for the rest of the month (UTC) once the usage log says
    # Claude Code's list-price estimate has reached it. Null means no budget.
    "caps": {"max_triage": 1500, "max_assess": 100, "max_attempts": 3, "max_usd_per_month": None},
    # A number parks postings older than that many days when a source is first read. The default
    # judges everything, newest first: live boards keep most postings open far longer than a month.
    "backfill": {"days": None},
}


class CriteriaError(ValueError):
    """criteria.json needs a person: the message lists every problem found."""


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def _int(value: Any, low: int, high: int | None = None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low and (high is None or value <= high)


def normalize(data: Any, places: Places | None = None) -> dict[str, Any]:
    """Fill defaults and validate. Raises CriteriaError naming every problem."""
    if not isinstance(data, dict):
        raise CriteriaError("criteria.json must be a JSON object")
    problems = [f"unknown key {key!r}" for key in data if key not in DEFAULTS]
    out = copy.deepcopy(DEFAULTS)
    for key, default in DEFAULTS.items():
        if key not in data:
            continue
        if isinstance(default, dict):
            if not isinstance(data[key], dict):
                problems.append(f"{key} must be an object")
                continue
            problems += [f"unknown key {key}.{sub}" for sub in data[key] if sub not in default]
            out[key].update({sub: value for sub, value in data[key].items() if sub in default})
        else:
            out[key] = data[key]

    def check(ok: bool, message: str) -> None:
        if not ok:
            problems.append(message)

    check(out["schema_version"] == 1, "schema_version must be 1")
    targets, seniority, locations = out["targets"], out["seniority"], out["locations"]
    check(_strings(targets["roles"]) and bool(targets["roles"]), "targets.roles needs at least one role")
    check(
        _strings(targets["adjacent"]) and _strings(targets["avoid"]),
        "targets.adjacent and targets.avoid must be lists of text",
    )
    for bound in ("min", "max"):
        check(
            seniority[bound] is None or seniority[bound] in SENIORITY,
            f"seniority.{bound} must be one of {SENIORITY} or null",
        )
    years = seniority["years"]
    check(
        years is None or (isinstance(years, (int, float)) and not isinstance(years, bool) and 0 <= years <= 60),
        "seniority.years must be a number of years or null",
    )
    check(
        isinstance(out["employment"], list) and all(x in EMPLOYMENT_KINDS for x in out["employment"]),
        f"employment must list values from {EMPLOYMENT_KINDS}",
    )
    check(locations["remote"] in ("any", "only", "no"), "locations.remote must be any, only or no")
    check(_strings(locations["cities"]), "locations.cities must be a list of text")
    if not _strings(locations["regions"]):
        problems.append("locations.regions must be a list of country codes")
    elif places is not None:
        unknown = [code for code in locations["regions"] if not places.known_region(code)]
        check(not unknown, f"locations.regions has codes this version cannot read: {unknown}")
    eligibility = out["eligibility"]
    check(
        eligibility["needs_sponsorship"] in (None, True, False),
        "eligibility.needs_sponsorship must be true, false or null",
    )
    check(
        isinstance(eligibility["rule_packs"], list) and all(x in RULE_PACKS for x in eligibility["rule_packs"]),
        f"eligibility.rule_packs must list values from {RULE_PACKS}",
    )
    check(
        _strings(out["companies"]["prefer"]) and _strings(out["companies"]["exclude"]),
        "companies.prefer and companies.exclude must be lists of names",
    )
    thresholds = out["thresholds"]
    if all(_int(thresholds[k], 0, 100) for k in ("strong", "good", "publish_min")):
        check(
            thresholds["strong"] >= thresholds["good"] >= thresholds["publish_min"],
            "thresholds must satisfy strong >= good >= publish_min",
        )
    else:
        problems.append("thresholds must be whole numbers from 0 to 100")
    check(
        thresholds["direct_min"] is None or _int(thresholds["direct_min"], 0, 100),
        "thresholds.direct_min must be a whole number from 0 to 100, or null",
    )
    check(
        thresholds["max_required_years"] is None or _int(thresholds["max_required_years"], 0),
        "thresholds.max_required_years must be a whole number of years, or null",
    )
    check(
        thresholds["max_seniority"] is None or thresholds["max_seniority"] in SENIORITY,
        f"thresholds.max_seniority must be one of {SENIORITY}, or null",
    )
    unmet = thresholds["unmet_requirements"]
    check(
        isinstance(unmet, list) and all(isinstance(kind, str) and kind in UNMET_KINDS for kind in unmet),
        f"thresholds.unmet_requirements must list values from {UNMET_KINDS}",
    )
    check(
        all(_int(out["caps"][k], 1) for k in ("max_triage", "max_assess", "max_attempts")),
        "caps must be positive whole numbers",
    )
    budget = out["caps"]["max_usd_per_month"]
    check(
        budget is None or (isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 0),
        "caps.max_usd_per_month must be a positive number of US dollars, or null for no budget",
    )
    days = out["backfill"]["days"]
    check(days is None or _int(days, 0), "backfill.days must be a number of days or null")
    if problems:
        raise CriteriaError("; ".join(problems))
    return out


def load(path: Path, places: Places | None = None) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CriteriaError(f"{path} is missing") from None
    except ValueError as error:
        raise CriteriaError(f"{path} is not valid JSON: {error}") from None
    return normalize(data, places)


def profile_hashes(criteria: Mapping[str, Any], profile_text: str) -> dict[str, str]:
    """What each judging stage depends on. A changed hash re-judges that stage and nothing else.

    Triage sees only the targets and seniority. Assessment also sees the profile text and the
    eligibility and location preferences. Thresholds, caps and preferred companies affect ordering
    and tiers, never a verdict, so they are in neither hash.
    """
    triage = digest({"targets": criteria["targets"], "seniority": criteria["seniority"]})[:12]
    assess = digest(
        {
            "triage": triage,
            "eligibility": criteria["eligibility"],
            "locations": criteria["locations"],
            "employment": criteria["employment"],
            "profile": " ".join(profile_text.split()),
        }
    )[:12]
    return {"triage": triage, "assess": assess}


_EMPLOYMENT = [
    ("internship", r"\bintern(?:ship)?\b|\bco-?op\b|apprentice|working student|werkstudent|trainee"),
    ("contract", r"contract|freelance|consultant"),
    ("temporary", r"temporary|\btemp\b|seasonal|fixed[\s_-]*term"),
    ("part_time", r"part[\s_-]*time"),
    ("full_time", r"full[\s_-]*time|permanent|regular"),
]


def employment_kinds(label: str | None) -> set[str]:
    """Every kind a source's employment label could mean; empty when it says nothing readable."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label or "")  # "FullTime" -> "Full Time"
    return {kind for kind, pattern in _EMPLOYMENT if re.search(pattern, text, re.I)}


_PLACE_SEPARATOR = re.compile(r"\s*(?:/|;|\||•|·|\n)\s*")
# "or" as a word, or Oregon's code: not inside a word or a name such as Côte d'Or.
_OR = re.compile(r"(?<![^\s,(\[–—-])or(?![^\s,)\]:–—-])", re.I)


def _split_or(text: str) -> list[str]:
    """`text` split at the word "or" between places ("Remote or New York", "Remote OR Hybrid"), but not
    at Oregon's code OR, in capitals and standing as a part of its own: after a comma, a dash, a bracket
    or nothing, and before one, nothing, or the word "or" ("Portland, OR", "Eugene, OR, USA", "US - OR -
    Portland", and the first in "Portland, OR or Seattle, WA")."""
    pieces, start = [], 0
    for match in _OR.finditer(text):
        before, after = text[: match.start()].rstrip(), text[match.end() :].lstrip()
        alone = (not before or before[-1] in ",([–—-") and (not after or after[0] in ",)]:–—-" or _OR.match(after))
        if alone and match.group() == "OR":
            continue
        pieces.append(text[start : match.start()])
        start = match.end()
    return [*pieces, text[start:]]


def _places(location: str | None) -> list[str]:
    """The places a location lists: split at a slash, semicolon, bar, bullet or line break, and at "or"."""
    return [p for part in _PLACE_SEPARATOR.split(location or "") for p in _split_or(part) if p.strip()]


_NOISE = re.compile(
    r"\b(?:remote|hybrid|on-?site|in-?office|flexible|anywhere|worldwide|global|based|only|office|hq"
    r"|(?-i:in|the|within|from))\b",
    re.I,
)
# U+2018 and U+2019 (curly quotes) and U+02BB and U+02BC (modifier letters, as Uzbek writes) read as "'".
_APOSTROPHES = str.maketrans(dict.fromkeys("‘’ʻʼ", "'"))


def _fold(text: str) -> str:
    """Text as places are compared: without accents or case, and with curly apostrophes straight. "Panamá"
    and "PANAMA" are both "panama", and "Côte d'Ivoire" is "cote d'ivoire" however its apostrophe is written."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_APOSTROPHES))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _clean(part: str) -> str:
    """One comma-separated part of a place without its noise words, such as Remote or a lowercase "the"."""
    return " ".join(_NOISE.sub(" ", part).split()).strip("- ")


def _keys(name: str) -> set[str]:
    """What a name in the table matches: folded as written, and as a place's part is read, without the
    noise words ("Saint Vincent and the Grenadines" is read as "saint vincent and grenadines")."""
    return {_fold(name), _fold(_clean(name))}


class Places:
    """Reads the countries a location string names, using the bundled seed table."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self.countries = set(data["countries"])
        self.names: dict[str, str] = {}
        by_region: dict[str, set[str]] = {}
        for code, entry in data["countries"].items():
            by_region.setdefault(entry["region"], set()).add(code)
            for name in entry["names"]:
                self.names.update(dict.fromkeys(_keys(name), code))
        ambiguous_names = {key for name in data["ambiguous_state_names"] for key in _keys(name)}
        # A name that is also a US state's, such as Georgia, is no country's on its own: like IL, only a
        # known city settles it.
        self.ambiguous_names = {key: self.names.pop(key) for key in ambiguous_names if key in self.names}
        self.region_words = {
            key: set().union(*(by_region.get(tag, set()) for tag in tags))
            for word, tags in data["region_words"].items()
            for key in _keys(word)
        }
        self.groups = {name: set(codes) for name, codes in data["groups"].items()}
        self.ambiguous = set(data["ambiguous_state_codes"])
        self.alone = set(data["codes_read_alone"])
        self.us_states = set(data["us_states"])
        self.state_names = {key for name in data["us_states"].values() for key in _keys(name)} - ambiguous_names
        self.ca_provinces = set(data["ca_provinces"])
        self.province_names = {key for name in data["ca_provinces"].values() for key in _keys(name)}
        self.cities = {key: code for code, cities in data["cities"].items() for city in cities for key in _keys(city)}

    @classmethod
    def bundled(cls) -> Places:
        path = Path(__file__).with_name("data") / "places.json"
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def known_region(self, code: str) -> bool:
        return code in self.countries or code in self.groups

    def expand(self, regions: Iterable[str]) -> set[str]:
        out: set[str] = set()
        for code in regions:
            out |= self.groups.get(code, {code})
        return out

    def _part(self, token: str, country_slot: bool) -> tuple[str, set[str]]:
        """What one part of a place says, with the countries that go with it:

        - ("name", {code}): a country's name, which a state or province after it makes a town's;
        - ("country", codes): a region word, or a country's code where codes are read;
        - ("state", codes): a US state or Canadian province, with the countries it could be in: {"US"}
          for Ohio, {"CA"} for ON, {"US", "IL"} for IL, which is also Israel's code;
        - ("", set()): nothing the table reads.
        """
        key = _fold(token)
        if key in self.ambiguous_names:
            return "state", {"US", self.ambiguous_names[key]}
        if key in self.names:
            return "name", {self.names[key]}
        if key in self.region_words:
            return "country", self.region_words[key]
        if len(token) == 2 and token.isalpha():
            code = token.upper()
            if code in self.ambiguous:
                return "state", {"US", code}
            if code in self.us_states:
                return "state", {"US"}
            if code in self.ca_provinces:
                return "state", {"CA"}
            # A code that career sites write alone ("Paris, FR") is read anywhere. Any other only where an
            # address puts its country, after a locality and a region ("Beirut, Beyrouth, LB"): elsewhere
            # a two-letter part is as often a region, as GD (Guangdong) is in "Guangzhou, GD, CN".
            if code in self.alone or (country_slot and code in self.countries):
                return "country", {code}
            return "", set()
        if key in self.state_names:
            return "state", {"US"}
        if key in self.province_names:
            return "state", {"CA"}
        return "", set()

    def _place(self, place: str) -> set[str] | None:
        """Countries one place certainly names, or None when it cannot be read with certainty."""
        text = re.sub(r"[()\[\]:]|\s+[-–—]\s+|[–—]", ",", place)
        tokens = [t for t in (_clean(raw) for raw in text.split(",")) if t and not t.isdigit()]
        if not tokens:
            return None
        last = len(tokens) - 1
        parts = [self._part(token, country_slot=last >= 2 and i == last) for i, token in enumerate(tokens)]
        countries: set[str] = set()
        ambiguous: set[str] = set()
        us = canada = False
        for i, (kind, codes) in enumerate(parts):
            if kind == "country":
                countries |= codes
            elif kind == "name":
                # A country named before a state or province it cannot be in is a town there: "Lebanon,
                # PA" and "Peru, Indiana" are not abroad, while "United States, CA" is still the US.
                # Known limit: a country's name alone is that country, so a listing that gives only the
                # town of a US town named like a country ("Lebanon") reads it as abroad, as a listing
                # of only "Dublin" reads Dublin, Ohio as Ireland (docs/spikes.md, E13).
                if all(later != "state" or codes <= homes for later, homes in parts[i + 1 :]):
                    countries |= codes
            elif kind == "state":
                if codes == {"US"}:
                    us = True
                elif codes == {"CA"}:
                    canada = True
                else:
                    ambiguous |= codes - {"US"}
        if countries:
            return countries
        if us != canada:
            return {"US"} if us else {"CA"}
        city = self.cities.get(_fold(tokens[0]))
        if ambiguous:
            # "Chicago, IL" and "Tel Aviv, IL", "Atlanta, Georgia" and "Tbilisi, Georgia": only a known
            # city settles which one this is.
            return {city} if city and (city == "US" or city in ambiguous) else None
        return {city} if city and not (us and canada) else None

    def resolve(self, location: str | None) -> tuple[set[str], bool]:
        """(countries named, whether every listed place could be read)."""
        places = _places(location)
        found: set[str] = set()
        complete = bool(places)
        for place in places:
            codes = self._place(place)
            if codes is None:
                complete = False
            else:
                found |= codes
        return found, complete


_TITLE_INTERNSHIP = re.compile(r"\bintern(?:ship)?\b|\bco-?op\b|\bworking student\b|\bwerkstudent", re.I)


class Stage0:
    """The hard constraints. `check` returns why a posting is dropped, or None when it passes."""

    def __init__(self, criteria: Mapping[str, Any], places: Places) -> None:
        self.excluded = {company_key(name) for name in criteria["companies"]["exclude"]}
        self.employment = set(criteria["employment"])
        self.allowed = places.expand(criteria["locations"]["regions"])
        self.places = places

    def check(self, job: Job) -> str | None:
        if company_key(job.company) in self.excluded:
            return "company"
        if self.employment:
            kinds = employment_kinds(job.employment_type)
            if _TITLE_INTERNSHIP.search(job.title):  # a title that says so is evidence too
                kinds = kinds | {"internship"}
            if kinds and not kinds & self.employment:
                return "employment"
        if self.outside(job.location):
            return "location"
        return None

    def excludes(self, countries: Iterable[str]) -> bool:
        """Whether there are countries here and the wanted regions leave out every one. With no regions
        set, none are left out."""
        found = set(countries)
        return bool(self.allowed) and bool(found) and not found & self.allowed

    def outside(self, location: str | None) -> list[str]:
        """The countries a place names, when it certainly puts a posting outside the wanted regions.

        Empty when it does not: inside them, no regions set, or any of its places unreadable.
        """
        if not self.allowed:
            return []
        countries, complete = self.places.resolve(location)
        return sorted(countries) if complete and self.excludes(countries) else []
