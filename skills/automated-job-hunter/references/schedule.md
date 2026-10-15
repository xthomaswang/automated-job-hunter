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

If it is still empty after two scheduled times, see "If scheduled runs never start" below.

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

### If scheduled runs never start

Runs started by hand or by a push work, but `--event schedule` is still empty after two scheduled
times. First rule out the feed repo's side:

- `gh workflow list --all --repo <owner>/<repo>` lists `job-hunter` as `active`. If it is
  disabled, `gh workflow enable job-hunter.yml --repo <owner>/<repo>`.
- The workflow is on the default branch, the only branch GitHub reads schedules from.

If both hold, GitHub's scheduler is not starting runs for this repo. In the case this section was
written from ([issue #41](https://github.com/xthomaswang/automated-job-hunter/issues/41)), nothing
tried in the feed repo helped: not committing under the owner's account, not changing the cron line,
not pushing, and not a new workflow with nothing but a schedule.
[GitHub staff have said](https://github.com/orgs/community/discussions/185355) that a commit pushed
to the default branch makes GitHub read the schedules again; there, it did not help. Start the runs
from somewhere else instead: see "Alternatives" below. The first two keep the runs on GitHub; on a
Mac, the first is `timer install`.

### Who GitHub ties the schedule to

GitHub sends notifications for scheduled workflows to "the user who last modified the cron syntax in
the workflow file". When a commit that changes the cron line reactivates a disabled schedule, that
commit's user also becomes the actor of its runs. `hunter status` shows the cron line, the commit
that last changed it and that commit's addresses under `schedule`. A `warning` there means git made
the address up from the machine's name, so no account has it. Such an address is described rather
than printed, since it holds the machine's user and host names. For any other address, GitHub's
answer is `gh api repos/<owner>/<repo>/commits/<sha> --jq .author.login`: `null` means no account
has it.

To tie the schedule to the user's account, set their GitHub identity in the feed repo (step 5 of
[setup.md](setup.md)). Then write the cron line anew, which gives it a new minute, commit and push:

```sh
python3 <this skill>/scripts/hunter.py --repo <feed repo> upgrade --cadence <their cadence>
git -C <feed repo> commit -am "Change the schedule from my account" && git -C <feed repo> push
```

GitHub's documentation speaks of the cron syntax, so change the cron line itself, not just other
parts of the file. This does not make GitHub start scheduled runs it was not starting.

## Alternatives

**Start the workflow from a Mac.** On a Mac where `gh` is logged in and that is usually on, a timer
can run `gh workflow run job-hunter.yml --repo <owner>/<repo>` at the times of the cron line. The
runs then happen on GitHub as before, with the repo's secrets; only the start comes from the Mac.
From this skill's copy, show the user what it would do:

```sh
scripts/hunter.py --repo <feed repo> timer
```

`starts` are the times on the Mac's own clock. Every start that finds postings waiting spends their
usage, so ask before installing it:

```sh
scripts/hunter.py --repo <feed repo> timer install
```

The timer is a launchd agent, so it needs no new secret and keeps working after a restart once the
user logs in. `install` first checks that `gh`, run the way launchd will run it, can see the
workflow, and that the workflow is enabled. A start while a run is going waits for it, and a newer
start replaces one already waiting, so starts never pile up. While the Mac sleeps, nothing starts;
when it wakes, launchd starts the workflow once if a time went by. A failed start is tried again
for about five minutes, since the network can come back a little after the Mac wakes. The timer
keeps its times on the Mac's clock when the clocks change. Install it on one Mac only, or every run
starts twice.

- `timer` shows when it starts and how the last start went; `hunter status` shows the same under
  `timer`. A `warning` there says what to fix.
- After `upgrade --cadence`, run `timer install` again; the timer keeps the times it was made with.
- `timer remove` takes it away and keeps its log. If `gh run list --event schedule` shows GitHub
  starting scheduled runs again, remove it; until then, a slot that both start runs a second time,
  which mostly finds nothing new.

**Start the workflow from elsewhere.** Any other scheduler can run the same command, on a machine
that is on and where `gh` is logged in; cron skips a start that falls while the machine sleeps. An
external scheduler that calls GitHub's API instead needs a token stored with that service: a
fine-grained token for the feed repo alone, with the Actions permission set to read and write.

**Claude Code Routine.** A cloud routine on the feed repo with the prompt "Read .hunter/RUNBOOK.md
and run one tick. Push to main." Two things must be set by the user: the routine's environment has
to allow the job-board hosts (`hunter sources hosts` lists them), and the minimum interval is one
hour. The routine session runs the engine itself, so the tight tool limits of the headless judging
stage do not apply there.

**Run the whole update on their own machine.** Run `python3 .hunter/engine/hunter.py tick` from the
feed repo on a schedule (cron, launchd, systemd). The judging then happens on that machine, with
its Claude Code login. Nothing to configure in the cloud, but it stops whenever the machine sleeps.
