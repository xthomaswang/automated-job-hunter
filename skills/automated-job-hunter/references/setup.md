# Setting up a feed

Do these in order. `hunter` below means this skill's own copy, `scripts/hunter.py`, until the feed
repo exists; after step 5 it means the feed repo's `python3 .hunter/engine/hunter.py`.

Steps 1 to 6 need only Python 3.9 or newer and `git`. Step 7, putting the feed on GitHub, also needs
the GitHub CLI (`gh`); check for it early with `gh --version` so the user can install it while you work.

## 1. Learn what they are looking for

Ask for their résumé or CV (a file path, or pasted text) and read it. Then ask only what it does not
already answer:

- Which roles they want. Which related roles they would also consider. Which they do not want.
- Level: the lowest and highest seniority to show, and roughly how many years of experience.
- Hard limits: employment type (full time, internship, contract...), and which countries.
- Whether they need visa sponsorship in those countries.
- Companies they especially want, and any they want left out.
- How often the feed should update: every hour, 4 hours (the default), or 8 hours.

Treat a limit as hard only if they said so. A hard limit drops postings before any model sees them,
so "I'd prefer remote" belongs in the profile text, not in the constraints.

## 2. Write the profile

`profile/profile.md` is the experience every posting is assessed against. Write it from the résumé
as plain prose, a few short paragraphs, under 6,000 characters:

- What they have done: fields, methods, tools, kinds of projects, years, level, education.
- What they want next, in their own terms, including soft preferences (remote, city, team size).
- Leave out their name, email, phone, address, links to personal profiles, and the names of
  employers or schools unless they matter for matching. `hunter audit` rejects contact details.

Do not copy the résumé file into the feed repo.

## 3. Write the criteria

`profile/criteria.json`. Only `targets.roles` is required; everything else has a default.

```json
{
 "schema_version": 1,
 "targets": {
  "roles": ["UX Researcher", "Design Researcher"],
  "adjacent": ["Research Operations"],
  "avoid": ["Sales", "Data Science", "Software Engineering"]
 },
 "seniority": {"min": "junior", "max": "senior", "years": 3},
 "employment": ["full_time"],
 "locations": {"regions": ["US"], "remote": "any", "cities": []},
 "eligibility": {"needs_sponsorship": false},
 "companies": {"prefer": [], "exclude": []}
}
```

- `roles` are the jobs they want; a posting for one is always listed, even with a seniority gap.
  `adjacent` are jobs they would consider; those are listed only when the fit is good enough.
- Fill in `avoid` with the neighbouring fields they do not want. Triage keeps anything it is unsure
  about, and every posting it keeps costs a full assessment, so a clear `avoid` list is the main
  thing that keeps cost down.
- `employment` and `locations.regions` are hard limits; leave them empty for no limit. Regions are
  two-letter country codes, or `EU`.
- `locations.remote` and `locations.cities` are preferences passed to the assessment, not filters.
- Optional: `thresholds` (`strong` 75, `good` 55, `publish_min` 40), `caps` (`max_triage` 1500,
  `max_assess` 100 per update), `backfill.days` (unset: judge every open posting on the first run;
  a number: skip postings older than that on the first run).

## 4. Choose the boards

Run `hunter sources list` to see the boards this skill knows. Propose the ones that fit the roles
and companies they named, and for a company that is not listed run
`hunter sources probe "Company Name"`. Show the user the list and let them adjust it. Twenty to
sixty boards is a normal size.

The job feeds (`remotive`, `remoteok`, `jobicy`) list remote jobs from many employers. They are
broad and noisy; add them only if the user wants remote work.

## 5. Create the feed repo

Ask where it should live (a new, empty directory) and confirm before creating anything.

```sh
python3 <this skill>/scripts/hunter.py --repo <directory> init --cadence 4h
cd <directory>
python3 .hunter/engine/hunter.py sources add <id> <id> ...
```

Write `profile/profile.md` and `profile/criteria.json` there, then check and commit:

```sh
python3 .hunter/engine/hunter.py audit
python3 .hunter/engine/hunter.py sources test
git add -A && git commit -m "Set up the job feed"
```

`sources test` reads each board once. Remove or fix any that fail before going on.

## 6. Run the first update

Follow [run.md](run.md) with `--no-push`. Tell the user first that this judges every open posting
on their boards that passes the hard limits, that it stops at the per-update caps, and roughly what
that is: `hunter collect` reports how many postings are queued, triage costs about $0.14 per
thousand at API prices and assessment about $0.02 each (on a subscription it draws on their plan
instead). A large backlog finishes over the next few updates.

Then show them the result: the top of `README.md`, and one brief from `jobs/`.

## 7. Put it on GitHub and schedule it

This step needs the GitHub CLI, logged in with the `workflow` scope, because the feed repo contains
a workflow file. Check:

```sh
gh --version && gh auth status
```

If `gh` is missing, the user installs it: the macOS package installer
(`gh_<version>_macOS_universal.pkg`) from https://github.com/cli/cli/releases needs no Homebrew, or
with Homebrew, `brew install gh`. Then they run `gh auth login`, and `gh auth refresh -s workflow` if
`gh auth status` does not list the `workflow` scope. Until then the feed works on this machine:
updates commit locally, and it can go on GitHub later.

Only with their go-ahead: create a **private** repository and push.

```sh
gh repo create <name> --private --source . --push
```

Then follow [schedule.md](schedule.md).
