"""`apply`: the one step that changes tracked files, driven through whole ticks with scripted judges."""

import json
import subprocess
from datetime import timedelta

import pytest
from ajh import advance, apply, collect
from ajh.feed import readme_text
from ajh.http import Client
from ajh.layout import Layout
from helpers import (
    ACME,
    NOW,
    REMOTIVE,
    Boards,
    answer_assess,
    answer_triage,
    full_tick,
    good_assessment,
    posting,
    prose,
    rows,
    tracked,
)

TITLES = {1: "UX Researcher", 2: "Design Researcher", 3: "Account Executive", 4: "Product Analyst"}


def boards():
    return Boards(acme=[posting(n, title=title) for n, title in TITLES.items()])


def triage(job):
    return "drop_title" if job["title"] == "Account Executive" else "keep"


def assess(job):
    fit = {"UX Researcher": 88, "Design Researcher": 60, "Product Analyst": 30}[job["title"]]
    return good_assessment(job, fit=fit, role_match="adjacent" if job["title"] == "Product Analyst" else "direct")


def positions(root):
    return {r["key"]: r for r in map(json.loads, Layout(root).positions.read_text().splitlines())}


@pytest.fixture(autouse=True)
def no_canaries(monkeypatch):
    monkeypatch.setattr(advance, "is_canary", lambda key: False)


def test_a_whole_tick_publishes_the_matches_and_adopts_the_state(repo):
    root = repo()
    summary = full_tick(root, boards(), triage=triage, assess=assess)
    assert summary == {"published": 2, "below_threshold": 1, "blocked": 0, "rejected": 0, "unscored": 0, "unanswered": 0,
                       "open_matches": 2, "added": 2, "rescored": 0, "closed": 0, "removed": 0, "declined": 0}  # fmt: skip
    feed = positions(root)
    assert list(feed) == ["acme:1", "acme:2"]
    strong = feed["acme:1"]
    assert (strong["fit"], strong["tier"], strong["status"], strong["role_match"]) == (88, "strong", "scored", "direct")
    assert strong["matches"] == [{"label": "Core duties", "quote": " ".join(prose(1).split()[:8])}]
    assert (
        strong["first_seen"] == "2026-10-13"
        and strong["posted"] == "2026-10-10"
        and strong["brief"] == "jobs/acme/1.md"
    )
    assert (
        strong["judged"] == {"assess": 1, "rubric": "assess-v1", "unverified": 0} and feed["acme:2"]["tier"] == "good"
    )
    assert "last_seen" not in strong and not any(
        "T" in str(strong[k]) for k in ("first_seen", "posted")
    )  # days, not times

    brief = (root / "jobs" / "acme" / "1.md").read_text()
    assert brief.startswith("# UX Researcher\n") and "- Fit: 88 (strong)" in brief and f"> {prose(1)}" in brief
    assert "untrusted" in brief and not (root / "jobs" / "acme" / "4.md").exists()

    table = rows(root)
    assert {jid: (row.status, row.fit, row.ag) for jid, row in table.items()} == {
        "1": ("A", "88", 1), "2": ("A", "60", 1), "3": ("D", "-", 0), "4": ("R", "30", 1)}  # fmt: skip
    assert table["1"].dsh != "-" and table["3"].dsh == "-"
    layout = Layout(root)
    assert (layout.state / "titles.tsv").read_text() == "account executive\t1\n"
    meta = json.loads(layout.feed_meta.read_text())
    assert meta["as_of"] == "2026-10-13" and meta["counts"]["published"] == 2 and meta["counts"]["below_threshold"] == 1
    readme = layout.readme.read_text()
    assert (
        "**2 open matches** (1 strong, 1 good)" in readme
        and "| 88 | [UX Researcher](jobs/acme/1.md) | Acme |" in readme
    )
    assert "2 of these were first seen in the last 7 days" in readme and "Product Analyst" not in readme
    assert readme.count("(jobs/acme/1.md)") == 1 and "| 2026-10-10 | new |" in readme  # listed once, marked new
    later = readme_text(dict(meta, as_of="2026-10-25"), positions(root))
    assert "Nothing new in the last 7 days." in later and "| new |" not in later
    latest = layout.latest.read_text()
    assert "## New matches (2)" in latest and "`acme:1`" in latest
    log = [json.loads(line) for line in (layout.feed / "changes" / "2026-10.jsonl").read_text().splitlines()]
    assert [(e["event"], e["key"], e["at"]) for e in log] == [
        ("added", "acme:1", "2026-10-13"),
        ("added", "acme:2", "2026-10-13"),
    ]


