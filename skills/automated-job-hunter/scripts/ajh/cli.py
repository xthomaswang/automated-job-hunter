"""Command line for the engine. Every command prints one JSON object.

Exit status: 0 done, 1 the push failed, 2 a person has to fix something (the JSON says what).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import advance, apply, audit, collect, decisions, judge, publish, registry, requeue, scaffold, tick, timer
from .advance import WorkError
from .collect import ConfigError
from .criteria import CriteriaError
from .decisions import DecisionError
from .layout import Layout
from .model import SourceError
from .packets import ProfileTooLong
from .publish import PublishBlocked
from .scaffold import ScaffoldError
from .sources import load_own
from .state import StateError, read_json
from .timer import TimerError

# Problems a person has to fix. Each is reported as JSON with exit status 2.
HANDLED = (
    CriteriaError,
    ConfigError,
    DecisionError,
    StateError,
    WorkError,
    ProfileTooLong,
    PublishBlocked,
    ScaffoldError,
    SourceError,
    TimerError,
)


def _github_output(values: dict[str, Any]) -> None:
    """Expose step outputs to a GitHub Actions workflow when it asked for them."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.writelines(f"{name}={value}\n" for name, value in values.items())


def _collect(args: argparse.Namespace) -> dict[str, Any]:
    summary = collect.run(args.repo)
    budget = summary.get("budget")
    if budget and budget["reached"] and os.environ.get("GITHUB_ACTIONS") == "true":
        print(
            f"::warning::Monthly budget reached: ${budget['spent_usd']:.2f} of ${budget['usd_per_month']:.2f} spent in "
            f"{budget['month']} (Claude Code's list-price estimate). Judging is paused until {budget['resumes']}; "
            "postings stay queued.",
            file=sys.stderr,
        )
    if args.github_output:
        _github_output(
            {
                "judge_needed": str(summary["judge_needed"]).lower(),
                "seal": summary["seal"],
                "triage_packets": summary["triage_packets"],
            }
        )
    return summary


def _advance(args: argparse.Namespace) -> dict[str, Any]:
    summary = advance.run(args.repo, args.seal)
    if args.github_output:
        _github_output({"assess_packets": summary["assess_packets"], "seal": summary["seal"]})
    return summary


def _apply(args: argparse.Namespace) -> dict[str, Any]:
    return apply.run(args.repo, args.seal)


def _publish(args: argparse.Namespace) -> dict[str, Any]:
    result = publish.run(args.repo, push=not args.no_push, dry_run=args.dry_run)
    if result["state"] == "stale_base" and os.environ.get("GITHUB_ACTIONS") == "true":
        # Not a failure: nothing is lost and the next run redoes the work. It must not pass unseen either.
        print(
            f"::warning::Nothing was pushed: {result['detail']}. The next run judges the same postings again.",
            file=sys.stderr,
        )
    return result


def _audit(args: argparse.Namespace) -> dict[str, Any]:
    problems = audit.check(args.repo)
    if problems:
        raise PublishBlocked("; ".join(problems))
    notes = audit.warnings(args.repo)
    return {"audit": "passed", **({"warnings": notes} if notes else {})}


def _status(args: argparse.Namespace) -> dict[str, Any]:
    layout = Layout(args.repo)
    meta = read_json(layout.feed_meta, {})
    # The types the feed's own readers provide, and the files not used and why. Loading them runs
    # their code, as `sources test` does; nothing the judging step runs ever loads them.
    readers = load_own(layout.root)
    return {
        "engine": scaffold.verify(layout.root),
        "sources_configured": len(read_json(layout.sources, [])),
        **({"readers": readers.report()} if readers.found else {}),
        "as_of": meta.get("as_of"),
        "counts": meta.get("counts"),
        "triage_check": meta.get("canary"),
        "failing_sources": (meta.get("sources") or {}).get("failing"),
        "judging_failed": meta.get("judging_failed"),
        "usage": meta.get("usage"),
        "schedule": scaffold.schedule(layout.root),
        "timer": timer.summary(layout.root),
    }


