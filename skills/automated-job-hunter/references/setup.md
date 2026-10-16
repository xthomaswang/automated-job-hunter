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
- How often the feed should update: every hour, 4 hours (the default) or 8 hours, or at set times
  of day. For set times, also ask their time zone: the schedule runs in UTC.

Also run `gh --version` now. If the GitHub CLI is missing, tell them at this point that putting the
feed on GitHub (step 7) needs it, and how to get it, so they can install it while you work through
the next steps.

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
- Get `targets` and `seniority` as right as you can now. Changing them later asks triage again for
  every posting it dropped and asks for every assessment again, which after a few weeks can be
  hundreds of assessments (about $0.02 each).
- `employment` and `locations.regions` are hard limits; leave them empty for no limit. Regions are
  two-letter country codes, or `EU`.
- `locations.remote` and `locations.cities` are preferences passed to the assessment, not filters.
- Optional: `thresholds` (`strong` 75, `good` 55, `publish_min` 40), `caps` (`max_triage` 1500,
  `max_assess` 100 per update), `backfill.days` (unset: judge every open posting on the first run;
  a number: park postings older than that on the first run; `hunter requeue --parked` sends them to
  triage later).
- Optional, when the user treats something as a hard requirement:
  - `thresholds.max_required_years`: leave out postings that require more years than this. A
    posting that states no years is never left out, and preferred years never count.
  - `thresholds.max_seniority` (`intern` to `staff`): leave out postings assessed as more senior,
    which catches leadership roles that state no years. An unknown seniority never counts.
  - `thresholds.unmet_requirements`: any of `degree`, `enrollment`, `credential` and `license`.
    Leave out postings whose assessment quotes a mandatory requirement of that kind the profile does
    not meet, such as a PhD, current enrollment in a degree program, or a nursing license. A
    requirement that is only preferred, or that equivalent experience can replace, never counts.
    The assessment can only tell from what the profile says, so the profile must state the person's
    degrees and fields, whether they are enrolled now (in what program, finishing when), and any
    certifications or licenses; otherwise nothing is ever left out for it. With the list empty,
    unmet requirements are still flagged in the README and quoted in each brief, so the user can
    see a few before deciding which kinds should leave postings out.
  - `thresholds.direct_min`: the lowest fit at which a wanted role is still listed. Unset, a wanted
    role is always listed, however low the fit.

  These change only what is listed, so they judge nothing again and take effect at the next update,
  for postings already listed too. Relaxing a hard requirement sends the postings it left out back
  to be assessed. Unmet requirements are found only by assessments made since rubric `assess-v2`
  (see "Changing the search" in SKILL.md for checking older ones).
- Optional: `caps.max_usd_per_month`, a monthly budget in US dollars (unset: no budget). Before each
  update the engine adds up this month's spending from `feed/usage.jsonl`; once it has reached the
  budget, judging pauses until the first of next month, and new postings wait in the queue. Months
  are UTC, and the spending is Claude Code's list-price estimate whatever the credential. An update
  that starts below the budget runs to the end, so a month can go over by one update's worth, about
  $2 at the default caps. Only updates logged since this version was installed count, so the first
  month undercounts what was spent before then. Changing it judges nothing again.

## 4. Choose the boards

Run `hunter sources list` to see the boards this skill knows. Propose the ones that fit the roles
and companies they named, and for a company that is not listed run
`hunter sources probe "Company Name"`. Show the user the list and let them adjust it. Twenty to
sixty boards is a normal size.

For a large employer the name probe often finds nothing, because many use Workday or a system
this version cannot read (it does find SmartRecruiters companies by name). Find its careers page (or the link of one of its postings) and run
`hunter sources probe <url> Company Name`; add what it reports, or tell the user the system it
names is not supported. A Workday site is read through its own search for the wanted and related
roles (set `queries` on the entry to search for something else). That search matches any of the
words, so it returns many unrelated postings, which triage screens out cheaply. On a very large
site a search can match more than 1,000 postings and the source then fails: set `categories` on
the entry to the site's job categories for these roles, which the probe lists with their counts,
for example `"categories": ["Research", "Design"]`. Use it only then: on a site of a few hundred
postings, leave it off, because a role is often filed under a generic category ("Product",
"Strategy") and triage screens the rest out cheaply. Some sites list no categories at all (the
probe shows an empty list); there, use fewer queries. Its postings' texts take one request each,
so a new Workday source fills in over a few updates, up to 100 postings an update; the feed's
README counts the ones still waiting for their text. SmartRecruiters companies
(`smartrecruiters:<company>:Company`) are searched and filled in the same way. Sites on the
shared host (`wd1.myworkdaysite.com/recruiting/<tenant>/<site>`) work the same way; the probe
prints their add line with the tenant.

Eightfold career sites are searched and filled in the same way. They are on
`<company>.eightfold.ai/careers`, or the same site on the company's own careers host. Probe the
careers page or one posting's link, and add the line it prints, `eightfold:<host>/<domain>:Company`.
The domain is the one the site's own pages name; the probe reads it from them. A search there
lists 10 postings a request, so a search matching more than 500 postings fails the source: set
fewer, more specific `queries` on the entry.

