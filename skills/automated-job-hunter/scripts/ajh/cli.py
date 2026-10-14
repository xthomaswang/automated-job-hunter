"""Command line for the engine. Every command prints one JSON object.

Exit status: 0 done, 1 the push failed, 2 a person has to fix something (the JSON says what).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import advance, apply, audit, collect, judge, publish, registry, scaffold, tick
from .advance import WorkError
from .collect import ConfigError
from .criteria import CriteriaError
from .layout import Layout
from .model import SourceError
from .packets import ProfileTooLong
from .publish import PublishBlocked
from .scaffold import ScaffoldError
from .state import StateError, read_json

# Problems a person has to fix. Each is reported as JSON with exit status 2.
HANDLED = (
    CriteriaError,
    ConfigError,
    StateError,
    WorkError,
    ProfileTooLong,
    PublishBlocked,
    ScaffoldError,
    SourceError,
)


def _github_output(values: dict[str, Any]) -> None:
    """Expose step outputs to a GitHub Actions workflow when it asked for them."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.writelines(f"{name}={value}\n" for name, value in values.items())


def _collect(args: argparse.Namespace) -> dict[str, Any]:
    summary = collect.run(args.repo)
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
    return publish.run(args.repo, push=not args.no_push, dry_run=args.dry_run)


def _audit(args: argparse.Namespace) -> dict[str, Any]:
    problems = audit.check(args.repo)
    if problems:
        raise PublishBlocked("; ".join(problems))
    return {"audit": "passed"}


def _status(args: argparse.Namespace) -> dict[str, Any]:
    layout = Layout(args.repo)
    meta = read_json(layout.feed_meta, {})
    return {
        "engine": scaffold.verify(layout.root),
        "sources_configured": len(read_json(layout.sources, [])),
        "as_of": meta.get("as_of"),
        "counts": meta.get("counts"),
        "triage_check": meta.get("canary"),
        "failing_sources": (meta.get("sources") or {}).get("failing"),
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
    step = commands.add_parser("tick", help="one whole tick on this machine, judging with headless Claude Code")
    step.add_argument("--no-push", action="store_true", help="commit locally only")
    step.set_defaults(run=lambda a: tick.run(a.repo, push=not a.no_push))
    step = commands.add_parser(
        "status", help="what is published, what is waiting, and whether the engine copy is intact"
    )
    step.set_defaults(run=_status)
    step = commands.add_parser("sources", help="choose which job boards this feed watches")
    step.add_argument("action", choices=["list", "add", "remove", "probe", "hosts", "test"])
    step.add_argument("items", nargs="*", help="registry ids, `ats:slug:Company` entries, or a company name to probe")
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
    args = parser.parse_args(argv)
    try:
        result = args.run(args)
    except HANDLED as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error), "needs": "a person"}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("state") == "push_failed" else 0  # a failed push fails the run; a moved remote does not


if __name__ == "__main__":
    sys.exit(main())
