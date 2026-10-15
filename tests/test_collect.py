"""`collect` across ticks, each tick starting from only what a fresh clone would have: the tracked tree."""

import json
from datetime import timedelta

from ajh import collect
from ajh.cli import main
from ajh.layout import Layout
from ajh.state import read_rows
from helpers import (
    ACME,
    CRITERIA,
    GLOBEX,
    JOBICY,
    NOW,
    REMOTIVE,
    Boards,
    frozen_hash,
    jobicy_posting,
    judge,
    plan,
    posting,
    prose,
    remotive_posting,
    rows,
    tick,
    tracked,
)


def test_first_tick_proposes_new_rows_and_changes_nothing_tracked(repo):
    root = repo()
    before = tracked(root)
    summary = tick(root, Boards(acme=[posting(1), posting(2)]), adopt=False)
    assert tracked(root) == before
    assert summary["new"] == 2 and summary["queued"] == {"triage": 2, "assess": 0} and summary["judge_needed"] is True
    assert summary["sources"] == {"fetched": 1, "gated": 0, "failed": 0, "failures": {}}
    proposed = read_rows(Layout(root).state_next / "jobs" / "acme.tsv")
    assert {jid: (row.status, row.first_seen) for jid, row in proposed.items()} == {
        "1": ("P", "2026-10-13"),
        "2": ("P", "2026-10-13"),
    }
    assert plan(root)["queue"]["triage"] == ["acme:1", "acme:2"]
    snapshot = json.loads((Layout(root).snapshot / "acme.json").read_text())
    assert snapshot["1"]["title"] == "UX Researcher" and snapshot["1"]["description"] == prose(1)


def test_an_unchanged_board_proposes_a_byte_identical_state(repo):
    root, boards = repo(), Boards(acme=[posting(1), posting(2)])
    tick(root, boards)
    first = tracked(root)
    summary = tick(root, boards, now=NOW + timedelta(hours=4))
    assert tracked(root) == first and summary["new"] == 0
    tick(root, boards, now=NOW + timedelta(days=1))
    changed = {name for name, data in tracked(root).items() if first[name] != data}
    assert changed == {"state/meta.json", "state/sources.json"}  # only the day moved


def test_stage0_drops_are_not_stored_and_unreadable_values_pass(repo):
    root = repo(
        criteria={"locations": {"regions": ["US"]}, "companies": {"exclude": ["Globex"]}}, sources=(ACME, GLOBEX)
    )
    boards = Boards(
        acme=[posting(1), posting(2, location="Berlin, Germany"), posting(3, location="Remote")], globex=[posting(9)]
    )
    summary = tick(root, boards)
    assert set(rows(root)) == {"1", "3"} and rows(root, "globex") == {}
    assert summary["dropped"] == {"location": 1, "company": 1}
    boards.jobs["acme"][1]["location"] = {"name": "Munich, Germany"}  # a published posting moves out of bounds
    judge(root, "acme", "1", "A", fit="80")
    tick(root, boards)
    assert set(rows(root)) == {"3"} and plan(root)["removed"] == ["acme:1"]


def test_a_cosmetic_edit_keeps_the_verdict_and_a_rewrite_or_retitle_reopens_it(repo):
    root, boards = repo(), Boards(acme=[posting(1), posting(2)])
    tick(root, boards)
    judge(root, "acme", "1", "A", fit="80", dsh=frozen_hash(boards.jobs["acme"][1]))
    judge(root, "acme", "2", "D")
    boards.jobs["acme"][1]["content"] = f"<p>{prose(1)} Apply today via our partner portal.</p>"
    summary = tick(root, boards)
    assert summary["changed"] == 0 and rows(root)["1"].status == "A" and rows(root)["1"].fit == "80"
    boards.jobs["acme"][1]["content"] = f"<p>{prose(777)}</p>"
    boards.jobs["acme"][2]["title"] = "Senior UX Researcher"
    summary = tick(root, boards)
    table = rows(root)
    assert summary["changed"] == 2 and plan(root)["changed"] == ["acme:1", "acme:2"]
    assert (table["1"].status, table["1"].note, table["1"].fit, table["1"].dsh) == ("P", "was:A", "-", "-")
    assert (table["2"].status, table["2"].note, table["2"].tg) == ("P", "-", 0)
    assert plan(root)["queue"]["triage"] == ["acme:1", "acme:2"]  # the published one is refreshed first
    assert table["1"].first_seen == "2026-10-13"