def test_a_tick_with_nothing_new_leaves_every_tracked_file_byte_identical(repo):
    root, board = repo(), boards()
    full_tick(root, board, triage=triage, assess=assess)
    before = tracked(root)
    summary = full_tick(root, board, now=NOW + timedelta(hours=4), triage=triage, assess=assess)
    assert tracked(root) == before and summary["added"] == 0 and summary["open_matches"] == 2
    full_tick(root, board, now=NOW + timedelta(days=1), triage=triage, assess=assess)
    changed = {name for name, data in tracked(root).items() if before[name] != data}
    assert changed == {"README.md", "feed/meta.json", "state/meta.json", "state/sources.json"}  # only the day moved


def test_a_closed_posting_leaves_the_feed_with_a_tombstone_event(repo):
    root, board = repo(), boards()
    full_tick(root, board, triage=triage, assess=assess)
    del board.jobs["acme"][1]
    assert full_tick(root, board, triage=triage, assess=assess)["closed"] == 0  # one miss is forgiven
    summary = full_tick(root, board, triage=triage, assess=assess)
    layout = Layout(root)
    assert summary["closed"] == 1 and summary["open_matches"] == 1 and list(positions(root)) == ["acme:2"]
    assert not (root / "jobs" / "acme" / "1.md").exists() and (root / "jobs" / "acme" / "2.md").exists()
    log = [json.loads(line) for line in (layout.feed / "changes" / "2026-10.jsonl").read_text().splitlines()]
    assert [(e["event"], e["key"]) for e in log][-1] == ("closed", "acme:1") and log[-1]["fit"] == 88
    assert (
        "## Closed (1)" in layout.latest.read_text()
        and "UX Researcher" not in layout.readme.read_text().split("## Status")[0]
    )


def test_a_changed_profile_reassesses_published_postings_and_records_the_new_score(repo):
    root, board = repo(), boards()
    full_tick(root, board, triage=triage, assess=assess)
    (root / "profile" / "profile.md").write_text("Ten years of quantitative research.")
    summary = full_tick(root, board, triage=triage, assess=lambda job: good_assessment(job, fit=50))
    feed = positions(root)
    # every assessed posting is asked again; the two published ones move tier, the third now clears the bar
    assert summary["rescored"] == 2 and summary["added"] == 1 and summary["published"] == 3
    assert {key: (r["fit"], r["tier"], r["judged"]["assess"]) for key, r in feed.items()} == {
        "acme:1": (50, "stretch", 2), "acme:2": (50, "stretch", 2), "acme:4": (50, "stretch", 2)}  # fmt: skip


def test_an_unusable_assessment_is_retried_then_published_as_facts_only(repo):
    root = repo(criteria={"caps": {"max_triage": 100, "max_assess": 100, "max_attempts": 2}})
    board = Boards(acme=[posting(1)])

    def invented(job):
        return dict(good_assessment(job), match="Made up || ten years leading large research organizations")

    first = full_tick(root, board, assess=invented)
    assert first["rejected"] == 1 and first["open_matches"] == 0 and not Layout(root).positions.read_text()
    assert (rows(root)["1"].status, rows(root)["1"].attempts, rows(root)["1"].note) == ("K", 1, "fb:quote,no_match")
    summary = collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    advance.run(root, summary["seal"])
    packet = (Layout(root).packets / "A-001.md").read_text()
    assert "An earlier answer for this posting was rejected" in packet and "quotes must be copied exactly" in packet
    second = full_tick(root, board, assess=invented)
    record = positions(root)["acme:1"]
    assert second["unscored"] == 1 and second["added"] == 1 and rows(root)["1"].status == "U"
    assert (record["status"], record["tier"], record["fit"], record["why"], record["matches"]) == (
        "unscored",
        "unscored",
        None,
        None,
        [],
    )
    assert "could not be assessed automatically" in (root / "jobs" / "acme" / "1.md").read_text()
    assert "## Could not be assessed (1)" in Layout(root).readme.read_text()


