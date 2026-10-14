# automated-job-hunter

An installable skill for Claude Code that keeps a private GitHub repo updated with job postings
matched to your experience. A deterministic engine fetches public job boards and
publishes the feed; the agent's cheap subagents judge fit.

**Status: early.** A feed repo can be created, updated end to end with real subagents, and committed
on one machine. Installing from GitHub and the scheduled GitHub Actions run have not been tested
yet. The skill file is written to be portable to Codex, but nothing has been run there. Please
[open an issue](https://github.com/xthomaswang/automated-job-hunter/issues) for anything that breaks.

## Install

In Claude Code:

```
/plugin install automated-job-hunter --marketplace xthomaswang/automated-job-hunter
```

On a version that does not accept that one-step form, add the marketplace first:

```
/plugin marketplace add xthomaswang/automated-job-hunter
/plugin install automated-job-hunter@automated-job-hunter
```

Then ask Claude Code to "set up my job feed". You need Python 3.9 or newer and `git`. To put your
feed on GitHub you also need `gh`, logged in with the `workflow` scope
(`gh auth refresh -s workflow`), because the feed repo contains a workflow file.

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

## Run from a checkout instead

```sh
git clone https://github.com/xthomaswang/automated-job-hunter && cd automated-job-hunter
claude --plugin-dir .
```

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