def test_absence_closes_after_two_misses_and_a_return_revives_the_verdict(repo):
    root, boards = repo(), Boards(acme=[posting(1), posting(2), posting(3)])
    tick(root, boards)
    judge(root, "acme", "2", "A", fit="70")
    judge(root, "acme", "3", "D")
    gone = {jid: boards.jobs["acme"].pop(jid) for jid in (2, 3)}
    assert tick(root, boards)["closed"] == 0 and rows(root)["2"].miss == 1 and rows(root)["2"].status == "A"
    summary = tick(root, boards)
    assert summary["closed"] == 1 and plan(root)["closed"] == ["acme:2"]  # only published postings are announced
    assert (rows(root)["2"].status, rows(root)["2"].note) == ("X", "A@2026-10-13")
    assert (rows(root)["3"].status, rows(root)["3"].note) == ("X", "D@2026-10-13")
    boards.jobs["acme"].update(gone)
    summary = tick(root, boards, now=NOW + timedelta(days=5))
    table = rows(root)
    assert summary["revived"] == 2 and summary["new"] == 0
    assert (table["3"].status, table["3"].tg) == ("D", 1)  # a triage drop comes back without a model call
    assert (table["2"].status, table["2"].tg, table["2"].ag, table["2"].fit) == (
        "K",
        1,
        0,
        "-",
    )  # details were dropped: assess again
    # posting 1 was never judged in this test, so it is still waiting for triage
    assert plan(root)["queue"] == {"triage": ["acme:1"], "assess": ["acme:2"], "deferred": {"triage": 0, "assess": 0}}


def test_a_single_miss_is_forgiven_and_tombstones_expire(repo):
    root, boards = repo(), Boards(acme=[posting(1), posting(2)])
    tick(root, boards)
    held = boards.jobs["acme"].pop(2)
    tick(root, boards)
    boards.jobs["acme"][2] = held
    tick(root, boards)
    assert rows(root)["2"].miss == 0 and rows(root)["2"].status == "P"
    del boards.jobs["acme"][2]
    tick(root, boards)
    tick(root, boards)
    assert rows(root)["2"].status == "X"
    tick(root, boards, now=NOW + timedelta(days=14))
    assert "2" in rows(root)
    tick(root, boards, now=NOW + timedelta(days=15))
    assert set(rows(root)) == {"1"}


def test_a_failed_fetch_keeps_the_previous_state_and_closes_nothing(repo):
    root, boards = repo(), Boards(acme=[posting(1), posting(2)])
    tick(root, boards)
    before = rows(root)
    boards.status["acme"] = 503
    for _ in range(3):
        summary = tick(root, boards)
    assert rows(root) == before
    assert summary["sources"]["failed"] == 1 and "HttpError" in summary["sources"]["failures"]["acme"]
    info = json.loads((Layout(root).state / "sources.json").read_text())["acme"]
    assert info["error"] == "HTTP 503" and info["fail_since"] == "2026-10-13" and info["ok_day"] == "2026-10-13"
    del boards.status["acme"]
    tick(root, boards)
    info = json.loads((Layout(root).state / "sources.json").read_text())["acme"]
    assert "error" not in info and "fail_since" not in info


def test_a_rate_limited_source_is_left_alone_until_its_interval_passes(repo):
    root = repo(sources=(dict(ACME, min_fetch_interval_seconds=21600),))
    boards = Boards(acme=[posting(1)])
    tick(root, boards)
    summary = tick(root, boards, now=NOW + timedelta(hours=1))
    assert boards.requests == ["acme"] and summary["sources"]["gated"] == 1 and summary["sources"]["fetched"] == 0
    assert rows(root)["1"].miss == 0
    tick(root, boards, now=NOW + timedelta(hours=6))
    assert boards.requests == ["acme", "acme"]
    boards.status["acme"] = 500  # a failed attempt uses up the interval too
    tick(root, boards, now=NOW + timedelta(hours=12))
    tick(root, boards, now=NOW + timedelta(hours=13))
    assert boards.requests == ["acme"] * 3


