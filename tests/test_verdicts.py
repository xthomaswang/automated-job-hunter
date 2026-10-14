import pytest
from ajh.model import Job
from ajh.rubric import FEEDBACK
from ajh.verdicts import check_assessment, clean_text, read_assess, read_triage

TRIAGE = {"stage": "triage", "nonce": "9f2c", "items": {"T0001": "acme:1", "T0002": "acme:2", "T0003": "acme:3"}}
ASSESS = {"stage": "assess", "nonce": "77ab", "items": {"A0001": "acme:1", "A0002": "acme:2"}}

POSTING = (
    "About the role\nPlan and run interviews, surveys and usability studies with customers.\n"
    "Requirements\n- 3+ years of experience conducting “mixed-methods” research\n"
    "- Experience with SQL is preferred; ideally 5 years in B2B\n"
    "- We do not offer visa sponsorship for this role."
)


def job(description=POSTING, title="UX Researcher"):
    return Job(
        key="acme:1",
        source="acme",
        source_id="1",
        company="Acme",
        title=title,
        location="Remote, US",
        url="https://jobs.example.com/1",
        description=description,
    )


def fields(**changes):
    base = {
        "fit": ["78"],
        "role_match": ["direct"],
        "seniority": ["mid"],
        "required_years": ["3"],
        "required_years_quote": ['3+ years of experience conducting "mixed-methods" research'],
        "preferred_years": ["none"],
        "why": ["Core research role that matches your interview and survey work."],
        "match": ["Mixed-methods research || Plan and run interviews, surveys and usability studies"],
        "gap": ["SQL || Experience with SQL is preferred"],
        "note": ["Sponsorship || We do not offer visa sponsorship for this role."],
    }
    base.update({name: value if isinstance(value, list) else [value] for name, value in changes.items()})
    return base


def test_clean_text_removes_links_and_token_shapes_and_bounds_length():
    fake_key = (
        "sk-ant-" + "abcdefghijklmnopqrstuvwxyz0123456789"
    )  # assembled here so no scanner mistakes it for a real one
    dirty = f"Great fit.  See https://evil.example/x?token=1 and www.example.com now {fake_key}"
    assert clean_text(dirty, 240) == "Great fit. See [link removed] and [link removed] now [removed]"
    long = clean_text("word " * 100, 40)
    assert len(long) <= 41 and long.endswith("…") and not long.endswith(" …")


def test_triage_result_is_read_leniently_on_layout_and_strictly_on_content():
    text = "```\nPacket T-001  nonce 9F2C\n- T0001: keep\nt0002 DROP_TITLE.\nT0003 drop\nend 3\n```\n"
    assert read_triage(text, "T-001", TRIAGE) == ({"T0001": "keep", "T0002": "drop_title", "T0003": "drop"}, {})


@pytest.mark.parametrize(
    "text",
    ["", "T0001 keep\nT0002 keep\n", "packet T-001 nonce 0000\nT0001 keep\n", "packet T-002 nonce 9f2c\nT0001 keep\n"],
)
def test_a_result_without_the_right_header_answers_nothing(text):
    assert read_triage(text, "T-001", TRIAGE) == ({}, {"T0001": "header", "T0002": "header", "T0003": "header"})


def test_triage_bad_and_conflicting_answers_are_invalid_and_missing_ones_are_just_missing():
    text = "packet T-001 nonce 9f2c\nT0001 maybe\nT0002 keep\nT0002 drop\nT0999 keep\nnot a line\nend 3\n"
    verdicts, invalid = read_triage(text, "T-001", TRIAGE)
    assert verdicts == {} and invalid == {"T0001": "verdict", "T0002": "conflict"}  # T0003 is in neither: ask again
    assert read_triage("packet T-001 nonce 9f2c\nT0002 keep\nT0002 keep\n", "T-001", TRIAGE) == ({"T0002": "keep"}, {})


