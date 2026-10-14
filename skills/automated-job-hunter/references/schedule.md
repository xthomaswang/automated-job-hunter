# Making it update by itself

## GitHub Actions (the default)

This needs the feed repo on GitHub and the GitHub CLI logged in with the `workflow` scope; step 7 of
[setup.md](setup.md) says how to install it and log in.

The feed repo already contains `.github/workflows/job-hunter.yml`. Once the repo is on GitHub the
workflow runs on its schedule, and also whenever `profile/` or `config/` changes on `main`. Most
runs only read the boards and use no model. The judging steps start only when something is waiting.

The workflow needs one secret so its judging steps can use Claude. The token must not pass through
you. Give the user these two commands to run in their own terminal:

```sh
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo <owner>/<repo>
```

The first prints a long-lived token for their Claude subscription; the second asks for it and
stores it as a repository secret. To bill an API key instead, they set an `ANTHROPIC_API_KEY`
secret (`gh secret set ANTHROPIC_API_KEY --repo <owner>/<repo>`) holding a key created inside a
workspace in the Claude Console; the API rejects a key that is not scoped to a workspace with
`400 This API key is not scoped to a workspace`. Either secret works without editing the workflow;
if both are set, the API key is used. If neither is set, or the token has expired, the judging
steps fail, whatever else the run did still lands, and the run is marked failed. GitHub then sends
its failed-run notification; for scheduled runs it goes to whoever last changed the workflow's
schedule, normally the repository owner.

Then start one run and watch it:

```sh
gh workflow run job-hunter.yml --repo <owner>/<repo>
gh run watch --repo <owner>/<repo>
```

Things to tell the user:

- GitHub's schedule is best effort: a run can start late, and under heavy load be skipped.
- What judging costs is in the feed: the README's Usage section and `feed/usage.jsonl`. With an API
  key, a monthly budget (`caps.max_usd_per_month`, see [setup.md](setup.md)) pauses judging once it
  is reached; the run then shows a warning, not a failure.
- Private repositories have a monthly allowance of Actions minutes. A run that only reads the
  boards takes about a minute; one that judges takes a few. Every 4 hours fits comfortably; every
  hour uses most of a free allowance.
- To pause it: `gh workflow disable job-hunter.yml`.

### When it runs

The schedule is the one `cron:` line in `.github/workflows/job-hunter.yml`. GitHub reads it in UTC
and has no time zones. To change it, run one of these from this skill's copy, then commit and push:

- Every 1, 4 or 8 hours: `scripts/hunter.py --repo <feed repo> upgrade --cadence 4h` (or `1h`, `8h`).
- At set times of day: convert the user's local times to UTC hours and pass the list, for example
  `upgrade --cadence 6,10,14,20` for 08:00, 12:00, 16:00 and 22:00 at UTC+2. The engine adds a
  random minute between :03 and :20, so runs start at, say, 06:14, 10:14, 14:14 and 20:14 UTC: the
  top of the hour is when GitHub is busiest. GitHub may also start a run some minutes late. `init`
  takes the same `--cadence` values.

Where clocks change for daylight saving, the local times move by an hour when the clocks do. To keep
the same local times, run `upgrade --cadence` again with the new UTC hours after each change.

The line may also be edited by hand. `upgrade` keeps the first `cron:` line exactly as written (only
`--cadence` replaces it), so keep the whole schedule on that one line and off the top of the hour.

## Alternatives

**Claude Code Routine.** A cloud routine on the feed repo with the prompt "Read .hunter/RUNBOOK.md
and run one tick. Push to main." Two things must be set by the user: the routine's environment has
to allow the job-board hosts (`hunter sources hosts` lists them), and the minimum interval is one
hour. The routine session runs the engine itself, so the tight tool limits of the headless judging
stage do not apply there.

**A timer on their own machine.** Run `python3 .hunter/engine/hunter.py tick` from the feed repo on
a schedule (cron, launchd, systemd). Nothing to configure in the cloud, but it stops whenever the
machine sleeps.
