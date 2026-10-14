"""`advance`: triage results in, assessment packets out, with result files treated as untrusted."""

import json

import pytest
from ajh import advance
from ajh.cli import main
from ajh.layout import Layout
from ajh.state import read_rows, tag
from helpers import Boards, adopt_state, judge, plan, posting, tick

TITLES = {
    1: "UX Researcher",
    2: "Account Executive",
    3: "Account Executive",
    4: "Product Analyst",
    5: "Backend Engineer",
}
ANSWERS = {
    "UX Researcher": "keep",
    "Account Executive": "drop_title",
    "Product Analyst": "keep",
    "Backend Engineer": "drop",
}


def boards():
    return Boards(acme=[posting(n, title=title) for n, title in TITLES.items()])


def answer(root, verdict_for=ANSWERS.get, skip=(), nonce=None):
    """Write triage result files the way a subagent would, from the packet index."""
    layout = Layout(root)
    snapshot = json.loads((layout.snapshot / "acme.json").read_text())
    for packet, entry in json.loads(layout.packet_index.read_text()).items():
        if entry["stage"] != "triage":
            continue
        lines = [f"packet {packet} nonce {nonce or entry['nonce']}"]
        for local, key in entry["items"].items():
            if key not in skip:
                lines.append(f"{local} {verdict_for(snapshot[key.split(':')[1]]['title'])}")
        layout.results.mkdir(parents=True, exist_ok=True)
        (layout.results / f"{packet}.txt").write_text("\n".join([*lines, f"end {len(lines) - 1}"]) + "\n")


def proposed(root, source="acme"):
    return read_rows(Layout(root).state_next / "jobs" / f"{source}.tsv")


@pytest.fixture(autouse=True)
def no_canaries(monkeypatch):
    monkeypatch.setattr(advance, "is_canary", lambda key: False)


def test_triage_results_are_folded_in_and_assessment_packets_prepared(repo):
    root = repo()
    summary = tick(root, boards(), adopt=False)
    assert summary["triage_packets"] == 1
    answer(root)
    result = advance.run(root, summary["seal"])
    assert result["triage"] == {"kept": 2, "dropped": 3, "rejected": 0, "unanswered": 0, "escalated": 0, "canary": 0}
    assert result["assess_queued"] == 2 and result["assess_packets"] == 1 and result["seal"] != summary["seal"]
    table = proposed(root)
    assert {jid: (row.status, row.tg) for jid, row in table.items()} == {
        "1": ("K", 1),
        "2": ("D", 1),
        "3": ("D", 1),
        "4": ("K", 1),
        "5": ("D", 1),
    }
    layout = Layout(root)
    assert (layout.state_next / "titles.tsv").read_text() == "account executive\t1\n"  # the plain drop is not cached
    assert plan(root)["queue"]["assess"] == ["acme:1", "acme:4"] and "hunter-assess" in layout.manifest.read_text()
    assert "### A0001\ntitle: UX Researcher" in (layout.packets / "A-001.md").read_text()
    assert layout.seal.read_text().strip() == result["seal"] == advance.seal_of(layout.work)
    assert not (root / "state").exists()  # still nothing tracked has changed


def test_a_title_kept_once_is_never_cached_as_unrelated(repo):
    root = repo()
    tick(root, boards(), adopt=False)
    seen = []

    def split_verdict(title):
        seen.append(title)
        return "keep" if title == "Account Executive" and seen.count(title) == 1 else ANSWERS[title]

    answer(root, split_verdict)
    advance.run(root)
    assert (Layout(root).state_next / "titles.tsv").read_text() == ""