def test_unrelated_work_is_never_published_whatever_the_score(repo):
    root = repo()
    summary = full_tick(
        root, Boards(acme=[posting(1)]), assess=lambda job: good_assessment(job, fit=95, role_match="unrelated")
    )
    assert summary["below_threshold"] == 1 and positions(root) == {} and rows(root)["1"].status == "R"


def test_a_cut_off_judging_stage_still_lands_what_was_judged(repo):
    root, board = repo(), boards()
    summary = collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    answer_triage(root, triage)
    moved = advance.run(root, summary["seal"])
    result = apply.run(root, moved["seal"])  # the assessment stage never ran
    assert result["unanswered"] == 3 and result["open_matches"] == 0
    assert {jid: row.status for jid, row in rows(root).items()} == {"1": "K", "2": "K", "3": "D", "4": "K"}
    done = full_tick(
        root, board, triage=triage, assess=lambda job: good_assessment(job) if job["title"] == "UX Researcher" else None
    )
    assert done["published"] == 1 and done["unanswered"] == 2 and rows(root)["2"].status == "K"


def test_judging_that_got_no_answers_is_reported_in_the_feed_until_answers_come_back(repo):
    from ajh import usage

    root, board = repo(), boards()
    layout = Layout(root)
    client = Client(board.transport, sleep=lambda s: None, retries=0)
    first = collect.run(root, client, NOW)
    day = json.loads(layout.plan.read_text())["as_of"]
    layout.results.mkdir(parents=True, exist_ok=True)  # what `hunter judge` leaves when no packet got an answer
    leaked = "sk-ant-api03-" + "x" * 40
    (layout.results / "_judging-triage.json").write_text(
        json.dumps({"failures": [f"API Error: 401 invalid x-api-key {leaked} see https://example.com/help"]})
    )
    apply.run(root, advance.run(root, first["seal"])["seal"])
    meta = json.loads(layout.feed_meta.read_text())
    assert meta["judging_failed"] == {
        "since": day,
        "stages": ["triage"],
        "detail": "API Error: 401 invalid x-api-key [removed] see [link removed]",
    }
    readme = layout.readme.read_text()
    assert f"> **Judging has failed since {day}**" in readme and "Last error: API Error: 401" in readme
    assert leaked not in readme
    (logged,) = usage.load(layout.usage)  # a failed update is logged too: no cost, and why
    assert (logged["total"]["cost_usd"], logged["stages"]["triage"]["postings"]) == (0, 4)
    assert logged["error"] == meta["judging_failed"]["detail"]

    again = collect.run(root, client, NOW + timedelta(days=1))  # fails again, this time without a report
    apply.run(root, advance.run(root, again["seal"])["seal"])
    meta = json.loads(layout.feed_meta.read_text())
    assert meta["judging_failed"]["since"] == day  # still the day it started
    assert meta["judging_failed"]["detail"] == "No answers came back; the judging step's log says why."

    assert usage.load(layout.usage)[-1]["error"] == "No answers came back; the judging step's log says why."

    full_tick(root, board, now=NOW + timedelta(days=2), triage=triage, assess=assess)
    assert "judging_failed" not in json.loads(layout.feed_meta.read_text())
    assert "Judging has failed" not in layout.readme.read_text()


