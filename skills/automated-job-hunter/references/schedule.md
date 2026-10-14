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

A run started by hand says nothing about the schedule. Once the first scheduled time has passed
(allow up to an hour), check that GitHub started one by itself:

```sh
gh run list --repo <owner>/<repo> --event schedule --limit 3
```

### If scheduled runs never start

Runs started by hand or by a push work, but `--event schedule` stays empty after two scheduled
times. GitHub runs a schedule as the account behind the commit that last changed the cron line, and
never runs it when no account has that commit's email. That happens when git had no identity on the
machine and made an address up from its name, such as `user@laptop.local`. `hunter status` shows
that commit and its addresses under `schedule`. A made-up address is described, not printed, since
it holds the machine's user and host names, and comes with a `warning`. For any other address,
GitHub's answer is `gh api repos/<owner>/<repo>/commits/<sha> --jq .author.login`: `null`
means no account has it.

To fix it, set the user's GitHub identity in the feed repo (step 5 of [setup.md](setup.md)). Then
write the cron line anew, which gives it a new minute, commit and push:

```sh
python3 <this skill>/scripts/hunter.py --repo <feed repo> upgrade --cadence <their cadence>
git -C <feed repo> commit -am "Run the schedule from my account" && git -C <feed repo> push
```

Only a change to the cron line itself counts; other edits to the workflow file do not. The next
scheduled time then starts a run.

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