def test_a_sharp_shrink_is_held_for_one_tick(repo):
    root, boards = repo(), Boards(acme=[posting(n) for n in range(1, 31)])
    tick(root, boards)
    full = dict(boards.jobs["acme"])
    boards.jobs["acme"] = {jid: p for jid, p in full.items() if jid <= 5}
    summary = tick(root, boards)
    assert summary["held"] == ["acme"] and all(row.miss == 0 for row in rows(root).values())
    boards.jobs["acme"] = full  # the board recovered: nothing was lost
    assert tick(root, boards)["held"] == [] and all(row.miss == 0 for row in rows(root).values())
    boards.jobs["acme"] = {jid: p for jid, p in full.items() if jid <= 5}
    tick(root, boards)
    summary = tick(root, boards)  # the shrink repeated, so it is real
    assert summary["held"] == [] and sum(row.miss == 1 for row in rows(root).values()) == 25
    assert sum(row.status == "X" for row in tick(root, boards) and rows(root).values()) == 25


def test_a_rolling_feed_never_closes_by_absence_but_expires_by_age(repo):
    root = repo(sources=(dict(ACME, membership_mode="rolling"),))
    boards = Boards(acme=[posting(1), posting(2)])
    tick(root, boards)
    del boards.jobs["acme"][2]
    for days in (1, 2, 45):
        tick(root, boards, now=NOW + timedelta(days=days))
    assert rows(root)["2"].status == "P" and rows(root)["2"].miss == 0
    tick(root, boards, now=NOW + timedelta(days=46))
    assert rows(root)["2"].status == "X" and rows(root)["1"].status == "P"


def test_a_backfill_limit_parks_old_postings_once_and_later_arrivals_are_never_parked(repo):
    root = repo(criteria={"backfill": {"days": 45}})
    boards = Boards(acme=[posting(1), posting(2, published="2025-01-28T18:57:29-05:00"), posting(3, published=None)])
    summary = tick(root, boards)
    assert summary["parked"] == 1 and summary["new"] == 2
    assert {jid: row.status for jid, row in rows(root).items()} == {"1": "P", "2": "S", "3": "P"}
    assert plan(root)["queue"]["triage"] == ["acme:1", "acme:3"]  # dated before undated
    boards.jobs["acme"][4] = posting(4, published="2024-06-01T00:00:00Z")
    tick(root, boards)
    assert rows(root)["4"].status == "P" and rows(root)["2"].status == "S"


def test_by_default_nothing_is_parked_for_its_age(repo):
    root = repo()
    tick(root, Boards(acme=[posting(2, published="2020-01-01T00:00:00Z")]))
    assert rows(root)["2"].status == "P"


def test_one_opening_listed_per_location_is_judged_once(repo):
    root = repo()
    same = prose(50)
    boards = Boards(
        acme=[
            posting(1, text=same),
            posting(2, text=same + " Based in our New York office.", location="New York, NY"),
            posting(3, text=prose(51)),
            posting(4, title="Product Analyst", text=same),
        ]
    )
    summary = tick(root, boards)
    table = rows(root)
    assert summary["variants"] == 1 and (table["2"].status, table["2"].note) == ("V", "acme:1")
    assert table["3"].status == "P"  # same title, different text: its own posting
    assert table["4"].status == "P"  # same text, different title: its own posting
    assert plan(root)["queue"]["triage"] == ["acme:1", "acme:3", "acme:4"]
    del boards.jobs["acme"][1]  # when the representative closes, a variant takes over and is judged
    tick(root, boards)
    tick(root, boards)
    assert rows(root)["1"].status == "X" and (rows(root)["2"].status, rows(root)["2"].note) == ("P", "-")


