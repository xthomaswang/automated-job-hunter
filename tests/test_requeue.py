"""`hunter requeue`: judge parked postings, or every assessed posting, again."""

import json

from ajh import apply, collect, decisions, requeue
from ajh.cli import main
from ajh.http import Client
from ajh.layout import Layout
from helpers import NOW, Boards, full_tick, good_assessment, posting, rows

TITLES = {1: "UX Researcher", 2: "Design Researcher", 3: "Account Executive", 4: "Product Analyst"}
FITS = {"UX Researcher": 88, "Design Researcher": 60, "Product Analyst": 30}


def judge(job):
    return good_assessment(
        job, fit=FITS[job["title"]], role_match="adjacent" if job["title"] == "Product Analyst" else "direct"
    )


def triage(job):
    return "drop_title" if job["title"] == "Account Executive" else "keep"


def test_assessed_postings_are_assessed_again_and_stay_listed_meanwhile(repo, capsys):
    root = repo()
    board = Boards(acme=[posting(n, title=t) for n, t in TITLES.items()])
    full_tick(root, board, triage=triage, assess=judge)
    decisions.record(root, "applied", "acme:2", "2026-10-06")  # an applied posting is never judged again
    result = requeue.run(root, "assessed")
    assert (result["requeued"], result["stage"], result["estimated_cost_usd"]) == (2, "assess", 0.04)
    assert {jid: row.ag for jid, row in rows(root).items() if row.status in "AR"} == {"1": 0, "2": 1, "4": 0}
    summary = collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    layout = Layout(root)
    assert sorted(json.loads(layout.plan.read_text())["queue"]["assess"]) == ["acme:1", "acme:4"]
    apply.run(root, summary["seal"])  # nothing judged yet
    listed = {json.loads(line)["key"] for line in layout.positions.read_text().splitlines()}
    assert listed == {"acme:1", "acme:2"}  # still listed while waiting
    assert json.loads(layout.feed_meta.read_text())["counts"]["pending_reassess"] == 2
    assert main(["--repo", str(root), "requeue", "--assessed"]) == 0  # already waiting: nothing more to do
    assert json.loads(capsys.readouterr().out) == {
        "requeued": 0,
        "stage": "assess",
        "estimated_cost_usd": 0.0,
        "next": "Nothing to requeue.",
    }


def test_parked_postings_go_to_triage(repo):
    root = repo(criteria={"backfill": {"days": 30}})
    board = Boards(acme=[posting(1, published="2026-01-05T00:00:00Z"), posting(2, title="Design Researcher")])
    full_tick(root, board, triage=triage, assess=judge)
    assert {jid: row.status for jid, row in rows(root).items()} == {"1": "S", "2": "A"}
    assert requeue.run(root, "parked")["requeued"] == 1 and rows(root)["1"].status == "P"
    collect.run(root, Client(board.transport, sleep=lambda s: None, retries=0), NOW)
    assert json.loads(Layout(root).plan.read_text())["queue"]["triage"] == ["acme:1"]
