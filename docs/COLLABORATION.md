# How work is reported and fixed

Problems are tracked as GitHub issues and fixed through pull requests, so a person can follow every
change from the report to the merge. People and agents use the same formats.

## Titles

Issues and pull requests both use `[type] [area] Short description`.

| | Types | Example |
|---|---|---|
| Issue | `bug`, `feature`, `refactor`, `docs`, `question` | `[bug] [setup] init refuses a folder that only contains .DS_Store` |
| Pull request | `fix`, `feature`, `refactor`, `docs`, `test`, `chore` | `[fix] [setup] Accept a folder that only contains .DS_Store (fixes #12)` |

Areas: `install`, `setup`, `sources`, `run`, `engine`, `feed`, `publish`, `workflow`, `skill`, `docs`.

The description is a plain sentence in the imperative for pull requests ("Accept ...", "Retry
..."), and a statement of the problem for issues. A pull request that resolves issues ends its
title with `(fixes #N)`, and its body says `Fixes #N` so GitHub closes the issue on merge.

## Issues

- One problem per issue. Search open issues first.
- Use the sections of the templates in `.github/ISSUE_TEMPLATE/`: what happened, expected, steps
  to reproduce, output, environment, reported by.
- **This repository is public.** Never include names, contact details, résumé or profile text,
  employers or schools taken from a résumé, or anything from a private feed repo. Describe the
  problem in general terms and remove personal details from pasted output.

## Pull requests

- Branch names: `<type>/<issue>-<short-slug>`, for example `fix/12-init-ds-store`.
- One concern per pull request. Use the sections of `.github/pull_request_template.md`.
- Before opening: the full test suite on Python 3.13 and 3.9, `ruff format --check` and
  `ruff check`, and a check of the specific failure where one is possible.
- A person merges. Whoever verified a fix comments on the pull request, starting with
  `Verified on <branch>:` or `Still failing on <branch>:`.

## Agents working together

Two Claude Code sessions on different machines, logged in to the same account and connected through
Remote Control, coordinate by message and record everything here on GitHub:

| Session | Role |
|---|---|
| `jobhunter-test1` | Installs the plugin, runs it on real inputs, and files issues |
| `jobhunter-main` | Reproduces each issue, fixes it on a branch, and opens a pull request |

1. The tester files an issue, then messages `jobhunter-main` with a first line of
   `Issue #N opened: <title>`.
2. The fixer replies on the issue if more information is needed, otherwise opens a pull request
   and messages the tester the branch name.
3. The tester tries the branch (the pull request template says how) and comments the result on
   the pull request.
4. A person reviews and merges.

Messages carry only short notices. Details, logs and decisions go on the issue or pull request, so
the record stays complete without access to either session.