def test_an_update_that_judged_adds_one_line_to_the_usage_log_and_a_quiet_one_adds_none(repo, monkeypatch, capsys):
    from ajh import __version__, usage
    from ajh.cli import main

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(usage, "now", lambda: "2026-10-13T12:30:00Z")
    root, board = repo(), boards()
    layout = Layout(root)

    def report(stage, cost, out):  # what `hunter judge` leaves for each stage
        model = {"in": 10, "out": out, "thinking": 0, "cache_read": 1000, "cache_write": 200, "cost_usd": cost}
        spent = {"models": {"claude-haiku-5-5": model}, "seconds": 12.0, "api_seconds": 40.0, "turns": 4}
        layout.results.mkdir(parents=True, exist_ok=True)
        (layout.results / f"_judging-{stage}.json").write_text(
            json.dumps({"packets": 1, "results": 1, "cost_usd": cost, "credential": "api_key", "usage": spent})
        )

    first = collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    answer_triage(root, triage)
    report("triage", 0.004, 300)
    moved = advance.run(root, first["seal"])
    answer_assess(root, assess)
    report("assess", 0.05, 900)
    apply.run(root, moved["seal"])
    (line,) = usage.load(layout.usage)
    assert {k: line[k] for k in ("v", "at", "where", "credential", "engine")} == {
        "v": 1,
        "at": "2026-10-13T12:30:00Z",
        "where": "local",
        "credential": "api_key",
        "engine": __version__,
    }
    assert line["stages"]["triage"] == {
        "postings": 4,
        "packets": 1,
        "results": 1,
        "missing": 0,
        "cost_usd": 0.004,
        "models": {"claude-haiku-5-5": {"in": 10, "out": 300, "thinking": 0, "cache_read": 1000, "cache_write": 200,
                                        "cost_usd": 0.004}},
        "seconds": 12.0,
        "api_seconds": 40.0,
        "turns": 4,
    }  # fmt: skip
    assert line["stages"]["assess"]["postings"] == 3  # the unrelated title was dropped at triage
    assert line["total"] == {
        "in": 20,
        "out": 1200,
        "thinking": 0,
        "cache_read": 2000,
        "cache_write": 400,
        "cost_usd": 0.054,
    }
    assert (line["per_posting_triaged_usd"], line["per_posting_assessed_usd"], line["per_new_match_usd"]) == (
        0.001,
        0.016667,
        0.027,
    )
    meta = json.loads(layout.feed_meta.read_text())
    assert (meta["usage"]["month"]["name"], meta["usage"]["all_time"]["cost_usd"]) == ("2026-10", 0.054)
    readme = layout.readme.read_text()
    assert "## Usage" in readme and "| All time | 1 | 20 | 1,200 | 2,000 | 400 | $0.054 |" in readme
    assert main(["--repo", str(root), "status"]) == 0
    assert json.loads(capsys.readouterr().out)["usage"]["all_time"]["updates"] == 1

    before = tracked(root)
    full_tick(root, board, triage=triage, assess=assess)  # nothing new to judge: no line, no change
    assert tracked(root) == before


def test_a_reached_monthly_budget_pauses_judging_until_next_month(repo, monkeypatch, capsys):
    from ajh import usage
    from ajh.cli import main

    root, board = repo(criteria={"caps": {"max_usd_per_month": 50}}), boards()
    layout = Layout(root)
    usage.append(layout.usage, {"at": "2026-10-02T08:00:00Z", "total": {"cost_usd": 50.5}})  # spent this month
    client = Client(board.transport, sleep=lambda s: None, retries=0)
    real_collect = collect.run
    monkeypatch.setattr(collect, "run", lambda root, client_=None, now=None: real_collect(root, client, NOW))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert main(["--repo", str(root), "collect"]) == 0  # as the workflow runs it
    out = capsys.readouterr()
    summary = json.loads(out.out)
    assert (summary["judge_needed"], summary["triage_packets"]) == (False, 0)
    assert summary["queued"] == {"triage": 0, "assess": 0} and summary["deferred"]["triage"] == 4  # all still waiting
    assert summary["budget"] == {
        "usd_per_month": 50,
        "month": "2026-10",
        "spent_usd": 50.5,
        "left_usd": 0.0,
        "reached": True,
        "resumes": "2026-11-01",
    }
    assert "::warning::Monthly budget reached: $50.50 of $50.00 spent in 2026-10" in out.err  # not a failure

    apply.run(root, summary["seal"])  # the judging steps are skipped; apply and publish still run
    meta = json.loads(layout.feed_meta.read_text())
    assert meta["usage"]["budget"]["paused"] is True and "judging_failed" not in meta
    readme = layout.readme.read_text()
    assert "- Judging is paused until 2026-11-01: the monthly budget is reached (see Usage)." in readme
    assert "Monthly budget reached: $50.50 of $50.00 spent in 2026-10. Judging is paused until 2026-11-01" in readme
    assert len(usage.load(layout.usage)) == 1  # nothing judged, nothing logged

    before = tracked(root)
    again = real_collect(root, client, NOW)
    apply.run(root, again["seal"])
    assert tracked(root) == before  # a paused feed commits nothing new

    november = real_collect(root, client, NOW.replace(month=11, day=2))
    assert (november["judge_needed"], november["budget"]["reached"], november["budget"]["spent_usd"]) == (
        True,
        False,
        0,
    )


