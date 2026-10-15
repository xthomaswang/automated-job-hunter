import pytest
from ajh import criteria as C
from ajh.model import Job

PLACES = C.Places.bundled()
MINIMAL = {"schema_version": 1, "targets": {"roles": ["UX Researcher"]}}


def job(**changes):
    facts = dict(
        key="acme:1",
        source="acme",
        source_id="1",
        company="Acme",
        title="UX Researcher",
        location="Remote, US",
        url="https://jobs.example.com/1",
        description="Run studies.",
    )
    facts.update(changes)
    return Job(**facts)


def test_a_minimal_file_gets_every_default():
    out = C.normalize(MINIMAL, PLACES)
    assert out["targets"] == {"roles": ["UX Researcher"], "adjacent": [], "avoid": []}
    assert out["caps"] == {"max_triage": 1500, "max_assess": 100, "max_attempts": 3, "max_usd_per_month": None}
    assert out["backfill"] == {"days": None} and out["employment"] == [] and out["locations"]["regions"] == []
    assert C.normalize(dict(MINIMAL, backfill={"days": 45}), PLACES)["backfill"]["days"] == 45


def test_every_problem_is_reported_at_once():
    bad = {
        "schema_version": 2,
        "targets": {"roles": [], "adjcent": ["x"]},
        "employment": ["full-time"],
        "locations": {"regions": ["US", "Atlantis"], "remote": "sometimes"},
        "thresholds": {"strong": 50, "good": 60},
        "caps": {"max_assess": 0},
        "colour": "blue",
    }
    with pytest.raises(C.CriteriaError) as failure:
        C.normalize(bad, PLACES)
    message = str(failure.value)
    for expected in (
        "schema_version",
        "targets.adjcent",
        "at least one role",
        "employment must list",
        "Atlantis",
        "locations.remote",
        "strong >= good",
        "caps must be positive",
        "'colour'",
    ):
        assert expected in message


@pytest.mark.parametrize(
    "label,expected",
    [
        ("FullTime", {"full_time"}),
        ("Full-time", {"full_time"}),
        ("fulltime_permanent", {"full_time"}),
        ("fulltime_fixed_term", {"full_time", "temporary"}),
        ("Part-time", {"part_time"}),
        ("Intern", {"internship"}),
        ("Internship", {"internship"}),
        ("Contractor", {"contract"}),
        ("Working Student", {"internship"}),
        ("unknown", set()),
        ("", set()),
        (None, set()),
        ("International", set()),
    ],
)
def test_employment_labels_map_to_every_kind_they_could_mean(label, expected):
    assert C.employment_kinds(label) == expected


@pytest.mark.parametrize(
    "location,countries,complete",
    [
        ("Remote, US", {"US"}, True),
        ("United States", {"US"}, True),
        ("US, WA, Bellevue", {"US"}, True),
        ("New York, NY", {"US"}, True),
        ("San Francisco, CA", {"US"}, True),
        ("San Francisco, CA • New York, NY • United States", {"US"}, True),
        ("San Francisco Bay Area", {"US"}, True),
        ("Chicago, IL", {"US"}, True),
        ("Tel Aviv, IL", {"IL"}, True),
        ("Bengaluru, IN", {"IN"}, True),
        ("Toronto, CA", {"CA"}, True),
        ("Vancouver, WA", {"US"}, True),
        ("London, ON", {"CA"}, True),
        ("London", {"GB"}, True),
        ("Hybrid - London, UK", {"GB"}, True),
        ("Birmingham, United Kingdom / Belfast / Homeworker - UK", {"GB"}, True),  # a Workday record's places
        ("Cardiff", {"GB"}, True),
        ("Singapore, 01, sg", {"SG"}, True),
        ("Guangzhou, GD, CN", {"CN"}, True),
        ("Remote (Germany)", {"DE"}, True),
        ("US-Remote", {"US"}, True),
        ("New York / Berlin", {"US", "DE"}, True),
        ("Amsterdam, Noord-Holland, Netherlands", {"NL"}, True),
        ("Germany; Remote", {"DE"}, False),
        ("Remote", set(), False),
        ("Worldwide", set(), False),
        ("Anywhere in the world", set(), False),
        ("Cambridge, MA", set(), False),
        ("Athens, GA", set(), False),
        ("Georgia", set(), False),
        ("Springfield", set(), False),
        ("unknown", set(), False),
        ("", set(), False),
        (None, set(), False),
    ],
)
def test_places_reads_a_country_only_when_it_is_certain(location, countries, complete):
    assert PLACES.resolve(location) == (countries, complete)


def test_region_words_and_groups_expand_to_countries():
    europe, complete = PLACES.resolve("Remote - EMEA")
    assert complete and {"DE", "GB", "IL", "ZA"} <= europe and "US" not in europe
    assert PLACES.expand(["EU", "US"]) >= {"FR", "DE", "US"} and "GB" not in PLACES.expand(["EU"])
    assert PLACES.resolve("Americas")[0] >= {"US", "CA", "BR"}


