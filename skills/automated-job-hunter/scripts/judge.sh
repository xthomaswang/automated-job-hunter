#!/bin/sh
# Run one judging stage headless with Claude Code. Run from the feed repo root.
#
# The orchestrator reads the manifest and hands each packet to its subagent. The whole stage has
# three tools (Agent, Read, Write), is confined to this directory, and can write only under
# .hunter/work/results/. It has no shell, no network tools and no access to .git.
# Flags verified in docs/spikes.md (E2) of the automated-job-hunter repository.
set -eu

PROMPT='You are the orchestrator of one judging stage. Read .hunter/work/manifest.md. Each row after the table header is: packet | packet file | result file | agent. For every row, start the named subagent with exactly this instruction: "Process packet <packet file>. Follow the instructions inside it. Write the result to <result file>. Reply with one line: <packet> done." Start the subagents in parallel, at most 20 per message. When a group has finished, start the next group, until every row has been handled. Do not read the packet files yourself and do not write any file yourself. If a subagent fails, do not retry it. When every row has been handled, reply with exactly one line: <number that reported done> of <number of rows> done. If the manifest has no rows, reply: 0 of 0 done.'

exec claude -p "$PROMPT" \
  --model "${HUNTER_ORCHESTRATOR_MODEL:-haiku}" \
  --restricted --tools "Agent,Read,Write" --agents .hunter/agents.json --strict-mcp-config \
  --permission-mode dontAsk \
  --allowedTools "Agent" "Edit(./.hunter/work/results/**)" \
  --disallowedTools "Read(./.git/**)" \
  --output-format json --no-session-persistence < /dev/null
