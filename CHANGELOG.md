# Changelog

What changes for people who run a feed. Bring a feed repo up to date with
`scripts/hunter.py --repo <feed repo> upgrade` from the skill's copy, then commit.

## Unreleased: engine 0.2.0, state schema 2

### An engine never misreads state written by a newer one

- The engine reports version 0.2.0 and writes state schema 2: `blk` tags now hold several facts,
  which an earlier engine misreads. Earlier engines only ever reported 0.1.0, whatever they
  contained, so a version number alone could not tell them apart.
- An engine that finds state newer than it can read stops with the usual JSON error, naming both
  versions, instead of failing part-way. Every earlier engine already refuses schema 2 that way.
- `upgrade` refuses to install an engine older than the feed's: older than its `.hunter/VERSION`,
  or unable to read its state schema. Upgrade from a newer copy of the skill instead.
- A `blk` tag holding a fact this engine cannot read is left alone, never judged again on part of
  its facts.

### Unmet requirements (assessment rubric `assess-v2`)

- Each assessment now lists the mandatory requirements the profile does not meet, of four kinds:
  a degree, current enrollment, a credential or a license. Each comes with the requirement quoted
  from the posting. A requirement that is only preferred, or that equivalent experience can
  replace, is not listed. The list is `unmet` in `positions.jsonl`, an "Unmet requirements"
  section in the posting's brief, and an "unmet <kind>" flag in the README. The kinds are also
  kept for postings that are not listed (an `unmet:` tag in `state/`), and `feed/meta.json` and the
  README count assessed postings with an unmet requirement by kind, so you can see how often the
  assessments find each kind.
- New setting `thresholds.unmet_requirements` in `profile/criteria.json`, for example
  `["degree", "enrollment"]`. It leaves out postings with an unmet requirement of those kinds, in
  the same way as `max_required_years` and `max_seniority`: they are counted under `blocked`, a
  listed posting it takes out gets a `blocked` event, and removing a kind sends the postings it
  left out back to be assessed. It is off by default and judges nothing again.
- The assessment can only tell from what `profile/profile.md` says, so the profile should state
  the person's degrees and fields, whether they are enrolled now, and any certifications or
  licenses.
- **Nothing is assessed again by upgrading.** Postings assessed before this version have no
  `unmet` field and stay listed, unchecked; `judged.rubric` says which rubric assessed a posting.
  To check them, run `python3 .hunter/engine/hunter.py requeue --assessed` once in the feed repo,
  then commit `state/`. It prints the estimated cost, about $2 for every hundred assessed postings,
  and the work is spread over the next updates. If you are editing `profile/profile.md` anyway,
  skip the requeue: a profile change assesses everything again by itself. Consider leaving
  `unmet_requirements` empty until those assessments have arrived: the unmet requirements are then
  only flagged, so you can check the model's claims against their quotes before any posting is
  left out. Setting the kinds afterwards judges nothing again.
- `hunter apply`'s `blocked` count no longer counts a listed posting twice when it is assessed
  again and left out.
- The `blk` tag in `state/` now records every fact a hard requirement checks, such as
  `blk:years=7+seniority=senior+unmet=degree`. Relaxing one requirement then sends back only the
  postings that no other requirement still leaves out. Tags written by earlier versions still work.

### Workday career sites, and a probe that takes a careers URL

- New source type `workday`, added as `workday:<host>/<site>:Company`. It reads the JSON the site's
  own public page loads, through the site's search: `queries` on the entry, which default to the
  wanted and related roles. That search matches any of the words; on a site where one search
  matches more than 1,000 postings the source fails and says to set `categories` on the entry to
  the site's job categories for these roles.
- A Workday listing has no text, and each posting's text is one more request. Texts are fetched
  only where an update needs them, postings about to be judged first, at most `max_details` (100)
  per source per update, one request a second. A new posting joins the feed only with its text, so
  a new Workday source fills in over a few updates, and nothing is closed for want of a text. The
  README and `feed/meta.json` count the postings still waiting for theirs.
- `sources probe <url> Company Name` takes a careers page or one posting's link. It reads
  Greenhouse, Ashby, Lever, Recruitee and Workday URLs and prints the `add` line, listing a Workday
  site's job categories with their counts; it follows a company's own careers page to the board it
  links to; and it names the systems this version cannot read, such as Oracle Recruiting Cloud,
  Taleo, iCIMS, SuccessFactors or Eightfold, instead of finding nothing. A name probe that finds
  nothing says to probe a URL.