def test_a_declined_posting_leaves_the_feed_and_is_never_judged_again(repo):
    from ajh import decisions

    root, board = repo(), boards()
    layout = Layout(root)
    full_tick(root, board, triage=triage, assess=assess)
    assert set(positions(root)) == {"acme:1", "acme:2"}
    decisions.record(root, "declined", "acme:2", "2026-10-13")
    summary = full_tick(root, board, triage=triage, assess=assess)
    assert (summary["declined"], summary["open_matches"]) == (1, 1) and set(positions(root)) == {"acme:1"}
    assert not (root / "jobs" / "acme" / "2.md").exists()  # its brief went with it
    latest = layout.latest.read_text()
    assert "## Declined by you (1)" in latest and "`acme:2`" in latest
    log = [json.loads(line) for line in (layout.feed / "changes" / "2026-10.jsonl").read_text().splitlines()]
    assert (log[-1]["event"], log[-1]["key"]) == ("declined", "acme:2")
    counts = json.loads(layout.feed_meta.read_text())["counts"]
    assert (counts["declined"], counts["published"], counts["pending_assess"], counts["pending_reassess"]) == (
        1,
        1,
        0,
        0,
    )
    assert "Your decisions: 0 applied, 1 declined" in layout.readme.read_text()

    (root / "profile" / "profile.md").write_text(
        "Seven years of mixed-methods research."
    )  # a new assessment generation
    collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    queued = json.loads(layout.plan.read_text())["queue"]
    assert "acme:2" not in queued["assess"] + queued["triage"] and "acme:1" in queued["assess"]


def test_an_applied_posting_is_marked_and_not_judged_again_after_a_search_change(repo):
    from ajh import decisions

    root, board = repo(), boards()
    layout = Layout(root)
    full_tick(root, board, triage=triage, assess=assess)
    decisions.record(root, "applied", "acme:1", "2026-10-06")
    full_tick(root, board, triage=triage, assess=assess)
    assert positions(root)["acme:1"]["applied"] == "2026-10-06" and "applied" not in positions(root)["acme:2"]
    readme = layout.readme.read_text()
    assert "| ✅ applied 2026-10-06 · [UX Researcher](jobs/acme/1.md) |" in readme
    assert "Your decisions: 1 applied, 0 declined" in readme

    (root / "profile" / "profile.md").write_text(
        "Seven years of mixed-methods research."
    )  # a new assessment generation
    summary = collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    queued = json.loads(layout.plan.read_text())["queue"]["assess"]
    assert "acme:2" in queued and "acme:1" not in queued  # the other one is assessed again; the applied one is not
    apply.run(root, summary["seal"])  # nothing judged this time
    counts = json.loads(layout.feed_meta.read_text())["counts"]
    assert counts["applied"] == 1 and "acme:1" in positions(root)  # still listed and marked
    assert counts["pending_reassess"] == len(queued)  # waiting: what the queue holds, never the applied one
    assert positions(root)["acme:1"]["applied"] == "2026-10-06"


def limits(root, **thresholds):
    """Set listing thresholds in the feed's criteria, as a person editing criteria.json would."""
    path = Layout(root).criteria
    criteria = json.loads(path.read_text())
    criteria["thresholds"] = {**criteria.get("thresholds", {}), **thresholds}
    path.write_text(json.dumps(criteria))


