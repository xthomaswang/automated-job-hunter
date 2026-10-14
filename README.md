# automated-job-hunter

An installable skill for Claude Code that keeps a private GitHub repo updated with job postings
matched to your experience. A deterministic engine fetches public job boards and
publishes the feed; the agent's cheap subagents judge fit.

**Status: works locally, not yet published.** A feed repo can be created, updated end to end with
real subagents, and committed. The scheduled GitHub Actions run is written but has not been tested
on a runner yet. The skill file is written to be portable to Codex, but nothing has been run there.

## What it does

1. `setup` reads your résumé, asks what you want, and writes a contact-free profile and search
   criteria into a new private feed repo, together with a pinned copy of the engine.
2. Every update, the engine reads the job boards you chose, drops what breaks your hard limits, and
   hands the rest to subagents: a cheap one screens each posting on its title and a snippet, a
   stronger one assesses the survivors against your profile.
3. The engine checks everything the models wrote (every quote must be in the posting), updates
   `README.md`, `positions.jsonl` and one brief per posting, and commits.

A model never touches git or a link, never sees a credential, and in a scheduled run has no shell
and no network: it can read packets and write result files, nothing else.

## Try it from a checkout

```sh
git clone <this repository> && cd automated-job-hunter
claude --plugin-dir .
```

Then ask Claude Code to "set up my job feed". Needs Python 3.9 or newer, `git`, and for the first
push `gh`.

Measured cost at API prices: about $0.14 per 1,000 postings screened and $0.02 per posting
assessed; an update that finds nothing new uses no model at all. See [docs/spikes.md](docs/spikes.md).

- Design: [docs/DESIGN.md](docs/DESIGN.md)
- Measurements: [docs/spikes.md](docs/spikes.md)
- Contributor rules: [AGENTS.md](AGENTS.md)

## Development

```sh
uv sync
uv run pytest
```
