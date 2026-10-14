"""Command line for the engine. Every command prints one JSON object.

Exit status: 0 done, 2 a person has to fix something (the JSON says what).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import collect
from .collect import ConfigError
from .criteria import CriteriaError
from .state import StateError


def _github_output(values: dict[str, Any]) -> None:
    """Expose step outputs to a GitHub Actions workflow when it asked for them."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.writelines(f"{name}={value}\n" for name, value in values.items())


def _collect(args: argparse.Namespace) -> dict[str, Any]:
    summary = collect.run(args.repo)
    if args.github_output:
        _github_output({"judge_needed": str(summary["judge_needed"]).lower(), "seal": summary["seal"],
                        "triage_queued": summary["queued"]["triage"], "assess_queued": summary["queued"]["assess"]})
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter", description="automated-job-hunter engine")
    parser.add_argument("--repo", default=".", help="feed repo root (default: current directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    step = commands.add_parser("collect", help="read every source and propose the next state")
    step.add_argument("--github-output", action="store_true", help="also write step outputs for GitHub Actions")
    step.set_defaults(run=_collect)
    args = parser.parse_args(argv)
    try:
        result = args.run(args)
    except (CriteriaError, ConfigError, StateError) as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error), "needs": "a person"}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