def _sources(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "list":
        return registry.listing()
    if args.action == "add":
        return registry.add(args.repo, args.items)
    if args.action == "remove":
        return registry.remove(args.repo, args.items)
    if args.action == "probe":
        return registry.probe(" ".join(args.items))
    if args.action == "hosts":
        return registry.hosts(args.repo)
    return registry.test(args.repo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter", description="automated-job-hunter engine")
    parser.add_argument("--repo", default=".", help="feed repo root (default: current directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    step = commands.add_parser("collect", help="read every source and propose the next state")
    step.add_argument("--github-output", action="store_true", help="also write step outputs for GitHub Actions")
    step.set_defaults(run=_collect)
    step = commands.add_parser("advance", help="fold in triage results and prepare assessment packets")
    step.add_argument("--seal", help="the seal the previous step printed; defaults to the one on disk")
    step.add_argument("--github-output", action="store_true", help="also write step outputs for GitHub Actions")
    step.set_defaults(run=_advance)
    step = commands.add_parser("judge", help="run the current judging stage headless with Claude Code, in batches")
    step.set_defaults(run=lambda a: judge.run(a.repo))
    step = commands.add_parser("apply", help="fold in assessments, update the feed and adopt the new state")
    step.add_argument("--seal", help="the seal the previous step printed; defaults to the one on disk")
    step.set_defaults(run=_apply)
    step = commands.add_parser("publish", help="audit, commit the feed and state, and push")
    step.add_argument("--dry-run", action="store_true", help="report what would be committed and change nothing")
    step.add_argument("--no-push", action="store_true", help="commit locally only")
    step.set_defaults(run=_publish)
    step = commands.add_parser("audit", help="check the profile and config for contact details and credentials")
    step.set_defaults(run=_audit)
    step = commands.add_parser(
        "requeue", help="ask for postings to be judged again: parked ones, or every assessed one"
    )
    which = step.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--parked", action="store_const", const="parked", dest="what", help="send parked postings to triage"
    )
    which.add_argument(
        "--assessed", action="store_const", const="assessed", dest="what", help="assess every assessed posting again"
    )
    step.set_defaults(run=lambda a: requeue.run(a.repo, a.what))
    step = commands.add_parser("decide", help="record that the person applied to or declined a posting")
    step.add_argument("decision", choices=decisions.KINDS)
    step.add_argument("key", help="the posting's key, <source>:<id>, as in positions.jsonl")
    step.add_argument("--date", help="YYYY-MM-DD (default: today, UTC)")
    step.set_defaults(run=lambda a: decisions.record(a.repo, a.decision, a.key, a.date))
    step = commands.add_parser("tick", help="one whole tick on this machine, judging with headless Claude Code")
    step.add_argument("--no-push", action="store_true", help="commit locally only")
    step.set_defaults(run=lambda a: tick.run(a.repo, push=not a.no_push))
    step = commands.add_parser(
        "status", help="what is published, what is waiting, and whether the engine copy is intact"
    )
    step.set_defaults(run=_status)
    step = commands.add_parser("sources", help="choose which job boards this feed watches")
    step.add_argument("action", choices=["list", "add", "remove", "probe", "hosts", "test"])
    step.add_argument(
        "items",
        nargs="*",
        help="registry ids, `ats:slug:Company`, `smartrecruiters:<company>:Company` or `workday:<host>/<site>:Company` "
        "entries, or what to probe: "
        "a company name, or a careers page URL followed by the company name",
    )
    step.set_defaults(run=_sources)
    step = commands.add_parser("init", help="create a feed repo at --repo (run from the installed skill)")
    step.add_argument(
        "--cadence", default="4h", help="when the workflow runs: 1h, 4h or 8h, or UTC hours such as 6,10,14,20"
    )
    step.set_defaults(run=lambda a: scaffold.init(a.repo, a.cadence))
    step = commands.add_parser("upgrade", help="replace the feed repo's engine copy with this version")
    step.add_argument(
        "--cadence", help="also change when the workflow runs: 1h, 4h, 8h, or UTC hours such as 6,10,14,20"
    )
    step.set_defaults(run=lambda a: scaffold.upgrade(a.repo, a.cadence))
    step = commands.add_parser(
        "timer", help="start the workflow from this Mac at the cron line's times (launchd); show, install or remove"
    )
    step.add_argument("action", nargs="?", default="show", choices=["show", "install", "remove"])
    step.set_defaults(run=lambda a: getattr(timer, a.action)(a.repo))
    args = parser.parse_args(argv)
    try:
        result = args.run(args)
    except HANDLED as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error), "needs": "a person"}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    # A failed push fails the run, and so does a judging stage that got no answer at all. A moved remote
    # does not, nor does a stage with some answers: its missing packets are issued again next time.
    return 1 if result.get("state") == "push_failed" or result.get("judging_failed") else 0


if __name__ == "__main__":
    sys.exit(main())