def test_an_assessment_is_published_in_the_postings_own_words():
    assessment, codes = check_assessment(fields(), job())
    assert codes == [] and assessment["fit"] == 78 and assessment["role_match"] == "direct"
    assert assessment["required_years"] == 3.0 and assessment["preferred_years"] is None
    assert assessment["matches"] == [
        {
            "label": "Mixed-methods research",
            "quote": "Plan and run interviews, surveys and usability studies with customers.",
        }
    ]
    assert assessment["gaps"] == [{"label": "SQL", "quote": "Experience with SQL is preferred"}]
    assert assessment["notes"][0]["quote"] == "We do not offer visa sponsorship for this role."
    assert assessment["unverified"] == 0 and assessment["seniority"] == "mid"
    reworded = fields(match="Research || plan and run   interviews, surveys and\nusability studies")
    assert (
        check_assessment(reworded, job())[0]["matches"][0]["quote"]
        == "Plan and run interviews, surveys and usability studies with customers."
    )


def test_a_quote_that_is_not_in_the_posting_is_discarded_not_published():
    assessment, codes = check_assessment(
        fields(
            match=["Real || Plan and run interviews", "Invented || 10 years leading research teams"],
            gap=["No separator at all", " || Experience with SQL is preferred"],
        ),
        job(),
    )
    assert [m["label"] for m in assessment["matches"]] == ["Real"] and assessment["gaps"] == []
    assert assessment["unverified"] == 3 and codes == ["quote"]


def test_an_assessment_with_no_valid_match_is_rejected_unless_the_work_is_unrelated():
    assert check_assessment(fields(match="Invented || we love research"), job()) == (None, ["quote", "no_match"])
    unrelated, codes = check_assessment(
        fields(role_match="unrelated", fit="5", match=[], gap=[], note=[], required_years="none"), job()
    )
    assert unrelated["matches"] == [] and unrelated["fit"] == 5 and codes == []


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"fit": "101"}, "fit"),
        ({"fit": "high"}, "fit"),
        ({"fit": []}, "fit"),
        ({"role_match": "perfect"}, "role_match"),
        ({"why": "   "}, "why"),
        ({"why": []}, "why"),
    ],
)
def test_missing_essentials_reject_the_assessment(changes, code):
    assessment, codes = check_assessment(fields(**changes), job())
    assert assessment is None and code in codes


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"required_years": "5"}, "years_number"),  # the quote says 3
        ({"required_years_quote": "at least three years"}, "years_quote"),  # not in the posting
        ({"required_years_quote": []}, "years_quote"),
        ({"required_years": "5", "required_years_quote": "ideally 5 years in B2B"}, "years_preference"),
        ({"required_years": "0", "required_years_quote": "Plan and run interviews"}, "years_zero"),
    ],
)
def test_an_unsupported_years_value_becomes_unknown_never_a_guess(changes, code):
    assessment, codes = check_assessment(fields(**changes), job())
    assert assessment["required_years"] is None and codes == [code] and assessment["unverified"] == 1


def test_supported_zero_and_preferred_years_are_kept():
    posting = POSTING + "\n- No prior experience required for this internship."
    zero, codes = check_assessment(
        fields(required_years="0", required_years_quote="No prior experience required"), job(posting)
    )
    assert zero["required_years"] == 0.0 and codes == []
    preferred, codes = check_assessment(
        fields(preferred_years="5 years", preferred_years_quote="ideally 5 years in B2B"), job()
    )
    assert preferred["preferred_years"] == 5.0 and preferred["required_years"] == 3.0 and codes == []


STUDENT_POSTING = POSTING + (
    "\n- Must be currently enrolled in a PhD program in Psychology or a related field."
    "\n- A Master's degree in Statistics is preferred."
    "\n- Bachelor's degree or equivalent practical experience."
    "\n- Active state nursing license required."
)


