#!/bin/sh
# Run one judging stage headless with Claude Code. Run from the feed repo root.
#
# The engine splits .hunter/work/manifest.md into batches of at most 20 packets and starts one
# orchestrator per batch. Each has three tools (Agent, Read, Write), is confined to this directory,
# and can write only under .hunter/work/results/: no shell, no network tools, no access to .git.
# Afterwards the engine checks that every packet has a result, tries once more for any that do not,
# and prints what it counted. Flags verified in docs/spikes.md (E2) of automated-job-hunter.
set -eu
exec python3 "$(dirname "$0")/hunter.py" judge "$@"