def test_unanswered_postings_are_asked_again_free_and_invalid_answers_are_charged(repo):
    root = repo(criteria={"caps": {"max_triage": 100, "max_assess": 100, "max_attempts": 2}})
    board = boards()
    tick(root, board, adopt=False)
    answer(root, lambda title: "perhaps" if title == "Backend Engineer" else ANSWERS[title], skip={"acme:1"})
    result = advance.run(root)
    assert result["triage"]["unanswered"] == 1 and result["triage"]["rejected"] == 1
    table = proposed(root)
    assert (table["1"].status, table["1"].attempts, table["1"].note) == ("P", 0, "-")
    assert (table["5"].status, table["5"].attempts, tag(table["5"], "fb")) == ("P", 1, "verdict")
    # next tick: the rejected posting is asked again with fixed-wording feedback, then escalated on a second failure
    adopt_state(root)
    summary = tick(root, board, adopt=False)
    packet = (Layout(root).packets / "T-001.md").read_text()
    assert (
        summary["queued"]["triage"] == 2
        and "An earlier answer for this posting was rejected: the verdict was missing" in packet
    )
    answer(root, lambda title: "perhaps")
    assert advance.run(root)["triage"] == {
        "kept": 0,
        "dropped": 0,
        "rejected": 1,
        "unanswered": 0,
        "escalated": 1,
        "canary": 0,
    }
    table = proposed(root)
    assert (table["5"].status, table["5"].tg, table["5"].attempts, table["5"].note) == ("K", 1, 0, "-")
    assert (table["1"].status, table["1"].attempts) == ("P", 1)


def test_a_wrong_nonce_invalidates_the_whole_file(repo):
    root = repo()
    tick(root, boards(), adopt=False)
    answer(root, nonce="0000")
    assert advance.run(root)["triage"]["rejected"] == 5
    assert all(row.status == "P" and tag(row, "fb") == "header" for row in proposed(root).values())


def test_no_results_at_all_changes_nothing_and_running_twice_is_harmless(repo):
    root = repo()
    summary = tick(root, boards(), adopt=False)
    before = proposed(root)
    first = advance.run(root, summary["seal"])
    assert first["triage"]["unanswered"] == 5 and proposed(root) == before and first["assess_packets"] == 0
    answer(root)
    second = advance.run(root, first["seal"])
    third = advance.run(root, second["seal"])
    assert second["triage"]["kept"] == 2 and third["triage"] == dict.fromkeys(second["triage"], 0)
    assert third["assess_queued"] == 2


def test_work_that_changed_since_the_engine_wrote_it_is_refused(repo, capsys):
    root = repo()
    summary = tick(root, boards(), adopt=False)
    answer(root)  # result files are expected and do not break the seal
    layout = Layout(root)
    with pytest.raises(advance.WorkError):
        advance.run(root, "0" * 32)
    (layout.snapshot / "acme.json").write_text("{}")
    with pytest.raises(advance.WorkError):
        advance.run(root, summary["seal"])
    layout.seal.write_text(advance.seal_of(layout.work) + "\n")  # forging the seal file does not help
    assert main(["--repo", str(root), "advance", "--seal", summary["seal"]]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "WorkError"
    assert all(row.status == "P" for row in proposed(root).values())


def test_a_sample_of_drops_is_assessed_anyway_to_measure_what_triage_misses(repo, monkeypatch):
    root = repo()
    tick(root, boards(), adopt=False)
    answer(root)
    monkeypatch.setattr(advance, "is_canary", lambda key: key == "acme:5")
    result = advance.run(root)
    table = proposed(root)
    assert result["triage"]["canary"] == 1 and (table["5"].status, tag(table["5"], "canary")) == ("K", "")
    assert plan(root)["queue"]["assess"] == ["acme:1", "acme:4", "acme:5"]  # canaries use what is left of the cap
    monkeypatch.undo()
    assert sum(advance.is_canary(f"acme:{n}") for n in range(5000)) in range(60, 141)  # about one in fifty


def test_a_published_posting_that_triage_now_drops_loses_its_record(repo):
    root, board = repo(), boards()
    tick(root, board)
    judge(root, "acme", "1", "A", fit="80", dsh="0" * 16)
    board.jobs["acme"][1]["title"] = "Backend Engineer"
    tick(root, board, adopt=False)
    answer(root)
    advance.run(root)
    assert proposed(root)["1"].status == "D" and proposed(root)["1"].note == "-" and plan(root)["removed"] == ["acme:1"]
