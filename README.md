# automated-job-hunter

An installable skill for Claude Code that keeps a private GitHub repo updated with job postings
matched to your experience. A deterministic engine fetches public job boards and
publishes the feed; the agent's cheap subagents judge fit.

**Status: in testing.** Tested end to end: installing from GitHub, setting up a feed, updates on one
machine on a Claude subscription, and scheduled GitHub Actions runs billed to an API key. The
workflow also accepts a subscription token, but no Actions run has used one yet. The skill file is
written to be portable to Codex, but nothing has been run there yet. Please
[open an issue](https://github.com/xthomaswang/automated-job-hunter/issues) for anything that breaks.

## Install

In Claude Code:

```
/plugin install automated-job-hunter --marketplace xthomaswang/automated-job-hunter
```

Or the same from a normal shell:

```sh
claude plugin install automated-job-hunter --marketplace xthomaswang/automated-job-hunter
```

On a version that does not accept that one-step form, add the marketplace first:

```
/plugin marketplace add xthomaswang/automated-job-hunter
/plugin install automated-job-hunter@automated-job-hunter
```

Then ask Claude Code to "set up my job feed". You need Python 3.9 or newer and `git`. To put your
feed on GitHub you also need the [GitHub CLI](https://cli.github.com) (on macOS, the package
installer on its [releases page](https://github.com/cli/cli/releases) needs no Homebrew), logged in
with the `workflow` scope (`gh auth refresh -s workflow`), because the feed repo contains a workflow
file.

## What it does

1. `setup` reads your résumé, asks what you want, and writes a contact-free profile and search
   criteria into a new private feed repo, together with a pinned copy of the engine.
2. Every update, the engine reads the job boards you chose, drops postings at companies you left
   out or outside the employment types and countries you want, and hands the rest to subagents: a
   cheap one screens each posting on its title and a snippet, a stronger one assesses the survivors
   against your profile.
3. The engine checks everything the models wrote (every quote must be in the posting), updates
   `README.md`, `positions.jsonl` and one brief per posting, and commits.
4. A GitHub Actions workflow in the feed repo runs the updates on a schedule: every 1, 4 or 8
   hours, or at set times of day. It needs one repository secret, `CLAUDE_CODE_OAUTH_TOKEN` (your
   Claude subscription) or `ANTHROPIC_API_KEY` (billed per use; a key created inside a workspace).

## What you can steer

- **What it looks for**: roles you want, related roles, roles to avoid, seniority, employment type,
  countries, visa sponsorship, companies to prefer or leave out, and the profile text itself. The
  skill says what each change judges again, and what it costs. Changing the profile text, the roles,
  the seniority, the countries, the employment types or the visa need assesses every assessed
  posting again: about $2 for every hundred postings already assessed, spread over the next
  updates.
- **Hard requirements**: leave out postings that require more years than you have, are more senior
  than you want, or require a degree, current enrollment, a credential or a license you do not
  have; and set a lowest fit for the roles you want. These are applied to each posting's
  assessment, so a posting is still assessed once, but changing them assesses nothing again.
- **Your decisions**: in the feed repo, `python3 .hunter/engine/hunter.py decide applied <key>`
  marks a posting "✅ applied" in the feed and stops judging it, and `... decide declined <key>`
  takes it out for good. Commit and push `profile/decisions.jsonl` afterwards. An agent that
  applies for you should record its decisions the same way.
- **Cost**: every update that judged is logged with its tokens and Claude Code's list-price
  estimate, in `feed/usage.jsonl` and the feed's README. A monthly budget
  (`caps.max_usd_per_month` in `profile/criteria.json`) pauses judging once it is reached.

A model never touches git or a link, never sees a credential, and in a scheduled run has no shell
and no network: it can read packets and write result files, nothing else.

## Run from a checkout instead

```sh
git clone https://github.com/xthomaswang/automated-job-hunter && cd automated-job-hunter
claude --plugin-dir .
```

Measured cost at API prices: about $0.14 per 1,000 postings screened and $0.02 per posting
assessed. An update working through a backlog at the default caps costs about $1.7 to $2; one that
finds nothing new uses no model at all. See [docs/spikes.md](docs/spikes.md).

- What changed in each version: [CHANGELOG.md](CHANGELOG.md)
- Design: [docs/DESIGN.md](docs/DESIGN.md)
- Measurements: [docs/spikes.md](docs/spikes.md)
- Contributor rules: [AGENTS.md](AGENTS.md)
- Reporting problems and proposing changes: [docs/COLLABORATION.md](docs/COLLABORATION.md)

## Development

```sh
uv sync
uv run pytest
```