def test_an_aggregator_copy_defers_to_the_employers_own_posting(repo):
    root = repo(sources=(ACME, REMOTIVE))
    boards = Boards(acme=[posting(1)])
    boards.remotive = [
        {
            "id": 7,
            "title": "UX  Researcher",
            "company_name": "Acme, Inc.",
            "url": "https://remotive.com/remote-jobs/7",
            "description": prose(7),
        },
        {
            "id": 8,
            "title": "UX Researcher",
            "company_name": "Initech",
            "url": "https://remotive.com/remote-jobs/8",
            "description": prose(8),
        },
    ]
    tick(root, boards)
    copies = rows(root, "remotive")
    assert (copies["7"].status, copies["7"].note) == ("V", "acme:1") and copies["8"].status == "P"
    assert plan(root)["queue"]["triage"] == ["acme:1", "remotive:8"]
    del boards.jobs["acme"][1]  # the official posting closes; the copy is then judged on its own
    tick(root, boards)
    tick(root, boards)
    assert rows(root)["1"].status == "X" and rows(root, "remotive")["7"].status == "P"


def test_copies_of_one_opening_on_several_aggregators_are_judged_once(repo):
    root = repo(sources=(ACME, REMOTIVE, JOBICY))
    boards = Boards(acme=[posting(1)])
    boards.remotive = [
        remotive_posting(7, company="Initech"),
        remotive_posting(8, title="Research Ops", company="Initech"),
    ]
    boards.jobicy = [jobicy_posting(3, company="Initech, Inc."), jobicy_posting(4)]
    tick(root, boards)
    # with none judged yet, the lowest key represents the copies; one of the employer's own posting defers to it
    assert (rows(root, "remotive")["7"].status, rows(root, "remotive")["7"].note) == ("V", "jobicy:3")
    assert (rows(root, "jobicy")["4"].status, rows(root, "jobicy")["4"].note) == ("V", "acme:1")
    assert plan(root)["queue"]["triage"] == ["acme:1", "jobicy:3", "remotive:8"]


def test_a_copy_already_judged_represents_the_others_until_it_closes(repo):
    root = repo(sources=(REMOTIVE, JOBICY))
    boards = Boards()
    boards.remotive = [remotive_posting(7, company="Initech"), remotive_posting(8, title="Research Ops")]
    tick(root, boards)
    judge(root, "remotive", "7", "D")
    boards.jobicy = [jobicy_posting(3, company="Initech")]  # the same opening on a second aggregator, lower key
    tick(root, boards)
    assert (rows(root, "jobicy")["3"].status, rows(root, "jobicy")["3"].note) == ("V", "remotive:7")
    assert plan(root)["queue"]["triage"] == ["remotive:8"]
    boards.remotive = [remotive_posting(8, title="Research Ops")]  # the judged copy closes
    tick(root, boards)
    tick(root, boards)
    assert rows(root, "remotive")["7"].status == "X"
    assert (rows(root, "jobicy")["3"].status, rows(root, "jobicy")["3"].note) == ("P", "-")
    assert plan(root)["queue"]["triage"] == ["jobicy:3", "remotive:8"]


def test_within_one_aggregator_a_copy_also_needs_near_identical_text(repo):
    root = repo(sources=(JOBICY,))
    same = prose(30)
    boards = Boards()
    boards.jobicy = [
        jobicy_posting(3, text=same),
        jobicy_posting(5, text=same + " Based in Austin."),
        jobicy_posting(6, text=prose(6)),  # same company and title, another opening
    ]
    tick(root, boards)
    copies = rows(root, "jobicy")
    assert [(copies[j].status, copies[j].note) for j in ("3", "5", "6")] == [("P", "-"), ("V", "jobicy:3"), ("P", "-")]


