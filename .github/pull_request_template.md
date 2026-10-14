<!-- Title: [type] [area] Short description (fixes #N). See docs/COLLABORATION.md. -->

## Summary

## Screenshots

<!-- Before and after, or a diagram, for anything a person can see: README, briefs, command output,
rendered docs. Test data only; see docs/COLLABORATION.md. Delete this section for changes nothing
shows. -->

## Why

Fixes #

## Changes

-

## How it was tested

- [ ] `uv run pytest` on Python 3.13
- [ ] `uv run --isolated --python 3.9 pytest`
- [ ] `uv run ruff format --check .` and `uv run ruff check .`
- [ ] Manual or live check:

## How to try this branch

```sh
git clone --branch <branch> https://github.com/xthomaswang/automated-job-hunter /tmp/ajh-<branch>
python3 /tmp/ajh-<branch>/skills/automated-job-hunter/scripts/hunter.py --repo <your feed repo> upgrade
```

## Notes for reviewers