def test_hard_requirements_leave_postings_out_until_relaxed(repo):
    years = "Requires 7 years of experience leading research programs across several product teams."
    board = Boards(acme=[posting(1, "UX Researcher", text=years), posting(2, "Design Researcher"),
                         posting(3, "Head of Research")])  # fmt: skip

    def judged(job):
        fields = good_assessment(job, fit=80)
        if job["title"] == "UX Researcher":
            fields.update(required_years=7, required_years_quote="Requires 7 years of experience")
        if job["title"] == "Head of Research":
            fields["seniority"] = "staff"  # no years stated: only the seniority can leave it out
        return fields

    root = repo()
    layout = Layout(root)
    limits(root, max_required_years=3)
    summary = full_tick(root, board, assess=judged)
    assert summary["blocked"] == 1 and set(positions(root)) == {"acme:2", "acme:3"}
    assert rows(root)["1"].status == "R" and rows(root)["1"].note == "blk:years=7"
    assert (
        "Left out for a hard requirement in `thresholds` (required years or seniority): 1." in layout.readme.read_text()
    )

    limits(root, max_seniority="senior")  # takes a listed posting out without judging anything
    assert full_tick(root, board, assess=judged)["blocked"] == 1  # the summary counts it too
    assert set(positions(root)) == {"acme:2"} and rows(root)["3"].note == "blk:seniority=staff"
    log = [json.loads(line) for line in (layout.feed / "changes" / "2026-10.jsonl").read_text().splitlines()]
    assert (log[-1]["event"], log[-1]["key"]) == ("blocked", "acme:3")
    assert "## Left out for a hard requirement (1)" in layout.latest.read_text()
    meta = json.loads(layout.feed_meta.read_text())
    assert (meta["counts"]["blocked"], meta["counts"]["below_threshold"]) == (2, 0)
    again = collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    assert again["unblocked"] == 0 and json.loads(layout.plan.read_text())["queue"]["assess"] == []  # not re-queued

    limits(root, max_required_years=10, max_seniority=None)  # relaxed: both go back to be assessed
    relaxed = collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    assert relaxed["unblocked"] == 2 and sorted(json.loads(layout.plan.read_text())["queue"]["assess"]) == [
        "acme:1",
        "acme:3",
    ]
    full_tick(root, board, assess=judged)
    assert set(positions(root)) == {"acme:1", "acme:2", "acme:3"}


def test_a_floor_for_direct_matches_and_new_tier_bounds_reach_listed_postings(repo):
    root, board = repo(), boards()
    full_tick(root, board, triage=triage, assess=assess)
    assert (positions(root)["acme:1"]["tier"], positions(root)["acme:2"]["fit"]) == ("strong", 60)
    limits(root, direct_min=70, strong=90)
    summary = full_tick(root, board, triage=triage, assess=assess)  # nothing to judge
    feed = positions(root)
    assert set(feed) == {"acme:1"} and feed["acme:1"]["tier"] == "good"  # 88 is below the new strong bound
    log = [json.loads(line) for line in (Layout(root).feed / "changes" / "2026-10.jsonl").read_text().splitlines()]
    assert (log[-1]["event"], log[-1]["key"]) == ("removed", "acme:2")  # below a floor: no longer a match
    assert (summary["added"], summary["rescored"]) == (0, 0)


def test_other_listings_of_the_same_opening_are_attached_to_its_record(repo):
    root = repo(sources=(ACME, REMOTIVE))
    same = prose(50)
    board = Boards(acme=[posting(1, text=same), posting(2, text=same + " Based in New York.", location="New York, NY")])
    board.remotive = [
        {
            "id": 7,
            "title": "UX Researcher",
            "company_name": "Acme",
            "url": "https://remotive.com/remote-jobs/7",
            "description": prose(7),
        }
    ]
    full_tick(root, board)
    record = positions(root)["acme:1"]
    assert record["variants"] == [
        {"key": "acme:2", "location": "New York, NY", "url": "https://job-boards.greenhouse.io/x/jobs/2"}
    ]
    assert record["also_on"] == [
        {"key": "remotive:7", "label": "Remotive", "url": "https://remotive.com/remote-jobs/7"}
    ]
    brief = (root / "jobs" / "acme" / "1.md").read_text()
    assert "## Also listed" in brief and "- New York, NY: https://" in brief and "- via Remotive: https://" in brief
    assert list(positions(root)) == ["acme:1"]


