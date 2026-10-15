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
