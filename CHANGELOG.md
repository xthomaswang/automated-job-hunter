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

### A feed can carry its own readers

- A feed repo can keep reader modules in a `readers/` folder at its root, for a career site no
  built-in reader reads: one employer's own site, say, or one whose employer you would rather not
  name in a public repo. Each module declares a source type (`KIND`), which a source in
  `config/sources.json` names with `"ats"`, and a `fetch` that returns every posting or raises
  `SourceError`, as the built-in readers do. Optional flags have it searched for the wanted roles
  (`QUERY_SCOPED`) or fetch texts on demand (`DETAILED`), and `HOSTS` or `hosts(source)` names its
  hosts for `sources hosts`. `references/own-readers.md` has the contract, the names a reader may
  import, and a skeleton.
- A reader is code. It runs during `collect`, `sources test` and `sources hosts`, with the
  workflow's permissions, and must keep to the built-in readers' rules. `status` loads the readers
  too, to list the types they provide and every file not used, with the reason. Nothing the judging
  step runs ever loads them.
- A file that fails to load, or declares an invalid type, a built-in one or one another file
  declares, fails only the sources of that type, with the file and the reason; they keep their last
  snapshot. A type that no reader declares is still a configuration error, unless a reader file
  failed before its type was known.
- `upgrade` and `init` leave `readers/` alone. The workflow now also starts when `readers/` changes
  on `main`; run `upgrade` to bring that line into a feed's workflow.
- `readers/` counts as hand-written, like `profile/` and `config/`: `hunter audit` refuses a
  reader holding a credential, and a run that finishes after a push to `readers/` lands on top of
  it instead of stopping as `stale_base`.

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
- The engine reads robots.txt itself, as RFC 9309 says, so a file gives the same answer on every
  Python version. Python's own parser does not: on 3.9 it applies the first matching rule instead of
  the longest, reads no wildcards, and reads `Disallow: /?` as disallowing the whole site.

### The probe names Oracle Recruiting Cloud only for Oracle Fusion's hosts

- `sources probe <url>` named Oracle Recruiting Cloud, and said there was no board to add, for a
  careers page that linked to anything on `oraclecloud.com`, such as an image in Oracle's object
  storage. It now names it only for Oracle Fusion's hosts, `<tenant>.fa.<region>.oraclecloud.com`
  or `<tenant>.fa.oraclecloud.com`. Links to other Oracle Cloud hosts are passed over, like links
  to any host no system is known for.

### A probe that cannot read a careers page says what to try instead

- When `sources probe <url>` cannot read a company's careers page (a timeout, a refusal, or a bot
  filter's 403), it still names the error and now says what to try: open one of the company's
  postings and probe the URL of its page or its Apply link, or probe the company's name. Trying the
  same page again seldom helps when a site refuses scripted requests.
- An `http://` careers address is probed over `https://`, as the engine reads only https. It used to
  fail on that alone.

### `sources test` and `collect` show more of a source's error

- `sources test` now shows a failing source's whole error, where it showed 160 characters, so an
  error that lists several problems shows every one. `collect` shows up to 1,000 characters of it
  under `sources.failures`, where it showed 200. The state still records only the error's type or
  HTTP status, and the README and `feed/meta.json` still name only the failing sources.

### Every country's name is read in a posting's place