def test_the_triage_check_counts_drops_that_turned_out_to_be_matches(repo, monkeypatch):
    root = repo()
    monkeypatch.setattr(advance, "is_canary", lambda key: key == "acme:3")
    full_tick(root, boards(), triage=triage, assess=lambda job: good_assessment(job, fit=70))
    layout = Layout(root)
    assert json.loads(layout.feed_meta.read_text())["canary"] == {"sampled": 1, "missed": 1}
    assert "1 of 1 dropped postings sampled for a second look turned out to be matches" in layout.readme.read_text()
    assert "acme:3" in positions(root) and rows(root)["3"].note == "-"


def test_a_record_that_went_missing_is_assessed_again_rather_than_guessed(repo):
    root, board = repo(), boards()
    full_tick(root, board, triage=triage, assess=assess)
    layout = Layout(root)
    layout.positions.write_text(
        "".join(line + "\n" for line in layout.positions.read_text().splitlines() if '"acme:1"' not in line)
    )
    full_tick(root, board, triage=triage, assess=lambda job: None)
    assert (rows(root)["1"].status, rows(root)["1"].ag, rows(root)["1"].fit) == ("K", 0, "-")
    full_tick(root, board, triage=triage, assess=assess)
    assert positions(root)["acme:1"]["fit"] == 88


def test_apply_refuses_changed_work_and_an_uncommitted_tree(repo):
    root, board = repo(), boards()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / ".gitignore").write_text(".hunter/work/\n")
    commit = [
        "git",
        "-C",
        str(root),
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "-m",
        "x",
    ]
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(commit, check=True)
    summary = collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    with pytest.raises(advance.WorkError, match="changed since"):
        apply.run(root, "0" * 32)
    (root / "profile" / "profile.md").write_text("edited after collect, not committed")
    with pytest.raises(advance.WorkError, match=r"uncommitted changes.*profile/profile\.md"):
        apply.run(root, summary["seal"])
    assert not (root / "state").exists() and not (root / "positions.jsonl").exists()
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "--", "profile/profile.md"], check=True)
    assert apply.run(root, summary["seal"])["open_matches"] == 0 and (root / "state" / "meta.json").exists()


def test_a_wanted_role_is_always_listed_and_related_work_only_above_the_bar(repo):
    root = repo()
    board = Boards(acme=[posting(1, title="UX Researcher"), posting(2, title="Product Analyst")])
    full_tick(
        root,
        board,
        assess=lambda job: good_assessment(job, fit=25, role_match="direct" if "UX" in job["title"] else "adjacent"),
    )
    feed = positions(root)
    assert list(feed) == ["acme:1"] and (feed["acme:1"]["fit"], feed["acme:1"]["tier"]) == (25, "stretch")
    assert rows(root)["2"].status == "R"


def test_postings_waiting_to_be_assessed_again_are_counted_and_shown(repo):
    root, board = repo(), boards()
    full_tick(root, board, triage=triage, assess=assess)
    meta = json.loads(Layout(root).feed_meta.read_text())
    assert meta["counts"]["pending_reassess"] == 0 and "assessed again" not in Layout(root).readme.read_text()
    (root / "profile" / "profile.md").write_text("Ten years of quantitative research.")
    full_tick(root, board, triage=triage, assess=lambda job: None)  # the profile changed and nothing was answered yet
    meta = json.loads(Layout(root).feed_meta.read_text())
    # both published postings and the one below the bar wait to be assessed again; the two stay listed meanwhile
    assert (
        meta["counts"]["pending_reassess"] == 3
        and meta["counts"]["published"] == 2
        and meta["counts"]["pending_assess"] == 0
    )
    assert (
        "- Waiting to be judged: 0 for triage, 0 for assessment, and 3 to be assessed again because the search changed."
        in (Layout(root).readme.read_text())
    )
    assert list(positions(root)) == ["acme:1", "acme:2"]