### SmartRecruiters companies

- New source type `smartrecruiters`, added as `smartrecruiters:<company id>:Company`. It reads the
  public Posting API through `queries`, as a Workday source does. A listing gives each posting's
  place, employment type and date, so the hard limits can leave a posting out before its text is
  fetched. Texts are fetched in the same order and under the same `max_details` budget as Workday's,
  up to 8 requests a second. A search that matches more than 2,000 postings fails and says to use
  more specific queries.
- `sources probe "Company Name"` also finds SmartRecruiters companies, and `sources probe <url>`
  reads a SmartRecruiters posting's link.

### Job lists on GitHub as sources, and each posting judged once

- New source type `github-list`: a job list kept in a public GitHub repo, as a JSON file or Markdown
  tables. A row is used only when its link is one posting on Greenhouse, Lever, Ashby,
  SmartRecruiters or Workday. That posting's text, date and link come from the employer's system,
  so it is listed as `official`. Rows that link to sites that re-post jobs, to systems with no
  public API, or to employers' own pages are skipped. Add a list to `config/sources.json` by hand
  (step 4 of `references/setup.md`); `sources test` shows what became of every row.
- `filters` (fields of a JSON list) and `sections` (headings of a Markdown list) leave rows out in
  code, before any text is fetched. A list fails instead of leaving out every row when its config
  matches nothing in it (a section name, a filter value or a filter field), so a typo or a renamed
  heading never closes the postings it had.
- A posting is judged once, wherever it is listed: a list's posting on a board the feed watches
  defers to the board's own, a posting two lists name is judged once, and copies of one opening on
  several aggregators defer to one of them. An aggregator copy also defers to a list's posting.
- A SmartRecruiters posting's date now comes from its detail when its listing gave none.

### Feed repos commit under the user's identity, and `status` shows the schedule

- `init` and `upgrade` now tell git, in the feed repo only, not to make up an identity from the
  machine's name, which no GitHub account has. A commit without one fails with "no email was given
  and auto-detection is disabled", and both commands print how to set the user's GitHub identity.
  GitHub sends notifications for scheduled workflows to the user who last changed the cron line.
- `status` has a `schedule` entry: the cron line, the commit that last changed it and its
  addresses, with a `warning` when an address is one git made up. `upgrade --cadence` always writes
  a new cron line, so the user can commit one under their own account.
- None of this makes GitHub start scheduled runs. In issue #41, a private feed repo's schedule
  started two runs in about two days, neither at one of its times, and a new, minimal schedule in
  the same repo started none in six. "If scheduled runs never start" in
  `references/schedule.md` says what to check, and how to start the runs from elsewhere, for
  example with a timer that runs `gh workflow run`.

### A timer on a Mac can start the workflow when GitHub does not

- New command `timer`. `scripts/hunter.py --repo <feed repo> timer install` sets up a launchd agent
  on this Mac. It runs `gh workflow run job-hunter.yml` for the feed at the times of its cron line,
  converted to the Mac's clock. The runs happen on GitHub as before; only the start comes from the
  Mac, and it needs no new secret. While the Mac sleeps nothing starts; when it wakes, launchd
  starts the workflow once if a time went by. A failed start is tried again for about five minutes.
- `install` first checks that `gh`, run the way launchd will run it, can see the workflow and that
  the workflow is enabled. `timer` alone shows the times on the Mac's clock and how the last start
  went, and `timer remove` takes the timer away and keeps its log.
- `status` has a `timer` entry on a Mac where the feed has a timer. It carries a `warning` when the
  last start failed, when launchd has not loaded the timer, or when the cron line changed after it
  was installed.
- This does not make GitHub start scheduled runs; it starts them instead. See "Start the workflow
  from a Mac" in `references/schedule.md`.

### SAP SuccessFactors career sites