def test_changed_targets_reopen_triage_drops_and_a_changed_profile_requeues_assessments(repo):
    root, boards = repo(), Boards(acme=[posting(1), posting(2), posting(3)])
    tick(root, boards)
    judge(root, "acme", "1", "D")
    judge(root, "acme", "2", "A", fit="80", dsh=frozen_hash(boards.jobs["acme"][2]))
    judge(root, "acme", "3", "K")
    (Layout(root).state / "titles.tsv").write_text("account executive\t1\n")
    (root / "profile" / "profile.md").write_text("Ten years of quantitative research.")
    tick(root, boards)
    assert rows(root)["1"].status == "D" and rows(root)["2"].status == "A"  # stale but still published
    assert plan(root)["queue"]["assess"] == ["acme:2", "acme:3"] and plan(root)["generations"] == {
        "triage": 1,
        "assess": 2,
    }
    repo(criteria={"targets": {"roles": ["Product Analyst"]}}, profile="Ten years of quantitative research.")
    tick(root, boards)
    assert (rows(root)["1"].status, rows(root)["1"].tg) == ("P", 0) and plan(root)["generations"] == {
        "triage": 2,
        "assess": 3,
    }
    assert (Layout(root).state / "titles.tsv").read_text() == ""


def test_a_title_already_judged_unrelated_skips_the_model(repo):
    root, boards = repo(), Boards(acme=[posting(1)])
    tick(root, boards)
    (Layout(root).state / "titles.tsv").write_text("account executive\t1\n")
    boards.jobs["acme"][2] = posting(2, title="Account Executive!")
    summary = tick(root, boards)
    assert (rows(root)["2"].status, rows(root)["2"].tg) == ("D", 1) and summary["queued"]["triage"] == 1


def test_the_queue_is_capped_and_puts_preferred_companies_and_newer_postings_first(repo):
    root = repo(
        sources=(ACME, GLOBEX),
        criteria={"caps": {"max_triage": 3, "max_assess": 1, "max_attempts": 3}, "companies": {"prefer": ["Globex"]}},
    )
    boards = Boards(
        acme=[
            posting(1, published="2026-10-12T00:00:00Z"),
            posting(2, published="2026-10-01T00:00:00Z"),
            posting(3, published="2026-10-11T00:00:00Z"),
        ],
        globex=[posting(5, published="2026-09-20T00:00:00Z")],
    )
    summary = tick(root, boards)
    assert plan(root)["queue"]["triage"] == ["globex:5", "acme:1", "acme:3"]
    assert summary["deferred"] == {"triage": 1, "assess": 0} and summary["queued"] == {"triage": 3, "assess": 0}
    for jid in ("1", "2", "3"):
        judge(root, "acme", jid, "K")
    assert tick(root, boards)["deferred"]["assess"] == 2 and plan(root)["queue"]["assess"] == ["acme:1"]


def test_a_source_removed_from_the_configuration_is_retired(repo):
    root, boards = repo(sources=(ACME, GLOBEX)), Boards(acme=[posting(1)], globex=[posting(5)])
    tick(root, boards)
    judge(root, "globex", "5", "A", fit="90")
    repo(sources=(ACME,))
    tick(root, boards)
    assert rows(root, "globex")["5"].status == "X" and plan(root)["closed"] == ["globex:5"]
    assert "globex" not in json.loads((Layout(root).state / "sources.json").read_text())
    assert boards.requests.count("globex") == 1


def test_the_seal_covers_everything_the_engine_wrote_but_not_results(repo):
    root = repo()
    summary = tick(root, Boards(acme=[posting(1)]), adopt=False)
    layout = Layout(root)
    assert summary["seal"] == layout.seal.read_text().strip() == collect.seal_of(layout.work)
    layout.results.mkdir()
    (layout.results / "T-001.txt").write_text("a model wrote this")
    assert collect.seal_of(layout.work) == summary["seal"]
    (layout.snapshot / "acme.json").write_text("{}")
    assert collect.seal_of(layout.work) != summary["seal"]


def test_configuration_problems_stop_with_exit_2_and_say_what_to_fix(repo, capsys, tmp_path):
    root = repo(criteria={"targets": {"roles": []}})
    assert main(["--repo", str(root), "collect"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "CriteriaError" and "at least one role" in report["detail"]
    repo(sources=({"id": "bad id", "ats": "greenhouse"}, {"id": "ok", "ats": "fax", "company": "X"}))
    (root / "profile" / "criteria.json").write_text(json.dumps(CRITERIA))
    assert main(["--repo", str(root), "collect"]) == 2
    detail = json.loads(capsys.readouterr().out)["detail"]
    assert "bad id" in detail and "unknown source type 'fax'" in detail
    assert not Layout(root).work.exists()
