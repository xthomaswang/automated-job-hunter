# Project conventions

automated-job-hunter keeps a private job-feed repo up to date. A deterministic engine fetches,
diffs, renders and publishes; an agent's cheap subagents judge fit. This file is for contributors
changing the code. The design is in [docs/DESIGN.md](docs/DESIGN.md).

- The engine (`skills/automated-job-hunter/scripts/ajh/`) uses only the Python standard library and
  must run on Python 3.9. Start every module with `from __future__ import annotations`.
- The engine never calls a model API. Models only read packets and only write result files.
- A model never produces or edits a URL and never touches git. Published quotes are spans of the
  source posting, validated as verbatim substrings.
- Failed or unvalidated model output never becomes a fact. It is re-queued with feedback.
- Adapters refuse partial snapshots: raise rather than return an incomplete board, so an outage
  cannot close live jobs. The one exception is a row that is plainly no posting a person could
  open, such as a Workday row with neither a link nor a title: skip it, count it with
  `sources/skips.py`, and raise when such rows are more than a few.
- Unknown is not a negative. Stage 0 drops only on positive evidence; missing years are unknown,
  not zero; preferred is not required.
- Sponsorship and citizenship flags come from fixed rules over the posting text, preferring a missed
  flag to a wrong one.
- Tracked feed files carry no per-run timestamps. A tick with unchanged inputs must leave tracked
  files byte-identical.
- This repo is public. No résumé, applicant data, credentials or private job-search notes in code,
  fixtures or docs. Fixtures are synthetic or public posting facts.
- Dev tooling: `uv sync`, then `uv run pytest`. Format and lint with `uv run ruff format .` and
  `uv run ruff check .` before committing. The runtime needs nothing installed.
- Spike and benchmark notes record the macOS build, Xcode version and Claude Code version.