- New source type `successfactors`, added as `successfactors:<host>:Company` (or
  `<host>/<brand>` for a brand's site under its own path). It covers Career Site Builder sites on
  a company's own domain whose search pages show how many postings they found. `sources probe`
  recognizes such a site from its page.
- A site is read through `queries`, each searched as a phrase. Every page is checked against the
  count it shows, so a changed layout fails the source rather than closing postings. A search
  matching more than 1,000 postings fails.
- Two kinds of SuccessFactors site stay unread, as their robots.txt requires: sites whose search
  loads its postings from a script, and SAP's older job board on its own hosts. The probe names
  both, and for the older board suggests probing the company's own career site.

### Why Oracle Recruiting Cloud and iCIMS have no reader

- `sources probe` still names these two systems as ones this version cannot read, and now says why.
  Oracle reserves the job endpoints its career sites use for itself and approved partners. iCIMS's
  terms forbid any automated access to its career portals. So there is no board to add, and no
  other way in to look for. `docs/spikes.md` (E9, E10) quotes the documents.

### Eightfold career sites

- New source type `eightfold`, added as `eightfold:<host>/<domain>:Company`. It covers sites on
  `<company>.eightfold.ai/careers` and the same app on a company's own host. `sources probe` takes
  the careers page or one posting's link, recognizes either kind, and reads the domain the site's
  own pages name, which its search needs.
- A site is read through `queries`, like Workday. Every page of a search reports its total, and a
  short read fails the source rather than closing postings. A search matching more than 500
  postings fails and says to use more specific queries. Place, remote work and date come from the
  listing. Texts are fetched on demand under `max_details`, one request a second.
- `sources probe <url>` reads a company's careers page where its redirects end. When an old
  address moves to the host a site is on now, the add line names that host, and the result shows
  the address as `resolved`.
- Some sites on a company's own host answer 429 (too many requests) after a few requests, even at
  one a second. That update then skips the source and keeps its last snapshot.

### Radancy TalentBrew career sites

- New source type `radancy`, added as `radancy:<host>:Company`. It covers TalentBrew career sites on
  a company's own host. `sources probe` recognizes such a site from its careers page or one
  posting's page.
- The site's sitemap is the listing, read whole every update, so `queries` do not apply. A
  posting's title and city come from its link. Its text, date and own address come from its page,
  fetched on demand under `max_details` (100), one request a second. A posting whose own address is
  outside `locations.regions` is parked, and its employment type is written at the top of its text.
- Each update first reads the site's robots.txt and fails the source if it disallows the sitemap
  or the postings. A sitemap that cannot be read, one sitemap of an index that fails, or more than
  one posting link in twenty in a shape the reader does not know also fails the source, which keeps
  its last snapshot.
- On a Python whose robots.txt parser is older than RFC 9309, such as 3.9, a site whose robots.txt
  has Allow lines, wildcards or repeated groups is not read, because that parser can read them as
  allowing what the file disallows.

### One Workday requisition that a list names on two sites is judged once

- A job list can name one Workday requisition on two sites of a tenant, sometimes with the company
  written two ways. The second posting is now a variant of the first, so it is not triaged,
  assessed or published again. Workday postings are compared by tenant and requisition, whatever
  the site.
- A requisition's later postings add `-1`, `-2` and so on to its id. The comparison drops that
  ending only when at least five digits remain, so `R-12` and `R-13` stay two requisitions.
- Pairs that were already judged twice stay until one of them closes.

### A posting's own record decides whether it is in the wanted regions

- A job list's region label no longer lets a posting outside `locations.regions` through. Neither
  does a listing that names no country, such as a street address or "3 Locations". When the
  posting's own record (Workday, SmartRecruiters, Greenhouse, Lever or Ashby) names only countries
  outside the regions, the posting is parked with those countries and never judged, and later
  updates fetch nothing more for it. Widening the regions sends it to triage. An unknown place
  still passes.
- The same applies to a watched Workday site's postings whose listing names no country.
- `collect` and `feed/meta.json` count these as `outside_regions`, and the README says how many.
  `requeue --parked` leaves them parked.
- Belfast and Cardiff are read as cities in the UK.
- Postings judged before this version keep their verdicts.

### Variants on boards that read texts on demand stay grouped

- On Workday, SmartRecruiters, SuccessFactors and Eightfold boards, and on job lists, a variant of
  another posting (same company and title, near-identical text) no longer comes apart on the next
  update just because neither text was read again.
  - When a text wasn't read, a comparison uses the hash frozen when that posting was assessed.
  - Otherwise it keeps the grouping it had. No new variant is made without a comparison.
- Variants that came apart under an earlier version are not all grouped again. One whose lead was
  dropped at triage is triaged once on its own.