def test_an_unmet_requirement_is_kept_only_when_mandatory_quoted_and_without_an_alternative():
    assert check_assessment(fields(), job())[0]["unmet"] == []  # none stated: checked, nothing found
    assessment, codes = check_assessment(
        fields(
            unmet=[
                "enrollment || Must be currently enrolled in a PhD program",
                "Licence || Active state nursing license required",  # any case, either spelling
                "degree || A Master's degree in Statistics is preferred",  # only preferred
                "degree || Bachelor's degree or equivalent practical experience",  # an alternative is offered
            ]
        ),
        job(STUDENT_POSTING),
    )
    assert assessment["unmet"] == [
        {
            "kind": "enrollment",
            "quote": "Must be currently enrolled in a PhD program in Psychology or a related field.",
        },
        {"kind": "license", "quote": "Active state nursing license required."},
    ]
    assert assessment["unverified"] == 2 and codes == ["unmet"] and "unmet" in FEEDBACK


def test_an_unmet_line_without_a_known_kind_or_a_real_quote_is_dropped_and_rejects_nothing():
    assessment, codes = check_assessment(
        fields(
            unmet=[
                "visa || We do not offer visa sponsorship for this role.",  # work authorization is a note
                "degree || A PhD from a top university",  # not in the posting
                "enrollment Must be currently enrolled in a PhD program",  # no separator
            ]
        ),
        job(STUDENT_POSTING),
    )
    assert assessment["unmet"] == [] and assessment["unverified"] == 3 and codes == ["unmet"]
    capped = check_assessment(
        fields(unmet=["license || Active state nursing license required"] * 6), job(STUDENT_POSTING)
    )
    assert len(capped[0]["unmet"]) == 4


def test_free_text_is_sanitized_and_extra_lines_are_ignored():
    assessment, _ = check_assessment(
        fields(
            why="Apply at https://evil.example/now " + "x" * 400,
            seniority="wizard",
            match=["m || Plan and run interviews"] * 9,
            fit="78/100",
        ),
        job(),
    )
    assert "evil" not in assessment["why"] and len(assessment["why"]) <= 241
    assert len(assessment["matches"]) == 4 and assessment["seniority"] == "unknown" and assessment["fit"] == 78


RESULT = """packet A-001 nonce 77ab
## A0001
fit: 78
role_match: direct
seniority: mid
required_years: 3
required_years_quote: 3+ years of experience conducting "mixed-methods" research
why: Core research role that matches your work.
match: Mixed-methods || Plan and run interviews, surveys and usability studies
- gap: SQL || Experience with SQL is preferred
## A0002
fit: 12
role_match: unrelated
why: A sales role.
## A0099
fit: 99
end 2
"""


def test_read_assess_splits_sections_and_checks_each_against_its_own_posting():
    jobs = {"A0001": job(), "A0002": job("Own a sales quota and close enterprise deals.", "Account Executive")}
    accepted, rejected = read_assess(RESULT, "A-001", ASSESS, jobs)
    assert rejected == {} and set(accepted) == {"A0001", "A0002"}
    assert accepted["A0001"]["gaps"][0]["label"] == "SQL" and accepted["A0002"]["role_match"] == "unrelated"
    crossed = {"A0001": jobs["A0002"], "A0002": jobs["A0001"]}  # quotes do not carry over to another posting
    assert read_assess(RESULT, "A-001", ASSESS, crossed)[1] == {"A0001": ["quote", "years_quote", "no_match"]}


def test_read_assess_rejects_wrong_headers_and_repeated_sections():
    jobs = {"A0001": job(), "A0002": job()}
    assert read_assess(RESULT.replace("77ab", "0000"), "A-001", ASSESS, jobs) == (
        {},
        {"A0001": ["header"], "A0002": ["header"]},
    )
    doubled = RESULT.replace("## A0002", "## A0001")
    accepted, rejected = read_assess(doubled, "A-001", ASSESS, jobs)
    assert accepted == {} and rejected == {"A0001": ["conflict"]}  # A0002 was not answered: asked again, not charged
