from datetime import date

import pytest
from ajh import rules as R


@pytest.mark.parametrize(
    "years,expected",
    [
        (None, "unknown"),
        (0, "junior"),
        (2, "junior"),
        (3, "junior"),
        (3.5, "mid"),
        (4, "mid"),
        (4.99, "mid"),
        (5, "senior"),
        (7, "senior"),
        (7.99, "senior"),
        (8, "staff"),
        (12, "staff"),
    ],
)
def test_mutually_exclusive_mandatory_experience_bins(years, expected):
    assert R.experience_level(years) == expected


def test_custom_level_boundaries_are_respected():
    assert R.experience_level(4, {"junior_max": 5, "senior_min": 8, "staff_min": 12}) == "junior"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Staff UX Researcher", "staff"),
        ("Principal Designer", "staff"),
        ("Sr. Product Analyst", "senior"),
        ("Mid-level Researcher", "mid"),
        ("Research Intern", "junior"),
        ("Associate Director of Research", "lead"),
        ("Associate Researcher", "associate"),
        ("UX Researcher", "unknown"),
    ],
)
def test_title_seniority_reads_the_title_label(title, expected):
    assert R.title_seniority(title) == expected


def test_title_and_years_conflict_is_labeled_without_rewriting_either():
    out = R.experience_metadata("Senior UX Researcher", 2)
    assert out["experience_level"] == "junior" and out["title_seniority"] == "senior"
    assert out["seniority_conflict"] is True and "Both source facts" in out["seniority_note"]
    quiet = R.experience_metadata("Staff UX Researcher", None)
    assert (
        quiet["experience_level"] == "unknown"
        and quiet["seniority_conflict"] is False
        and quiet["seniority_note"] is None
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0 years of experience required", True),
        ("0-2 years of experience", True),
        ("No prior experience required", True),
        ("No experience is required", True),
        ("Current students welcome.", False),
        ("10 years of experience", False),
    ],
)
def test_zero_experience_needs_an_explicit_statement(text, expected):
    assert R.explicit_zero_experience(text) is expected


def test_preference_only_quotes_cannot_support_mandatory_years():
    assert R.preference_only("8 years of experience is preferred.")
    assert R.preference_only("Ideally 5+ years in research")
    assert not R.preference_only("Requires 3 years; a PhD is preferred.")
    assert not R.preference_only("3 years of research experience")
    assert R.numbers_in("Requires 3 years, or 1.5 with a PhD") == [3.0, 1.5]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("We do not offer visa sponsorship for this role.", (True, False)),
        ("Please note that visa sponsorship is not available for this position.", (True, False)),
        ("We are unable to sponsor visas at this time.", (True, False)),
        ("Candidates must be authorized to work in the US without sponsorship.", (True, False)),
        (
            "We do sponsor visas! However, we aren't able to successfully sponsor visas for every role and every candidate.",
            (False, False),
        ),
        ("This role does not require sponsorship now or in the future.", (False, False)),
        ("Visa sponsorship is available for the right candidate.", (False, False)),
        ("You must be a U.S. citizen to apply.", (False, True)),
        ("This position requires U.S. citizenship and an active TS/SCI clearance.", (False, True)),
        ("US Citizenship required. 8+ years of experience.", (False, True)),
        ("Must be a U.S. citizen or permanent resident.", (False, False)),
        (
            "Open to U.S. citizens, permanent residents and visa holders. Tell us about your citizenship goals.",
            (False, False),
        ),
        ("Help us citizenship workshops run smoothly.", (False, False)),
    ],
)
def test_posting_flags_read_the_text_and_prefer_missing_to_wrong(text, expected):
    assert R.posting_flags(text) == expected
    assert R.posting_flags(text.replace(" ", "\n")) == expected  # line wrapping does not matter


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2020-01-02", date(2020, 1, 2)),
        ("2020-01-02T03:04:05Z", date(2020, 1, 2)),
        ("2020-01-02T03:04:05.123+00:00", date(2020, 1, 2)),
        ("2020-01-02T03:04:05.1234567Z", date(2020, 1, 2)),
        ("2026-09-24T17:26:49-04:00", date(2026, 9, 24)),
        ("January  2, 2020", date(2020, 1, 2)),
        ("Thu, 02 Jan 2020 03:04:05 +0000", date(2020, 1, 2)),
        ("2020-01-02 10:58:56 UTC", date(2020, 1, 2)),
        (1577934245, date(2020, 1, 2)),
        (1577934245000, date(2020, 1, 2)),
        ("1577934245000", date(2020, 1, 2)),
    ],
)
def test_parse_date_reads_every_format_the_sources_use(value, expected):
    assert R.parse_date(value) == expected


@pytest.mark.parametrize("value", [None, "", "   ", "not a date", "soon", True, "2020-13-45"])
def test_parse_date_never_invents_a_date(value):
    assert R.parse_date(value) is None


def test_age_days_is_whole_days_and_unknown_stays_unknown():
    today = date(2020, 1, 10)
    assert R.age_days("2020-01-02", today) == 8
    assert R.age_days("2020-02-01", today) == 0  # a future date is not negative age
    assert R.age_days(None, today) is None
