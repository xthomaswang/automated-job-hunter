# When something goes wrong

Start with `python3 .hunter/engine/hunter.py status` in the feed repo, then the "When something is
wrong" section of `.hunter/RUNBOOK.md`, which explains each error the engine reports.

| Symptom | Likely cause | What to do |
|---|---|---|
| A command exits with status 2 | A person has to fix something | Read `detail` in the JSON it printed; it names the file and the problem |
| `WorkError` from `advance` or `apply` | The tree has uncommitted changes, or the scratch folder changed | Commit or discard the changes, then start again from `collect` |
| Nothing new for days | Sources failing, the schedule disabled, or simply nothing new | `status` for failing sources; `gh run list` for the schedule; `feed/meta.json` for `as_of` |
| Runs by hand or on a push work, but `gh run list --event schedule` stays empty | The commit that last changed the cron line carries an email no GitHub account has, often one git made up from the machine's name, so GitHub has no account to run the schedule as | `schedule` in `status` names that commit; set the user's identity and write the cron line anew, as in "If scheduled runs never start" in [schedule.md](schedule.md) |
| `git commit` in the feed repo fails with "no email was given and auto-detection is disabled" | `init` or `upgrade` set `user.useConfigOnly` in the feed repo, so git does not make up an identity, and none is set | Set the user's GitHub identity in the feed repo (step 5 of [setup.md](setup.md)), then commit again |
| A scheduled run is marked failed at "Report a failed judging step" | A judging step failed, most often because no Claude secret is set or the token expired | Read the judging step's log; for credentials, have the user rerun the commands in [schedule.md](schedule.md) |
| A judging step's log shows `API Error: 400 This API key is not scoped to a workspace` | The `ANTHROPIC_API_KEY` secret holds a key that was not created inside a workspace | Have the user create a key inside a workspace in the Claude Console and run `gh secret set ANTHROPIC_API_KEY --repo <owner>/<repo>` again |
| Many assessments for unrelated jobs | Triage keeps what it is unsure about | Add the unwanted fields to `targets.avoid` in `profile/criteria.json` |
| A wanted kind of job never appears | Its boards are not watched, or a hard limit drops it | `sources probe` the companies; check `employment` and `locations.regions` |
| `engine.intact` is false in `status` | Files under `.hunter/engine/` were edited | Run `upgrade` from this skill's copy to restore them |
| A source fails every time | The company moved its job board, or the board changed | `sources test`, then `sources probe "Company"` and replace the entry |
| No postings are judged, and the README says judging is paused | The monthly budget (`caps.max_usd_per_month`) is reached | It resumes on the first of next month (UTC); to resume sooner, raise or remove the budget |
| The README says "Judging has failed since …" | Every judging request in a stage got no answer: a missing or expired secret, an API error, or Claude Code not installed | `judging_failed.detail` in `feed/meta.json` has the first line of the error, the judging step's log the rest; for credentials see [schedule.md](schedule.md) |

Do not repair the feed by editing generated files. Report what you found and fix the input.
