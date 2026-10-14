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
stores it as a repository secret. (To bill an API key instead, they set `ANTHROPIC_API_KEY` and
change the two `env:` lines in the workflow as its comment says.)

Then start one run and watch it:

```sh
gh workflow run job-hunter.yml --repo <owner>/<repo>
gh run watch --repo <owner>/<repo>
```

Things to tell the user:

- GitHub's schedule is best effort: a run can start late, and under heavy load be skipped.
- Private repositories have a monthly allowance of Actions minutes. A run that only reads the
  boards takes about a minute; one that judges takes a few. Every 4 hours fits comfortably; every
  hour uses most of a free allowance.
- To change how often it runs: `scripts/hunter.py --repo <feed repo> upgrade --cadence 1h` (or `4h`,
  `8h`) from this skill's copy, then commit and push.
- To pause it: `gh workflow disable job-hunter.yml`.

## Alternatives

**Claude Code Routine.** A cloud routine on the feed repo with the prompt "Read .hunter/RUNBOOK.md
and run one tick. Push to main." Two things must be set by the user: the routine's environment has
to allow the job-board hosts (`hunter sources hosts` lists them), and the minimum interval is one
hour. The routine session runs the engine itself, so the tight tool limits of the headless judging
stage do not apply there.

**A timer on their own machine.** Run `python3 .hunter/engine/hunter.py tick` from the feed repo on
a schedule (cron, launchd, systemd). Nothing to configure in the cloud, but it stops whenever the
machine sleeps.
