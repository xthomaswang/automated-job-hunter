# automated-job-hunter

An installable skill for Claude Code (Codex-compatible) that keeps a private GitHub repo updated
with job postings matched to your experience. A deterministic engine fetches public job boards and
publishes the feed; the agent's cheap subagents judge fit.

**Status: in development. Not installable yet.** The engine can read live job boards, apply hard
constraints and track what is new, changed or closed (`hunter.py collect`). Judging, publishing and
setup are not built yet; see the phases in the design doc.

- Design: [docs/DESIGN.md](docs/DESIGN.md)
- Contributor rules: [AGENTS.md](AGENTS.md)

## Development

```sh
uv sync
uv run pytest
```
