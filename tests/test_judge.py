"""The judging stage: fixed batches, a retry for unanswered packets, and counts the engine checked."""

import json

from ajh import judge
from ajh.cli import main
from ajh.layout import Layout


def manifest(root, count):
    layout = Layout(root)
    rows = [
        f"T-{n:03d} | .hunter/work/packets/T-{n:03d}.md | .hunter/work/results/T-{n:03d}.txt | hunter-triage"
        for n in range(1, count + 1)
    ]
    layout.work.mkdir(parents=True, exist_ok=True)
    layout.manifest.write_text(
        "# Judging manifest: triage\n\npacket | packet file | result file | agent\n" + "\n".join(rows) + "\n"
    )
    return layout


def orchestrator(skip=lambda packet, attempt: False, ok=True, cost=0.01):
    """A fake orchestrator that answers every row of the batch manifest it is given, except those `skip` names."""
    calls = []

    def invoke(root, path):
        rows = (root / path).read_text().splitlines()[1:]
        calls.append([row.split(" | ")[0] for row in rows])
        for row in rows:
            packet = row.split(" | ")[0]
            if not skip(packet, len(calls)):
                (Layout(root).results / f"{packet}.txt").write_text(f"packet {packet} nonce ab12\nend 0\n")
        return {"ok": ok, "cost_usd": cost, "denied": 0}

    return invoke, calls


def test_packets_go_out_in_fixed_batches_of_twenty(tmp_path):
    manifest(tmp_path, 47)
    invoke, calls = orchestrator()
    report = judge.run(tmp_path, invoke)
    assert [len(batch) for batch in calls] == [20, 20, 7] and calls[2][-1] == "T-047"
    assert report == {
        "ok": True,
        "packets": 47,
        "results": 47,
        "missing": [],
        "batches": 3,
        "cost_usd": 0.03,
        "denied": 0,
    }


def test_a_packet_the_orchestrator_dropped_is_tried_once_more(tmp_path):
    manifest(tmp_path, 47)
    invoke, calls = orchestrator(skip=lambda packet, call: packet == "T-047" and call == 3)  # the observed failure
    report = judge.run(tmp_path, invoke)
    assert calls[3] == ["T-047"] and report["ok"] is True and report["results"] == 47 and report["batches"] == 4


def test_a_packet_still_unanswered_after_the_retry_makes_the_stage_incomplete(tmp_path):
    manifest(tmp_path, 3)
    invoke, calls = orchestrator(skip=lambda packet, call: packet == "T-002")
    report = judge.run(tmp_path, invoke)
    assert calls == [["T-001", "T-002", "T-003"], ["T-002"]]
    assert (report["ok"], report["results"], report["missing"]) == (False, 2, ["T-002"])
    assert "judging_failed" not in report  # some answers: the rest are issued again next time


def test_an_empty_result_file_is_not_an_answer(tmp_path):
    layout = manifest(tmp_path, 2)

    def invoke(root, path):
        (layout.results / "T-001.txt").write_text("packet T-001 nonce ab12\nend 0\n")
        (layout.results / "T-002.txt").write_text("")
        return {"ok": True, "cost_usd": 0.0, "denied": 0}

    assert judge.run(tmp_path, invoke)["missing"] == ["T-002"]


def test_an_orchestrator_error_is_listed_but_only_missing_results_make_the_stage_incomplete(tmp_path, monkeypatch):
    manifest(tmp_path, 2)
    invoke, _ = orchestrator(ok=False)  # it errored, yet both results were written
    report = judge.run(tmp_path, invoke)
    assert (report["ok"], report["results"], report["failures"]) == (True, 2, ["the orchestrator reported an error"])
    monkeypatch.setattr(judge, "CLAUDE", "no-such-command-for-this-test")
    for path in Layout(tmp_path).results.glob("T-*.txt"):
        path.unlink()
    missing_cli = judge.run(tmp_path)
    assert (missing_cli["ok"], missing_cli["missing"]) == (False, ["T-001", "T-002"])
    assert missing_cli["failures"][0] == "FileNotFoundError"


def test_nothing_to_judge_is_a_complete_stage(tmp_path):
    manifest(tmp_path, 0)
    invoke, calls = orchestrator()
    assert judge.run(tmp_path, invoke) == {
        "ok": True,
        "packets": 0,
        "results": 0,
        "missing": [],
        "batches": 0,
        "cost_usd": 0,
        "denied": 0,
    }
    assert calls == []


def test_batch_manifests_stay_outside_the_seal_and_are_never_read_as_answers(tmp_path):
    from ajh.collect import seal_of

    layout = manifest(tmp_path, 25)
    before = seal_of(layout.work)
    invoke, _ = orchestrator()
    judge.run(tmp_path, invoke)
    assert sorted(p.name for p in layout.results.glob("_batch-*")) == ["_batch-1-1.md", "_batch-1-2.md"]
    assert seal_of(layout.work) == before


def test_a_stage_with_no_answer_at_all_fails_and_leaves_its_report_for_apply(tmp_path):
    layout = manifest(tmp_path, 3)
    error = "API Error: 400 This API key is not scoped to a workspace"
    report = judge.run(tmp_path, lambda root, path: {"ok": False, "cost_usd": 0.0, "denied": 0, "detail": error})
    assert (report["judging_failed"], report["results"], report["batches"]) == (True, 0, 2)
    assert report["failures"] == [error, error]  # the first pass and the retry
    assert json.loads((layout.results / "_judging-triage.json").read_text()) == report


def test_claude_codes_own_error_line_is_kept_and_fails_the_command(tmp_path, monkeypatch, capsys):
    manifest(tmp_path, 2)
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps({"type": "result", "is_error": True, "total_cost_usd": 0,
                                 "result": "API Error: 400 This API key is not scoped to a workspace\nmore"}))  # fmt: skip
    stub = tmp_path / "claude"
    stub.write_text(f"#!/bin/sh\ncat '{reply}'\nexit 1\n")
    stub.chmod(0o755)
    monkeypatch.setattr(judge, "CLAUDE", str(stub))
    assert main(["--repo", str(tmp_path), "judge"]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["judging_failed"] is True
    assert printed["failures"][0] == "API Error: 400 This API key is not scoped to a workspace"