- The place table now has every country, all 249 ISO 3166 codes, under its English name, common
  alternates such as Czechia or Ivory Coast, and its own names, such as Deutschland, Suomi or
  República Dominicana. Accents and capitals don't matter, so "Panamá, Panamá, Panama" and
  "Beirut, Beyrouth, Lebanon" read as Panama and Lebanon: a posting whose own address is there is
  parked as outside `locations.regions` instead of going to triage (#63). Any country code can now
  go in `locations.regions`.
- `US` in `locations.regions` takes in the US territories, where US law applies: Puerto Rico, the
  US Virgin Islands, Guam, American Samoa, the Northern Mariana Islands and the US Minor Outlying
  Islands. So a posting in "San Juan, Puerto Rico" is still neither left out nor parked for
  `["US"]`, as when the table did not know Puerto Rico. A place there still reads as the territory,
  so regions that leave out `US` leave it out, and a posting parked there is tagged with its own
  code, such as `PR`.
- A country's name before a US state or Canadian province is a town there. "Lebanon,
  Pennsylvania" and "Holland, MI" are in the US. "Lebanon, PA", "Peru, IN" and "Mexico, MO" are
  unknown, since PA, IN and MO are also the codes of Panama, India and Macao. "Holland, MI" and
  "Mexico, MO" were read as the Netherlands and Mexico before.
- A two-letter code of a country the table did not have before is read only at the end of an
  address with a locality and a region, as in "Beirut, Beyrouth, LB". Elsewhere such a code is as
  often a region: MH in "Mumbai, MH" is Maharashtra, not the Marshall Islands, and "Remote - NA" is
  North America, not Namibia. The codes read before are read as before.
- "Georgia" alone is still unknown, since it names a US state too. "Tbilisi, Georgia" is the
  country and "Atlanta, Georgia" the US state, as the table knows both cities. Another city no longer
  outweighs it: "Rome, Georgia" was read as Italy and is now unknown.
- Known limit: a country's name alone is that country. A listing that gives only the town, as a
  Radancy link does, reads a US town named like a country, such as Lebanon, Pennsylvania, as
  abroad, as it already read Dublin, Ohio as Ireland.
- From the next update, a posting whose listing or own record names one of the newly read countries
  outside `locations.regions` is left out or parked, as other postings outside the regions are.
- A posting parked for its place before this version keeps the countries in its `place` tag, which
  were decided when it was parked. So one parked under the old reading, such as "Mexico, MO" read as
  Mexico, stays parked until its listing changes and its own record is read again, or until the
  regions take in that country. `requeue --parked` leaves it parked.

### Oregon's code OR is no longer read as the word "or"

- The place reader split a location at "or", so that "Remote or New York" is two places, and took
  Oregon's code for that word too. "Portland, OR" read as unknown and "Eugene, OR, USA" as partly
  unknown, both of which pass, and a town named like a country or a city abroad, such as "Lebanon,
  OR" or "Dublin, OR", read as that country (#69).
- An OR in capitals that stands as a part of its own, after a comma, a dash or a bracket and before
  one or nothing, is now Oregon's code. So those read as the US, and "Salem, OR / Remote" is two
  places, the first in the US.
- The word "or" between places still splits them, as in "Remote or New York", "London or Paris" or
  "Remote OR Hybrid", and "Portland, OR or Seattle, WA" is two places in the US. A dangling "or" is
  no state, as in "Remote or", "Remote OR" or "Remote, or".

### Every Canadian province and territory is read

- The place table knew seven of Canada's thirteen provinces and territories. It now knows all of
  them, by code and by English and French name, such as Saskatchewan, Terre-Neuve-et-Labrador,
  Île-du-Prince-Édouard, Nouvelle-Écosse or Yukon. "Saskatoon, SK, CA" was read as Slovakia, so a feed
  wanting Canada left it out; it now reads as Canada. "Regina, SK, Canada" no longer names Slovakia
  as well, nor "St. John's, NL, Canada" the Netherlands or "Charlottetown, PE, Canada" Peru (#68).
- SK, NL, PE, NU and YT are also the codes of Slovakia, the Netherlands, Peru, Niue and Mayotte, so
  on their own they prove nothing, as IL or IN don't. A known city settles them, and the table of
  cities now has the main job cities of the Netherlands, Peru and Slovakia, such as Delft, Leiden,
  The Hague or Den Bosch, Cusco or Arequipa, and Bratislava or Košice. So "Delft, NL", "Leiden,
  Zuid-Holland, NL", "Cusco, PE" and "Bratislava, SK" read as before. So does CA beside such a code,
  since CA is Canada's code and California's: "Saskatoon, SK, CA" is in Canada. The countries by name
  are read as before.
- Such a code alone, or with a town the table doesn't know, is now unknown, which passes: "Remote,
  NL" and "Remote, SK" were read as the Netherlands and Slovakia, and "St. John's, NL" or
  "Saskatoon, SK" as the Netherlands and Slovakia. Beside another country's code it now counts for
  nothing, as DE already did: "Remote (NL, BE)" reads as Belgium only.
- The cities of the provinces and territories whose codes are also countries' codes are known too
  (Regina, Saskatoon, St. John's, Charlottetown, Whitehorse, Iqaluit and a few more), so "Regina, SK"
  reads as Canada even without "CA" or "Canada" beside it.

### A Greenhouse board whose postings have no https link is read whole

- A Greenhouse posting whose link in the board's API (`absolute_url`) is missing or not https is now
  linked to its page on Greenhouse itself, `https://job-boards.greenhouse.io/<board>/jobs/<id>`, the
  form a list's link to it already takes. Such a posting failed its whole board, since one
  incomplete posting aborts the snapshot: `Job … has no public https URL` (#74). A posting whose
  link is https keeps it, and one missing its id or title still fails the board, as before.
- A list's link to a Greenhouse posting that has no link of its own now brings the posting's text.
  It used to wait for it on every update.

### Ashby boards whose names have spaces or dots can be added

- An Ashby board's name can have spaces and dots, as in `Acme AI` or `acme.com`, and such boards are
  public. `sources add` and `sources probe` refused them as invalid board slugs (#73). Now
  `sources add "ashby:Acme AI:Acme"` adds one, and so does the name as the board's link writes it,
  `Acme%20AI`. `sources probe https://jobs.ashbyhq.com/Acme%20AI Acme` reads the board and prints
  that add line, and `sources hosts` names Ashby's API host for it. The board is read like any
  other, with its name percent-encoded in the address of Ashby's API.
- A list's link to a posting on such a board already kept the name percent-encoded in the posting's
  id, as in `ashby~Acme%20AI~<id>`. Now that such a board can be watched, a list naming one of its
  postings defers to the board's own, as for any other board.
- Greenhouse, Lever and Recruitee board names are still letters, digits, `-` and `_`, as their
  addresses need.

### A Workday site's HTTP 422 says that the site name is probably wrong

- A Workday site's jobs API answers HTTP 422 when the site name in the address is wrong, or the site
  is not public. `sources probe` showed only `HttpError: HTTP 422 …` for it, which looks like a
  refusal. It now says that the site name is probably wrong, or the site is not public, and to open
  one of the company's postings and probe its URL, which carries the right tenant and site (#76).
- Every other error is shown as before, including a 422 from another system.

### A probe that finds a Phenom career site points to a posting's Apply link

- Several large companies' career sites are a Phenom front end: a posting's Apply button leads to
  the system that holds it, often a Workday site this version reads. `sources probe <url>` named
  Phenom as a system it cannot read and said there was no board to add. When it finds no board it
  reads and names Phenom, or Avature, which some companies use the same way, it now says to open
  one posting and probe its Apply link, which can lead to a system it reads, such as Workday (#78).
- Where the page also links a system the probe reads, that board is reported as before. For the
  other systems it cannot read, such as iCIMS or Oracle Recruiting Cloud, which hold the postings
  themselves, it still says that there is no board to add.

### Eightfold career sites in the older layout

- Some companies run an Eightfold career site on their own host in the app's older layout, which
  neither the probe nor the reader knew (#75). `sources probe` now recognizes such a site from its
  careers page or one posting's page and prints `eightfold:<host>/<domain>/apply-v2:Company`, which
  adds the source with `"layout": "apply-v2"`.
- Such a site is read like one in the newer layout: through `queries`, 10 postings a request, every
  search checked against its total, and a search matching more than 500 postings refused. Texts are
  fetched on demand under `max_details`, one request a second. Place, date and link come from the
  listing; a place the site writes as "Helsinki,Finland" reads "Helsinki, Finland".
- Every update of such a site, and its probe, first reads the site's robots.txt, and the source
  fails if it disallows the listing or a posting's text.
- Eightfold sources without `layout` are read exactly as before, with nothing to change. A site that
  moves to the other layout fails until it is probed again and its new add line is added, which
  replaces the entry under the same id.

### A name probe says how sure it is of each board it finds

- `sources probe "<Company>"` tries the company's name on Greenhouse, Ashby, Lever, Recruitee and
  SmartRecruiters, and a short name often finds another company's board with the same name. Each
  match now has a `confidence` (#77):
  - `high` when the board's postings name the company asked for. Greenhouse, Recruitee and
    SmartRecruiters name it in the reply the probe already reads.
  - `medium` when they name no company, as on Lever and Ashby, so only the board's address matches.
  - `medium`, with a `warning`, when the slug that found the board spells only the first words of the
    name, as `capital` does for "Capital One", and no name its postings give spells all of them.
    Many companies' names start with such a word.
  - `medium`, with a `warning`, when the probe runs in a feed (`--repo`, with
    `profile/criteria.json`) and every posting of the board whose place can be read is outside
    `locations.regions`: "None of its 39 postings is in the wanted regions (NG)." The places are
    read as stage 0 reads them, and a place that cannot be read is not outside, so a board with a
    posting in the regions has no warning. Where some places cannot be read, the warning counts
    the postings "with a known place". Outside a feed, or with no regions set, the places are not
    checked. Name probes met same-named companies abroad this way, and a board of sales agents in
    another country.
  - `low`, with a `warning`, when the board has fewer than 5 postings, or its postings name another
    company. A board that small is often another company's with the same name: name probes met
    such boards of one to three postings, and the sample of three titles then shows most or all of
    it, too little to tell whose board it is. A warning drops nothing, so it costs only a look at
    the sample. A small board is flagged even when it names the company asked for, since a company
    with the same short name names itself the same way.
- Names are compared word by word, without case, punctuation, a leading "The", or legal suffixes
  and generic words at the end, such as Inc, GmbH, Group, Holdings, Labs, AI, Software, Systems or
  Technologies: "Acme Labs" is Acme, so its board under `acme` matches the whole name. Words for what
  a company does, such as Health or Robotics, still count. One name beginning the other counts as the
  same, as "Acme" and "Acme Rocket Co." do, and so does a difference only in spaces, as in "Back
  Market" and "Backmarket".
- When any match has a warning, `next` says to check that its sample looks like the company's
  postings before adding it, or to probe the company's careers page, or one of its postings,
  instead.
- No match is dropped, and the probe makes no more requests than before. Its one request to a
  SmartRecruiters company now asks for a page of 100 postings instead of 3, to know where they are;
  a company with more postings than that is not checked for its places.

### A site that answers 429 is asked again more patiently

- A request that a site answers with HTTP 429 (too many requests) is now asked again up to seven
  times, waiting 1, 2, 4, 8, 16, 30 and 30 seconds, or as long as the site's `Retry-After` says, at
  most 30 seconds (#89). It used to be asked again twice, so a site that refuses about one request
  in three, as an Eightfold site on a company's own host does at any pace, failed almost every
  update that made a hundred requests or more. Such a site can now be followed.
- A server error (HTTP 5xx) or a network error is still asked again twice.
- A 429 is retried with longer waits, with the same identity and at the same pace, and when the
  retries are used up the source fails that update cleanly and keeps its last snapshot, as before.
  The engine never changes its User-Agent, its address or anything else to get past a refusal.
- `collect` reports how many 429 retries each source took, under `sources.rate_limited`
  (`collect.sources.rate_limited` in what `tick` prints), for the sources that took any, so a
  site's real rate can be read off a run. The counts are only shown, never stored.
- `sources test` and `sources probe` still ask once, to answer quickly, so a 429 there does not mean
  that `collect` will fail.

### Gem job boards

- New source type `gem`, added as `gem:<board>:Company` for a board whose page is
  `jobs.gem.com/<board>` (#86). It reads Gem's public Job Board API like a Greenhouse board: every
  posting with its text in one request an update. A reply that is not a whole board, or a posting
  without its id, title, link or text, fails the source instead of closing postings.
- `sources probe` reads a board's link, one posting's link (`jobs.gem.com/<board>/<id>`) or an
  `api.gem.com/job_board/v0/<board>/...` address, and prints the add line. A name probe tries Gem as
  well, with one more request for each name it tries. `sources hosts` lists `api.gem.com`.
- A posting's place is its location, then the places of its other offices, sorted; remote or hybrid
  work leads it, as in "Remote, London, United Kingdom". Its date is when it was first published,
  and its employment type is Gem's own, such as `full_time`, which the hard limits read.
- A job list's link to one Gem posting is read from Gem, and a posting on a Gem board the feed
  watches is judged once.
- Gem documents this API as public, so, like the other documented public board APIs, it is read
  without consulting robots.txt. Spike E14 in `docs/spikes.md` records what was checked.

### A probe of a Phenom career site reads its search-results page for the board behind it

- On the large employers' Phenom career sites checked for #79, every posting was applied for through
  the employer's Workday site, and the site's search-results page linked those postings. Its home
  page, which people usually paste, links none, so `sources probe <url>` named Phenom and said to
  open one posting and probe its Apply link. When it reads a Phenom page that links no board it
  reads, it now reads the site's search-results page too, and reports a board that page links to,
  such as a Workday site, with its add line (#87).
- That page's address comes from the site's settings on the page read (the `baseUrl` of `phApp`)
  when they name the same host, or else from the page's own `/<country>/<language>/` path, or
  `/global/en/`. The site's robots.txt is read first, and the page is not asked for when robots.txt
  disallows it or cannot be read: at most two more requests, a second apart, to the host of the
  page read, and only for a Phenom page.
- When that page links no board the probe reads, the advice from #78 stands.

### The probe knows an Avature portal by its page, on any host

- An Avature portal can be served from a company's own host, where `sources probe <url>`, which
  named Avature only by the avature.net host, found no link to a known career system. It now knows
  such a portal by its page: the `avature.portal.*` meta tags it carries, or the assets it loads
  from templates-static-assets.avacdn.net. It names Avature and, as for a Phenom site, says to open
  one posting and probe its Apply link, since Avature can also be only a front end (#88).
- Where the page also links a system the probe reads, that board is reported as before. Only those
  markers make a page a portal, not a mention of Avature in its text.
