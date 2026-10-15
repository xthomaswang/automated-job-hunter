# When something goes wrong

Start with `python3 .hunter/engine/hunter.py status` in the feed repo, then the "When something is
wrong" section of `.hunter/RUNBOOK.md`, which explains each error the engine reports.

| Symptom | Likely cause | What to do |
|---|---|---|
| A command exits with status 2 | A person has to fix something | Read `detail` in the JSON it printed; it names the file and the problem |
| `WorkError` from `advance` or `apply` | The tree has uncommitted changes, or the scratch folder changed | Commit or discard the changes, then start again from `collect` |
| Nothing new for days | Sources failing, the schedule disabled, or simply nothing new | `status` for failing sources; `gh run list` for the schedule; `feed/meta.json` for `as_of` |
| A scheduled run is marked failed at "Report a failed judging step" | A judging step failed, most often because no Claude secret is set or the token expired | Read the judging step's log; for credentials, have the user rerun the commands in [schedule.md](schedule.md) |
| A judging step's log shows `API Error: 400 This API key is not scoped to a workspace` | The `ANTHROPIC_API_KEY` secret holds a key that was not created inside a workspace | Have the user create a key inside a workspace in the Claude Console and run `gh secret set ANTHROPIC_API_KEY --repo <owner>/<repo>` again |
| Many assessments for unrelated jobs | Triage keeps what it is unsure about | Add the unwanted fields to `targets.avoid` in `profile/criteria.json` |
| A wanted kind of job never appears | Its boards are not watched, or a hard limit drops it | `sources probe` the companies; check `employment` and `locations.regions` |
| `engine.intact` is false in `status` | Files under `.hunter/engine/` were edited | Run `upgrade` from this skill's copy to restore them |
| A source fails every time | The company moved its job board, or the board changed | `sources test`, then `sources probe "Company"` and replace the entry |
| A Workday source fails with "set `categories`" | One of its searches matches more than 1,000 postings; the site's search matches any of the words, so rewording does not help | `sources probe <the site's url>` lists its job categories; set `categories` on the entry to the ones for these roles, or use fewer queries |
| A SmartRecruiters source fails with "more specific queries" | One of its searches matches more than 2,000 postings | Set `queries` on the entry to fewer, more specific role titles |
| A Workday source fails with "no job category" | A name in its `categories` is not one of the site's | `sources probe <the site's url>` lists the names it has |
| A `github-list` source fails | The file moved or was emptied (an empty list is refused, so its postings are not closed), or its layout changed | `sources test`; correct `path`, `branch`, `rows`, `fields` or `columns` |
| A job list gives few postings | Most of its rows link to sites that re-post jobs or to systems with no public API, which are skipped | `sources test` shows what became of the rows; nothing is lost by skipping them |
| A large employer's board cannot be found | It uses Workday or a system this version cannot read | `sources probe <careers page url> Company Name`; it adds Workday sites and names the systems it cannot read |
| No postings are judged, and the README says judging is paused | The monthly budget (`caps.max_usd_per_month`) is reached | It resumes on the first of next month (UTC); to resume sooner, raise or remove the budget |
| The README says "Judging has failed since …" | Every judging request in a stage got no answer: a missing or expired secret, an API error, or Claude Code not installed | `judging_failed.detail` in `feed/meta.json` has the first line of the error, the judging step's log the rest; for credentials see [schedule.md](schedule.md) |

Do not repair the feed by editing generated files. Report what you found and fix the input.
