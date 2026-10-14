"""profile/decisions.jsonl: what the person applied to or declined, recorded without free text."""

import json

import pytest
from ajh import audit, decisions
from ajh.cli import main
from ajh.layout import Layout
from helpers import Boards, full_tick, posting


def test_a_decision_is_recorded_and_the_last_line_for_a_key_wins(repo, capsys):
    root = repo()
    full_tick(root, Boards(acme=[posting(1)]))
    assert main(["--repo", str(root), "decide", "declined", "acme:1", "--date", "2026-10-12"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["recorded"] == {"key": "acme:1", "decision": "declined", "date": "2026-10-12"} and first["known"]
    decisions.record(root, "applied", "acme:1", "2026-10-13")  # a change of mind
    later = decisions.record(root, "applied", "greenhouse:6204360004")  # not tracked yet: recorded, and said so
    assert later["known"] is False and "does not track" in later["next"]
    path = Layout(root).decisions
    assert [json.loads(line)["decision"] for line in path.read_text().splitlines()] == [
        "declined",
        "applied",
        "applied",
    ]
    assert decisions.load(path)["acme:1"] == {"decision": "applied", "date": "2026-10-13"}

    for bad in (["applied", "acme"], ["applied", "acme:1", "--date", "13 Oct"], ["declined", ":1"]):
        assert main(["--repo", str(root), "decide", *bad]) == 2
        assert "DecisionError" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # a decision other than applied or declined is refused before anything runs
        main(["--repo", str(root), "decide", "maybe", "acme:1"])
    assert len(path.read_text().splitlines()) == 3  # nothing was written for the refused ones


def test_audit_checks_the_file_by_its_structure_and_warns_about_unknown_keys(repo, capsys):
    root = repo()
    full_tick(root, Boards(acme=[posting(1)]))
    layout = Layout(root)
    lines = [
        {"key": "acme:1", "decision": "applied", "date": "2026-10-14"},
        {"key": "figma:6204360004", "decision": "declined", "date": "2026-10-14"},  # a long id is no phone number
    ]
    layout.decisions.write_text("".join(json.dumps(line) + "\n" for line in lines))
    assert audit.check(root) == []
    assert main(["--repo", str(root), "audit"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["audit"] == "passed" and out["warnings"] == [
        "profile/decisions.jsonl names figma:6204360004, which this feed does not track (closed, or on a board it "
        "does not watch); the decision waits for it"
    ]
    layout.decisions.write_text(
        '{"key": "acme:1", "decision": "applied", "date": "2026-10-14", "note": "call +1 412 555 0100"}\n'
        "not json\n"
        '{"key": "acme", "decision": "declined", "date": "2026-10-14"}\n'
        '{"key": "acme:1", "decision": "liked", "date": "2026-10-14"}\n'
        '{"key": "acme:1", "decision": "applied", "date": "2026-02-30"}\n'
    )
    assert audit.check(root) == [
        "profile/decisions.jsonl line 1 has fields other than key, decision and date: note",
        "profile/decisions.jsonl line 2 is not JSON",
        "profile/decisions.jsonl line 3 has a key that is not <source>:<id>: 'acme'",
        "profile/decisions.jsonl line 4 has a decision other than applied or declined: 'liked'",
        "profile/decisions.jsonl line 5 has a date that is not YYYY-MM-DD: '2026-02-30'",
    ]
    assert decisions.load(layout.decisions) == {}  # the engine skips what audit would reject


def test_an_iso_date_in_the_profile_is_not_a_phone_number_but_a_phone_number_still_is(repo):
    root = repo(profile="Research lead since 2026-10-14. Before that, five years of mixed-methods work.")
    assert audit.check(root) == []
    (root / "profile" / "profile.md").write_text("Call 412-555-0100 or +1 (412) 555 0100.")
    assert audit.check(root) == ["profile/profile.md contains a phone number"]