An SAP SuccessFactors career site (built with Career Site Builder) is on the company's own domain,
often `jobs.<company>.com`, so it cannot be found by name: probe its address or one posting's link.
The probe knows the site by its page and prints `successfactors:<host>:Company`, or
`successfactors:<host>/<brand>:Company` for a brand's site under its own path. It is searched and
filled in like Workday, one request a second. Each query is searched as a phrase, because the site's
search otherwise matches any of the words anywhere in a posting; a query with quotes of its own is
sent as written. These sites have no job categories to narrow by, so a search matching more than
1,000 postings fails the source: use fewer or more specific queries. The probe names the two kinds of
SuccessFactors site that cannot be read: a site whose search page loads its postings from a script
(from a path robots.txt disallows on every such site), and the older job board on SAP's own hosts
(`career<N>.successfactors.com`, `sapsf.com`), whose robots.txt disallows everything. For a company
on the older board, look for its career site on its own domain and probe that.

A Radancy TalentBrew career site is also on the company's own host, often `careers.<company>.com`:
probe its address or one posting's link. The probe knows the site by its page and prints
`radancy:<host>:Company`. Such a site is not searched. Each update reads its sitemap, which lists
every posting, so `queries` and `categories` do not apply, and the hard limits read each posting's
city and title from its link. Texts are fetched as for Workday, up to 100 an update (`max_details`),
one request a second, so a site with thousands of postings fills in over many updates. Each update
first reads the site's robots.txt; if it disallows the sitemap or the postings, the source fails and
nothing is read.

The job feeds (`remotive`, `remoteok`, `jobicy`) list remote jobs from many employers. They are
broad and noisy; add them only if the user wants remote work.

A job list someone keeps on GitHub, as a JSON file or Markdown tables, can be a source too, when
the user names one. Each row is only a pointer. A row whose link is one posting on Greenhouse,
Lever, Ashby, SmartRecruiters or Workday is read from that system, with its own text and date, and
judged like a posting from the employer's board. Rows that link anywhere else (sites that re-post
jobs, systems this version cannot read such as iCIMS, an employer's own page) are skipped. Add the list
to `config/sources.json` by hand:

```json
{"id": "joblist", "ats": "github-list", "repo": "<owner>/<repo>", "path": "jobs.json", "filters": {"region": ["US"]}}
```

- `branch` is `main` unless set. The format comes from the file's ending (`.json`, `.md`), or set
  `format` to `json` or `markdown`.
- JSON: a list of rows, or an object holding one list (otherwise set `rows` to its key). The fields
  read are `company`, `title`, `location` and `url`; map other names with `fields`, for example
  `{"link": "applyUrl"}`. `filters` keeps the rows whose field has one of the values; a row that
  leaves the field empty is kept.
- A list's region or location is only a label. Whether a posting is in `locations.regions` is
  decided by the place the employer's own system gives. A posting the list labels US whose own
  record names another country is parked, not judged; the README counts it, and it comes back if
  the regions are widened to take it in.
- Markdown: every table whose headings name a company, a role and a link (`Application`, `Apply`,
  `Link`) is read; `↳` in the company column means the company of the row above. Map other
  headings with `columns`. `sections` keeps only the tables under the page headings that contain
  one of the names as whole words: `["Junior", "Mid"]` keeps a table under "🌱 Team Junior" and
  leaves out one under "Midwest".
- `hunter sources test` shows what became of each list's rows: left out by `filters` or `sections`,
  skipped and why, named twice, and how many postings each system gave. `unmatched` lists the
  section names and filter values nothing in the list has today. When none of the section names,
  or none of a filter's values, matches anything, or no row has the filter's field at all, the list
  fails with a message naming what it has, instead of leaving out every row and closing its postings.

A posting a list shares with a board the feed watches, or with another list, is judged once. On
Workday this goes by requisition, whatever the site: one requisition that a list names on two
sites of a tenant is judged once too. Each update fetches the text of up to 50 of a list's new
postings (`max_details`), so a long list fills in over a few updates.

A career site that no reader in this version reads, such as one employer's own site, can still be a
source when the user wants it: the feed repo can carry its own reader for it, in a `readers/`
folder. That reader is code that runs during every update, so write it only when the user asks for
it, and follow [own-readers.md](own-readers.md).

## 5. Create the feed repo

Ask where it should live (a new, empty directory) and confirm before creating anything.

```sh
python3 <this skill>/scripts/hunter.py --repo <directory> init --cadence 4h
cd <directory>
python3 .hunter/engine/hunter.py sources add <id> <id> ...
```

`--cadence` is `1h`, `4h` or `8h`, or for set times the UTC hours as a list such as `6,10,14,20`
(see "When it runs" in [schedule.md](schedule.md)).

Read what `init` printed before the first commit. A `warnings` entry about the email means git has
no identity on this machine. `init` sets `user.useConfigOnly` in the feed repo's own git config, so
git refuses to commit there rather than make up an address from the machine's name. No GitHub
account has such an address, so those commits would belong to nobody, including the one that sets
the schedule (see "Who GitHub ties the schedule to" in [schedule.md](schedule.md)). Set the user's
GitHub identity in the feed repo only:

```sh
git config user.name "<their name or GitHub login>"
git config user.email "<an address on their GitHub account>"
```

If `gh` is logged in, their noreply address keeps their own email out of the history:
`gh api user --jq '"\(.id)+\(.login)@users.noreply.github.com"'`. Otherwise ask which address their
GitHub account uses.

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
