# When something goes wrong

Start with `python3 .hunter/engine/hunter.py status` in the feed repo, then the "When something is
wrong" section of `.hunter/RUNBOOK.md`, which explains each error the engine reports.

| Symptom | Likely cause | What to do |
|---|---|---|
| A command exits with status 2 | A person has to fix something | Read `detail` in the JSON it printed; it names the file and the problem |
| `WorkError` from `advance` or `apply` | The tree has uncommitted changes, or the scratch folder changed | Commit or discard the changes, then start again from `collect` |
| Nothing new for days | Sources failing, the schedule disabled, or simply nothing new | `status` for failing sources; `gh run list` for the schedule; `feed/meta.json` for `as_of` |
| The workflow's judging step fails at once | The secret is missing or expired | Have the user rerun the two commands in [schedule.md](schedule.md) |
| Many assessments for unrelated jobs | Triage keeps what it is unsure about | Add the unwanted fields to `targets.avoid` in `profile/criteria.json` |
| A wanted kind of job never appears | Its boards are not watched, or a hard limit drops it | `sources probe` the companies; check `employment` and `locations.regions` |
| `engine.intact` is false in `status` | Files under `.hunter/engine/` were edited | Run `upgrade` from this skill's copy to restore them |
| A source fails every time | The company moved its job board, or the board changed | `sources test`, then `sources probe "Company"` and replace the entry |
| No postings are judged, and the README says judging is paused | The monthly budget (`caps.max_usd_per_month`) is reached | It resumes on the first of next month (UTC); to resume sooner, raise or remove the budget |
| The README says "Judging has failed since …" | Every judging request in a stage got no answer: a missing or expired secret, an API error, or Claude Code not installed | `judging_failed.detail` in `feed/meta.json` has the first line of the error, the judging step's log the rest; for credentials see [schedule.md](schedule.md) |

Do not repair the feed by editing generated files. Report what you found and fix the input.