def stage0(**criteria):
    return C.Stage0(C.normalize(dict(MINIMAL, **criteria), PLACES), PLACES)


def test_stage0_drops_only_on_positive_evidence():
    check = stage0(companies={"exclude": ["Acme, Inc."]}, employment=["full_time"], locations={"regions": ["US"]}).check
    assert check(job()) == "company"
    assert check(job(company="Globex")) is None
    assert check(job(company="Globex", employment_type="Internship")) == "employment"
    assert check(job(company="Globex", employment_type="unknown")) is None  # unknown passes
    assert check(job(company="Globex", location="Berlin, Germany")) == "location"
    assert check(job(company="Globex", location="Berlin / New York")) is None  # one allowed place is enough
    assert check(job(company="Globex", location="Remote")) is None  # unreadable passes
    assert check(job(company="Globex", location="Germany; Remote")) is None  # partly unreadable passes
    assert check(job(company="Globex", location="Remote - Europe")) == "location"


def test_no_constraints_means_nothing_is_dropped():
    check = stage0().check
    assert check(job(location="Berlin, Germany", employment_type="Internship")) is None


def test_profile_hashes_change_only_with_what_each_stage_reads():
    base = C.normalize(MINIMAL, PLACES)
    hashes = C.profile_hashes(base, "Five years of mixed-methods research.")
    same = dict(
        base,
        thresholds={"strong": 90, "good": 70, "publish_min": 10},
        caps={"max_triage": 5, "max_assess": 5, "max_attempts": 1},
        companies={"prefer": ["Acme"], "exclude": ["Globex"]},
        backfill={"days": None},
    )
    assert C.profile_hashes(same, "Five years  of\nmixed-methods research.") == hashes
    retargeted = C.profile_hashes(
        dict(base, targets={"roles": ["Product Analyst"], "adjacent": [], "avoid": []}),
        "Five years of mixed-methods research.",
    )
    assert retargeted["triage"] != hashes["triage"] and retargeted["assess"] != hashes["assess"]
    rewritten = C.profile_hashes(base, "Ten years of quantitative research.")
    assert rewritten["triage"] == hashes["triage"] and rewritten["assess"] != hashes["assess"]


def test_a_title_that_names_an_internship_counts_as_evidence():
    check = stage0(employment=["full_time"]).check
    assert check(job(title="PhD Intern, Data Science (2027)")) == "employment"
    assert check(job(title="Data Science Internship")) == "employment"
    assert (
        check(job(title="Internal Tools Researcher")) is None
        and check(job(title="International UX Researcher")) is None
    )
    assert stage0(employment=["full_time", "internship"]).check(job(title="Research Intern")) is None
    assert stage0().check(job(title="Research Intern")) is None  # no employment constraint, nothing to drop


def test_a_monthly_budget_is_optional_and_judges_nothing_again():
    for good in (None, 50, 12.5):
        assert C.normalize(dict(MINIMAL, caps={"max_usd_per_month": good}), PLACES)["caps"]["max_usd_per_month"] == good
    for bad in (0, -5, "50", True):
        with pytest.raises(C.CriteriaError, match="max_usd_per_month"):
            C.normalize(dict(MINIMAL, caps={"max_usd_per_month": bad}), PLACES)
    plain = C.normalize(MINIMAL, PLACES)
    budgeted = C.normalize(dict(MINIMAL, caps={"max_usd_per_month": 50}), PLACES)
    assert C.profile_hashes(plain, "Five years of research.") == C.profile_hashes(budgeted, "Five years of research.")


def test_listing_limits_are_validated_and_judge_nothing_again():
    out = C.normalize(MINIMAL, PLACES)["thresholds"]
    assert (out["direct_min"], out["max_required_years"], out["max_seniority"], out["unmet_requirements"]) == (
        None,
        None,
        None,
        [],
    )
    good = {"direct_min": 30, "max_required_years": 3, "max_seniority": "mid", "unmet_requirements": ["degree"]}
    assert C.normalize(dict(MINIMAL, thresholds=good), PLACES)["thresholds"]["max_seniority"] == "mid"
    for bad, problem in (
        ({"direct_min": 101}, "direct_min"),
        ({"max_required_years": -1}, "max_required_years"),
        ({"max_required_years": 2.5}, "max_required_years"),
        ({"max_seniority": "lead"}, "max_seniority"),
        ({"unmet_requirements": ["degree", "visa"]}, "unmet_requirements"),
        ({"unmet_requirements": "degree"}, "unmet_requirements"),
    ):
        with pytest.raises(C.CriteriaError, match=problem):
            C.normalize(dict(MINIMAL, thresholds=bad), PLACES)
    plain = C.normalize(MINIMAL, PLACES)
    limited = C.normalize(dict(MINIMAL, thresholds=good), PLACES)
    assert C.profile_hashes(plain, "Five years of research.") == C.profile_hashes(limited, "Five years of research.")
