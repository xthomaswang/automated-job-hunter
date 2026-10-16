# Phase 0 spike results

Environment: macOS 27.2 build 26B5101f, Xcode 27.0 (27A266a), Claude Code 2.1.296, 2026-10-13.
Fixtures were synthetic and lived outside this repo. Costs are what `--output-format json` reported
for that run on a subscription login, at API list prices.

## E2: headless orchestrator with named subagents

**Verdict: works, and the model stage needs no shell at all.**

The invocation that worked, run from the feed-repo root:

```sh
claude -p "<dispatch prompt>" --model haiku \
  --restricted --tools "Agent,Read,Write" --agents .hunter/agents.json --strict-mcp-config \
  --permission-mode dontAsk \
  --allowedTools "Agent" "Edit(./.hunter/work/results/**)" \
  --output-format json --no-session-persistence < /dev/null
```

| Question | Result |
|---|---|
| Are subagents defined in an `--agents` JSON file usable headless? | Yes. `subagent_stats.by_type` showed `hunter-triage`. |
| Is `"model": "haiku"` honored? | Yes. `modelUsage` listed `claude-haiku-5-5` for the subagents while the orchestrator ran on Sonnet. |
| Do 20 subagents run in parallel? | Yes. 20 spawned, 20 completed, 0 refused, 20 well-formed result files, 18 s wall. |
| Does `Write(./path/**)` scope writes? | **No.** It is not a valid rule; every write was denied. |
| Does `Edit(./path/**)` scope writes? | Yes. Writes under `results/` allowed, any other write inside the working directory denied. |
| Does `--restricted` confine file tools? | Yes. Reads and writes outside the working directory were denied, for the orchestrator and inside a subagent. |
| Can an allow rule narrow reads inside the working directory? | No. `Read(./.hunter/**)` as an allow rule still let `./tracked.txt` be read. |
| Can a deny rule narrow reads? | Yes. `--disallowedTools "Read(./profile/**)"` denied that directory. |
| Did an instruction injected into job text get followed? | No. The packet marked job text as untrusted; the verdicts were unaffected and no file was written outside `results/`. One fixture, so this is an example, not a guarantee. |
| Are denials visible to the caller? | Yes. The top-level `permission_denials` array included denials that happened inside a subagent. |

Not tested: project hooks (`--restricted` ignores project settings, and the rules above made a hook
unnecessary), Codex, and behaviour inside a Routine.

Measured overhead:

- Each subagent carries about 10k input tokens of fixed context, almost all served from cache.
- 20 five-job triage packets with a Haiku orchestrator cost $0.016 in total.
- A Sonnet orchestrator added $0.03–0.05 per invocation for the same trivial dispatch job, so the
  orchestrator should run on Haiku.

Design consequences:

1. Packets and a manifest are built by the engine before the model stage starts. The model stage has
   only `Agent`, `Read` and `Write`; there is no restricted `judge.py` script and no guard hook.
2. A tick has two model stages (triage, then assess) with an engine step (`hunter advance`) between
   them, instead of a `judge next` / `judge ingest` loop driven by the model.
3. Scoped write access is spelled `Edit(...)`. Reads are narrowed with deny rules.

## E4: file-read limits for packets

**Verdict: the 48 KB / 1,500-line packet cap holds.** Measured by a Haiku subagent reading files with
random markers at known positions; every reported marker matched the generator's manifest.

| File | Size | Lines | Default read |
|---|---|---|---|
| p40 | 40 KB | 398 | Complete |
| p60 | 60 KB | 596 | Complete |
| p48_narrow | 48 KB | 1,461 | Complete |
| p100 | 100 KB | 993 | Truncated at line 717: 29,448 tokens against a 25,000-token cap |
| lines2500 | 82 KB | 2,495 | Truncated at line 2,011: 26,370 tokens against the same cap |
| longline | one 5,844-char line | 3 | Complete, including a marker at character 5,831 |

- The limit is 25,000 tokens per read, not a line count. This synthetic text ran about 3.4
  characters per token, so 48 KB is roughly 14,000 tokens.
- An over-cap read returns a partial page with a notice explaining how to page. A subagent could
  answer from the partial page, so packets must stay under the cap rather than rely on paging.
- Long lines were not truncated in this version. Packets are still hard-wrapped, because other
  runners may differ and wrapping costs nothing once quote matching normalizes whitespace.

## Live boards: first evidence for the snippet heuristic

Phase 1's live fetch (one board per adapter, 364 postings) showed how often the role-section
pattern in `textutil.snippet` finds a section to anchor on instead of falling back to the top of
the posting:

| Board | Postings | Anchored on a role section |
|---|---|---|
| Greenhouse (Figma) | 143 | 142 |
| Ashby (Notion) | 130 | 124 |
| Lever (Saviynt) | 70 | 69 |
| Recruitee (bunq) | 21 | 4 |

The pattern is English-only and tuned to common headings; boards that use other headings or another
language fall back to the first characters of the posting. That is a known limit, and E1 measures
whether triage recall holds with it.

The same fetch showed a Greenhouse posting first published in January 2025 that is still listed,
which is why posting age never drops a live posting (see DESIGN.md, the funnel's stage 0).

## E1: triage recall by snippet size

**Verdict: go, with the 1,200-character snippet as the default.**

500 postings labeled by the old radar (its model read full descriptions under a deliberately broad
research policy), rebuilt from its raw snapshots with this engine's adapters so they have real line
structure, then triaged by Haiku subagents through the headless path from E2. Criteria: the old
policy's fifteen target phrases, six as wanted roles and nine as "also open to".

| Snippet | Core-role postings kept | "Adjacent" postings kept | Off-target postings dropped | Cost for 500 |
|---|---|---|---|---|
| none (title only) | 148 / 150 (98.7%) | 118 / 150 (78.7%) | 174 / 200 (87.0%) | $0.030 |
| 600 chars | 149 / 150 (99.3%) | 121 / 150 (80.7%) | 189 / 200 (94.5%) | $0.051 |
| 1,200 chars | 149 / 150 (99.3%) | 126 / 150 (84.0%) | 189 / 200 (94.5%) | $0.066 |

- No result line was invalid or missing across 40 packets, so the line format holds up.
- The one core-role posting dropped at 1,200 characters was a paid app-testing gig.
- The 24 "adjacent" postings dropped at 1,200 characters were 12 product-management roles, 3
  marketing or creative roles, and 9 others (analytics, data science, partnerships, strategy,
  operations, management). They carry the old label because the old policy was deliberately broad.
  One of the nine, a research-advisor role, is arguably a real miss.
- The snippet buys precision more than recall: 7.5 points more off-target postings dropped, which
  is assessment work saved.
- The role section was found in 391 of the 500 postings; the rest used the top of the posting.

Limits: one run per size, 150 to 200 postings per class (so each percentage is good to roughly
plus or minus 5 points), one field (UX research), and labels produced by another model. The built-in
canary keeps measuring this on real runs.

## E5: fingerprint threshold

**Verdict: re-judge when the simhash distance exceeds 12.**

Across five snapshots of the old database, 8,643 postings had a description that changed between
consecutive snapshots. Each pair was classed by word overlap and measured with this engine's
64-bit simhash.

| Change | Pairs | Distance 8 or less | 12 or less | 16 or less |
|---|---|---|---|---|
| Cosmetic (word overlap 0.9 or more) | 8,425 | 97.6% | 99.9% | 100% |
| In between | 145 | 17.2% | 58.6% | 86.2% |
| Large (word overlap under 0.7) | 73 | 2.7% | 27.4% | 46.6% |

- At a distance of 16 the noisiest old feed kept its verdict on 7,622 of 7,633 changed pairs, where
  the old design discarded every one. Moving to 12 re-judges about 60 more pairs across all sources.
- Most "large" changes at small distances were one employer adding the same 409-character block to
  every posting. Not re-judging those is the intended behaviour, so the bottom row overstates what a
  threshold misses.
- 12 rather than 16 costs almost nothing (7 extra re-judgments in 8,425 cosmetic edits) and catches
  more partial rewrites.

## A whole tick with real judges

Four live boards (364 postings), a synthetic three-year UX researcher profile limited to full-time
roles in the United States, system Python 3.9, the engine run from this repository, and the headless
invocation from E2 through `scripts/judge.sh`.

| Step | Result | Time | Cost |
|---|---|---|---|
| `collect` | 195 passed the hard constraints (157 dropped for location, 13 for employment), 7 triage packets | 5 s | none |
| Judge: triage | 17 kept, 178 dropped, none rejected or unanswered; 7 drops sampled for the triage check | 23 s | $0.027 |
| `advance` | 12 queued for assessment (the per-tick cap in this test), 3 packets | under 1 s | none |
| Judge: assess | 12 assessed, none rejected | 28 s | $0.198 |
| `apply`, `publish` | 2 published, 10 below the bar; one local commit of 14 files | under 1 s | none |

- No permission denial occurred in either judging stage.
- Assessment is the expensive part: about $0.017 per posting against $0.00014 per posting for triage.
- Of the 12 assessed, 2 were the wanted role, 3 related and 7 unrelated (data science, operations,
  strategy). Triage is tuned to keep when unsure, so a profile's "not wanted" list matters for cost.
- The first run of this test found three defects, fixed before these numbers were taken: two
  direct-role postings asking for seven years were scored 38 and hidden (a wanted role is now always
  listed), internships passed a full-time-only filter when the board gave no employment type (a
  title that says "Intern" now counts), and quotes stopped where a packet line wrapped (they are now
  completed to the end of the sentence).

The same tick was then run from a freshly scaffolded feed repo with one command,
`python3 .hunter/engine/hunter.py tick --no-push`, using only that repo's own engine copy: four
boards, 255 postings past the hard limits, 17 kept by triage, 8 assessed (the cap in that test), 3
published, a clean tree and one commit, in about a minute for $0.18.

## E8: Workday and SmartRecruiters, as they answer today

2026-10-14, macOS 27.2 build 26B5101f, Xcode 27.0 (27A266a), Claude Code 2.1.299. About twenty requests to
public endpoints by hand while writing the readers (#37). No model was involved.

**Workday** (one large tenant's public career site, `<tenant>.wd5.myworkdayjobs.com/<site>`):

- The site's own page loads its listings from `POST /wday/cxs/<tenant>/<site>/jobs` with
  `{"appliedFacets": {}, "limit": 20, "offset": n, "searchText": q}`. The cxs tenant is the host's
  first label. A row holds `title`, `externalPath`, `locationsText`, `postedOn` and `bulletFields`,
  but no text.
- `total` is right only on the first page; later pages report 0. This matches the old reader's
  assumption.
- The requisition id ends `externalPath` after the last `_` and is also `bulletFields[0]`. They
  agreed in all 20 rows checked.
- `locationsText` is either one place, such as `US, CA, Santa Clara`, or a count such as
  `2 Locations`. So stage 0 can read single places from the listing alone.
- The text is one more request per posting: `GET /wday/cxs/<tenant>/<site><externalPath>` returns
  `jobPostingInfo` with `jobDescription` (HTML), `location`, `additionalLocations`, `startDate`
  (ISO), `timeType`, `jobReqId`, `externalUrl`, `canApply` and `posted`.
- Search is broad: `user research` matched 441 postings, mostly unrelated research roles, and
  quoting the phrase changed nothing. `UX researcher` matched 1. Queries work best as role titles.
- Every listing also returns `facets`. One is `jobFamilyGroup` ("Job Category"), the site's own
  categories with ids and counts, such as Engineering 257 and Research 25 for that search. It
  can be sent back in `appliedFacets` to keep a search to those categories. Others are job type,
  time type and location.
- An empty search on that site reported exactly 2,000 postings, while its category counts add up to
  over 2,700: the total is capped at 2,000. Hence the reader refuses any search matching over 1,000
  rather than trusting a total near the cap.
- The bare host (`https://<tenant>.wd5.myworkdayjobs.com/`) answers 406, with an HTML or JSON
  Accept header alike, so the site name cannot be found from it.
- `robots.txt` on that host allows `/<site>/`, disallows a few other paths, and says nothing about
  `/wday/`. This is the only terms fact checked; each employer's site has its own terms.

**SmartRecruiters** (the documented Posting API):

- An unknown company id answers 200 with `totalFound: 0`, not 404, so a probe cannot tell an
  unknown company from one with no openings.
- A listing row has `id`, `name`, `releasedDate` (ISO), `typeOfEmployment.label`, and a structured
  `location` (`city`, `country`, `remote`, `fullLocation`), but no text. The text is in
  `GET .../postings/<id>` under `jobAd.sections`.
- `q=user experience` narrowed one large company from 5,027 postings to 936, so search is broad
  here too.
- `https://jobs.smartrecruiters.com/<company>/<id>` answers 200, so a posting's link does not need
  its detail.
- Company ids are case-insensitive: the lowercase id answers like the canonical one, and each row
  carries the canonical spelling in `company.identifier`.
- A live `collect` with one role query on one large company listed 280 postings. 240 were
  dropped for location on the listing alone, and the 40 left arrived with their text, in 23
  seconds.

## E9: Oracle Recruiting Cloud, and why there is no reader

2026-10-15, macOS 27.2 build 26B5101f, Xcode 27.0 (27A266a), Claude Code 2.1.299. Oracle's
documents below, and two requests to tenant hosts for their robots.txt, read by hand (#37). No
model was involved.

According to the notes for #37, a career site's own page lists postings with
`recruitingCEJobRequisitions` and probably reads each text from
`recruitingCEJobRequisitionDetails`, under `/hcmRestApi/resources/latest/` on the tenant's host.
Neither endpoint was called, because Oracle's own documents rule both out:

- [Recruiting CE Job Requisitions](https://docs.oracle.com/en/cloud/saas/human-resources/farws/api-recruiting-ce-job-requisitions.html),
  in the REST API for Oracle Fusion Cloud HCM (G29489-06, October 2026): "The
  recruitingCEJobRequisitions resource includes the list of the available requisitions. Note that
  the service endpoints in this resource category are only for Oracle internal use."
- [Recruiting CE Job Requisition Details](https://docs.oracle.com/en/cloud/saas/human-resources/farws/api-recruiting-ce-job-requisition-details.html),
  the category for the text: "Note that the service endpoints in this resource category are only
  for Oracle internal use."
- [Job Site Posted Jobs](https://docs.oracle.com/en/cloud/saas/human-resources/farws/api-job-site-posted-jobs.html),
  the resource Oracle documents for reading posted jobs: "The recruitingJobSitePostedJobs resource
  provides details about the publicly posted job requisitions. Note that the service endpoints in
  this resource category can only be used by approved Oracle Cloud Marketplace partners."
- Oracle's [Terms of Use](https://www.oracle.com/legal/terms/), section 7: "You agree not to use
  any robot, spider, scraper or other automated means to access the Site or any Oracle accounts,
  computer systems or networks without Oracle’s express written permission." Career sites are
  served from Oracle's own `oraclecloud.com` hosts.
- robots.txt settles nothing: one public tenant's host answers 404, and another's firewall answers
  403 (`Blocked by WAF4SaaS`). The terms decide.

So `sources probe` still names Oracle Recruiting Cloud as a system it cannot read, now with the
reason, and a job list's links to it are still skipped. Whether the listing's total could be
checked against its rows was not tested, because the answer would not change this. If Oracle
offers a public API for posted jobs, this is worth checking again.

## E10: iCIMS, and why there is no reader

2026-10-15, macOS 27.2 build 26B5101f, Xcode 27.0 (27A266a), Claude Code 2.1.299. Eight read-only
requests by hand (#37), at least 1.2 s apart, with no login and no cookie. No model was involved,
and no posting page was opened.

iCIMS career portals are at `careers-<company>.icims.com`. iCIMS's
[Terms of Use](https://www.icims.com/legal/terms-of-use/) ("Last modified: April 1, 2024") rule
out reading them automatically:

- They "govern your access to and use of icims.com, including any content, functionality and
  services provided on or through the website www.icims.com or its related domains and
  subdomains". A portal is such a subdomain.
- "Additionally, you agree not to: [...] Use any robot, spider or other automatic device, process,
  or means to access the Website for any purpose, including monitoring or copying any of the
  material on the Website."
- The only allowance is to "print or download one copy of a reasonable number of pages of the
  Website for your own personal, non-commercial use", which is not automated access. No terms
  written for portal visitors were found. A portal's branded page links to its employer's own
  terms, which differ by employer; iCIMS's terms cover the host either way.

robots.txt does not change this. Two public portals serve the same file: it names the portal's
`/sitemap.xml` and disallows only the referral, login, candidate, reminder and connect paths. But
robots.txt is a crawler directive, not a licence, as with LinkedIn and Indeed.

The listing would have met #37's completeness bar: a public portal's sitemap appeared to list every
posting (`/jobs/<id>/<slug>/job`), each with a `lastmod`. So the terms are the only reason.
`sources probe` names iCIMS with that reason, and a job list's links to a portal are still skipped.
Portals on a company's own domain serve the same iCIMS pages; reading them under another host name
would be the same access.

## E11: Eightfold, as it answers today

2026-10-15, macOS 27.2 build 26B5101f, Xcode 27.0 (27A266a), Claude Code 2.1.299. 26 requests by hand
to two public tenants and the vendor's site, most of them 15 seconds or more apart, then one run of
the reader and the probe (6 requests, one a second), while writing the reader (#37). No login, no
key, no cookies kept, no script run. No model was involved.

**Terms.**

- `robots.txt` on a public tenant (`<company>.eightfold.ai`), and the same file on a public tenant
  on its company's own host, has `User-agent: *` and `Disallow: /`, then `Allow: /careers`,
  `Allow: /api/apply` and `Allow: /api/pcsx` among others. The reader requests only
  `/api/pcsx/...` (for a site in the older layout, below, robots.txt and `/api/apply/...`), and the
  probe also `/careers`. The bare host redirects to `/home`, which is not
  allowed, so the probe asks `/careers` for a site's domain. A bare host that a user pastes is
  fetched as given, like any other page handed to the probe.
- The career site shows visitors no terms (its terms-of-service setting is off). Its legal links
  are the employer's privacy statement and the vendor's privacy policy.
- The vendor's Acceptable Use Policy (revised Aug 2025) covers "Eightfold's website and Services",
  and "a visitor to Eightfold's website is deemed to be a Customer". It does not mention automated
  reading. It forbids use "that disrupts or otherwise negatively impacts any aspect of the
  Services" and "efforts to gain unauthorized access to non-public information". The reader reads
  public listings, one request a second at most, on paths robots.txt allows.
- The same policy forbids users "to reverse engineer, copy, disassemble, or decompile the Services".
  Reading the JSON a public page loads is not that, but it is the clause to revisit if the vendor
  says otherwise. An employer's own host may carry its own terms.

**The listing.**

- The career page is an app whose settings sit in a hidden `<code id="pcsx-data">` element that
  begins `{"domain": "<company domain>"`. It loads
  `GET /api/pcsx/search?domain=<domain>&query=<q>&location=&start=<n>`. The older
  `GET /api/apply/v2/jobs` answered 403 `Not authorized for PCSX` on the same tenant.
- Without `domain`, the search answers 422 ("Missing data for required field"), so an entry names
  both the host and the domain.
- The reply is `{"status": 200, "data": {"positions": [...], "count": n, ...}}`. A row has `id` (a
  number), `name`, `locations` (the site's own place names), `standardizedLocations` (coded, such
  as `Berlin, BE, DE`), `postedTs` and `creationTs` (seconds since the epoch), `department`,
  `workLocationOption` (`onsite`, `hybrid` or `remote`), `atsJobId` and `positionUrl`
  (`/careers/job/<id>`). It has no text and no employment type.
- A page holds ten rows; `num=100` returned the same ten. `count` was the same on the first,
  second and last page of a 334-posting listing (33 pages of ten and one of four), and on a page at
  offset 500 of a 2,461-posting one.
- A page at offset 2,450 of that listing answered 429 three times, the last after a pause of two
  and a half minutes, while offset 500 and other searches answered in between. Hence the reader
  refuses any search matching more than 500 postings (50 pages) rather than rely on deep pages.
- Search is narrow: `product designer` matched 3 of 334 postings on one tenant and 15 of 2,461 on
  the other, by relevance (an empty search sorts by date). So the reader searches for the wanted
  roles instead of reading whole boards ten at a time.
- Place names spell the country out, as `<city>, <region>, <country>` on one tenant and
  `<country>, <region>, <city>` on the other. The coded form uses two-letter codes such as DE, which
  is also a US state, so the reader uses the names.

**Texts and links.**

- `GET /api/pcsx/position_details?position_id=<id>&domain=<domain>&hl=en` returns the row's fields
  plus `jobDescription` (HTML) and `publicUrl` (`https://<the site's host>/careers/job/<id>`). It
  also returns the site's own fields, named `efcustomText…`: a time type on one tenant, and
  employment type, work site, travel and others on the other.
- `/careers/job/<id>` answers 200 with a canonical link to itself, so a posting's link needs no
  detail.
- An unknown `<company>.eightfold.ai` answers 404 `Group ID not found`.

**Rate.**

- No rate is documented, and no reply carried a rate header or `Retry-After`.
- The tenant on eightfold.ai answered every request, some a second apart.
- The tenant on its own host answered 429 to two of its eleven requests that were not deep pages:
  one a minute after the previous request, and one a second after its careers page at the start of
  a later session. Two searches seven seconds apart both answered.
- The reader keeps to one request a second. A 429 is retried twice; after that the source fails
  that update and keeps its last snapshot.

**One run of the reader.** The probe found the tenant on eightfold.ai (334 postings) in one request.
One search there listed 3 postings and fetched 2 texts, each starting with the site's time type. The
probe of the tenant on its own host recognized the site from its page, and then its search answered
429. Both took 6 requests and 4.5 seconds.

**The older layout.** 2026-10-16, macOS 27.2 build 26B5101f, Xcode 27.0 (27A266a), Claude Code
2.1.299. Written with the reader (#75) from the layout as #75 describes it; every test reads
synthetic fixtures offline, and no model was involved. The live check, by hand: one public site on a
company's own domain: robots.txt allows /careers and /api/apply; a search for two words matched 13
postings; one posting's text was about 6 KB.

The `GET /api/apply/v2/jobs` that answered 403 on a tenant in the newer layout (above) is how a site
in the app's older layout lists its postings. Some sites on a company's own host run it:

- Its career page (`/careers`, and a posting's page `/careers/job/<id>`) has no `pcsx-data`. It
  embeds `<code id="smartApplyData">` instead: HTML-escaped JSON of about 27 KB holding the `domain`,
  the first 10 `positions`, their total `count`, and many UI settings. The probe knows a site by it
  and reads the domain from it.
- `GET /api/apply/v2/jobs?domain=<domain>&start=<n>&num=10&query=<q>&sort_by=relevance` returns the
  same UI settings, the search's `count`, and up to 10 `positions`, with no `status` or `data`
  around them. A row has `id` (a number) and `name` (the title, also in `posting_name`), `location`
  (`<city>,<country>`, no space after the comma) and `locations` (a list of the same), `department`
  and `business_unit`, `t_create` and `t_update` (seconds since the epoch), `ats_job_id` (the
  employer's requisition), `work_location_option` (`onsite`, `remote` or `hybrid`),
  `canonicalPositionUrl` (`https://<host>/careers/job/<id>`), and an empty `job_description`.
- `GET /api/apply/v2/jobs/<id>?domain=<domain>` returns one record with the same fields and its
  `job_description` in HTML.
- Its robots.txt is the file above: `Disallow: /`, then `Allow:` for `/careers`, `/api/apply`,
  `/api/pcsx` and a few others, and a sitemap.

What the reader makes of it, with the newer layout's rules: searches, pages of 10, every search's
total checked, at most 500 postings a search, texts on demand under `max_details`, one request a
second.

- A source in this layout says `"layout": "apply-v2"`, which the probe's add line
  (`eightfold:<host>/<domain>/apply-v2:Company`) carries. A source without it reads the newer
  layout as before, with no request more. Finding the layout anew on every update would cost a page
  request each time, and a site that changed layout would quietly be read another way; as it is,
  such a site fails, keeps its last snapshot, and is probed again.
- Every update reads robots.txt first and refuses the site unless it allows each listing request
  and every listed posting's text. The probe reads it before the one page of the listing it asks for.
- A posting's place is its record's places with a space after each comma ("Helsinki, Finland"), the
  country spelled out; its date is `t_create`; its link is `/careers/job/<id>` on the site's host, the
  same as `canonicalPositionUrl`. When its text is read, the record's places also go in
  `own_location`.

## E12: SAP SuccessFactors, as it answers today

2026-10-15, macOS 27.2 build 26B5101f, Xcode 27.0 (27A266a), Claude Code 2.1.299. 26 requests by hand
to four public career sites and three of the older board's hosts while writing the reader (#37), at
least a second apart, with no login, key or cookie, besides reading SAP's own documentation. No model
was involved.

SAP hosts two kinds of public career site.

- **The older job board**, `career<N>.successfactors.com/career?company=<id>` (also on
  `successfactors.eu` and `sapsf.com`). The robots.txt of three such hosts in different data centers
  is the same: `User-agent: *`, `Disallow: /`, `Disallow: /*company`, `Allow: /login`. So none of its
  pages was read, and no reader was built for it.
- **Career Site Builder** (Recruiting Marketing) sites, on the employer's own domain. Their robots.txt
  disallows `/services/`, `/talentcommunity/`, `/applybutton/`, `/preapply/` and a few other paths and
  allows the rest; it was the same on three sites. SAP's knowledge base article 2820583 says "All RMK
  pages are designed to be publicly accessible", "it is actually not possible to totally prevent
  search engines from scraping and indexing public content", and "As to the robots.txt, it is
  generated at a system level per Data center. As a result, it is not possible to customize it per
  site." The one site whose footer was checked linked only to its employer's own legal notice and
  privacy policy. Such terms vary by employer and were not checked one by one, as for Workday in E8.

What three public Career Site Builder sites answered:

- A search page, `/search/?q=<query>&startrow=<n>`, comes in one of three layouts:
  - a table: `<tr class="data-row">` rows holding the title's link and a `span.jobLocation`, the
    count `Results <b>1 – 25</b> of <b>8992</b>`, and links to pages by `startrow`, the last included;
  - tiles: `<li class="job-tile">` rows whose facts are `div`s with ids such as
    `job-<id>-desktop-section-location-value` (once per screen size), the count
    `Showing 1 to 25 of 25 Jobs`, and `data-record-returned` holding the number of tiles;
  - filled in by a script (`j2w.searchResultsUnify`) that posts to `/services/recruiting/v1/jobs`:
    the page itself has no rows and no count, and `/services/` is disallowed. Not read.
- 25 postings a page on every site. The second page of a 6,733-posting search was rows 26 to 50, with
  none from the first.
- Search matches any of the words anywhere in a posting. A two-word query matched 6,733 of a site's
  8,992 postings, and its second word alone 130, only 7 of the first 25 with the word in the title.
  The two words in quotes matched 121. Hence each query is sent as a phrase.
- A search that finds nothing shows `<div id="noresults">`, in both layouts.
- Rows carry the title, the posting's link `/job/<city-title-region>/<id>/` (under the brand's path on
  a brand's site, with the brand also in the page's settings), and the place; tiles also carry the job
  category and the other places. Neither site with rows showed a date in them.
- A posting's page carries schema.org microdata: `title`, the parts of its place, `description`, and
  `datePosted`. On both pages read, `datePosted` was the day of reading, though one posting was in a
  sitemap made five days before, so it is not used. Its `<link rel="canonical">` ends in the id.
- Every page sets up SAP's scripts (`j2w.init(...)`, files under `/platform/js/j2w/`), whose settings
  name the sign-in host (`career<N>.successfactors.com`) and the brand. The probe knows a site by them.
- `/sitemap.xml` lists every posting's link (the same 25 ids as the search, on one small site). SAP's
  article 2757876 says "The system updates these Sitemaps every week", and the 406 entries of another
  site's all carried one date five days old. It has no titles or places either, so it is not the
  listing.
- One public site answered its search with a script-and-cookie challenge (HTTP 403), and its sitemap
  had another system's ids and paths. It no longer runs on Career Site Builder and was not read further.
- SAP's course on Career Site Builder describes a standard XML feed that lists all of a customer's
  current jobs each time it is read, but its address is given privately to job boards, so it is not
  a public listing.
- One search page read through the engine's own client (its usual headers) answered as by hand.

## E13: Radancy TalentBrew career sites

2026-10-15, macOS 27.2 build 26B5101f, Xcode 27.0 (27A266a), Claude Code 2.1.299. Written with the reader
(#37) from the vendor's structure as the notes for #37 describe it, with no request to a real site. The
examples below are synthetic, like the fixtures in `tests/fixtures/radancy/` that every test reads
offline. No model was involved.

TODO(main): live check. One public TalentBrew site: its robots.txt as served (the paths it disallows,
any Allow, wildcard or Crawl-delay line, and whether it names `/sitemap.xml`); the sitemap's size,
whether it is an index, and its entries by kind; a posting page's JSON-LD fields and `data-job-id`;
what a closed posting's address answers; and one run of the reader and the probe (requests, time).

A TalentBrew career site is on the employer's own host, such as `careers.acme.com`. Its paths may
start with a language segment (`/en/`) or not. Every page loads the vendor's assets from
`tbcdn.talentbrew.com` and `cdn.radancy.eu`, which is how the probe knows a site by its page: a link
in a posting's shape alone proves nothing.

**robots.txt** disallows the search, category, location and employment pages, such as
`/search-jobs`, `/en/search-jobs`, `/location/`, `/category/`, `/employment/` and `/job_location/`, and
names `Sitemap: https://careers.acme.com/sitemap.xml`. So the reader never reads those pages. Each
update reads robots.txt first and refuses the site if it disallows the sitemap or any posting.
The engine reads the rules itself (`ajh/robots.py`), as RFC 9309 says: the groups naming its product
token (`automated-job-hunter`), or else every group for `*`, merged; the longest matching pattern
wins, Allow on a tie; `*` matches any run of characters and a final `$` the end; an empty Disallow
matches nothing. `urllib.robotparser` answers differently by Python version. On 3.9.6 here it
applies the first matching rule and reads `*` and `$` as plain characters, so `Allow: /` followed by
`Disallow: /en/job/` allows the postings, which the RFC disallows. It also rewrites a rule's path
through urlparse and urlunparse, so `Disallow: /?` (the root with a query string) blocks `/about`
and every other page there, while the 3.13.15 here reads both files as the RFC does.

**The sitemap** is a sitemaps.org 0.9 `<urlset>` (an index of them, `<sitemapindex>`, is read too,
only its sitemaps on the site's own host). A posting's `<loc>` is
`https://<host>[/<lang>]/job/<city-slug>/<title-slug>/<org id>/<job id>`, such as
`https://careers.acme.com/en/job/springfield/retail-store-associate/1234/55500011122`.

- Slugs are lowercase and hyphenated; non-ASCII ones are percent-encoded UTF-8, so a Chinese title
  reads `%E9%94%80%E5%94%AE%E4%BB%A3%E8%A1%A8` (销售代表).
- Every `<lastmod>` is the time the sitemap was made, the same for every entry, so it says nothing
  about a posting's age and is not read.
- The same sitemap lists the home page and the category, location, employment and business pages,
  such as `/en/category/retail-jobs/1234/8899/1`. They are skipped and never fetched.
- A closed posting drops out of the sitemap, so its absence closes it, as on other boards.
- The job id is the posting's id; the org id is the site's and is not part of it.

**A posting's page** embeds one `<script type="application/ld+json">` holding a schema.org
`JobPosting`: `title`, `datePosted` (`2026-10-01`), `employmentType` (`"Full-time"`, `"Part-time"`),
`identifier` (`{"@type": "PropertyValue", "name": "Job ID", "value": "100200"}`, the employer's
requisition number, which is not the link's job id), `hiringOrganization.name`, `jobLocation` (a list
of `Place`s whose `address` has `addressLocality`, `addressRegion` and `addressCountry`, a name or a
code such as `USA`, besides street and postcode), and `description` (HTML, often 10 to 15 KB). Its
HTML also carries `data-org-id="1234"` and `data-job-id="55500011122"`.

What the reader makes of it:

- The listing gives only slugs, so a posting's title and place come from them, the same on every
  update: the change fingerprint never depends on whether its text was read. The place is the city
  (`springfield` is "Springfield"). The page's title (real capitals and punctuation) replaces the
  slug's only when their words are the same; "Assistant Manager, Store Operations" under the slug
  `assistant-store-manager` keeps "Assistant Store Manager".
- A bare city name is read through the bundled table of cities, so stage 0 reads `london` as the
  United Kingdom on the link alone. A posting in a US town named like a city in that table (Dublin,
  Ohio; Vancouver, Washington), or like a country (Lebanon, Pennsylvania), would be left out the same
  way when `locations.regions` is set.
- The page's address goes to the posting's own place ("Springfield, IL, USA"), which parks a posting
  outside the wanted regions; it and the employment type are also the first lines of the text.
- The employment type is not the posting's `employment_type`: the sitemap has none, so stage 0 would
  drop a part-time posting only on the updates that read its text, and on the others it would look
  new and have its text fetched again, every update.
- A page counts as this posting's only when the address it landed on ends in the posting's id, an
  element carries that id, and it has a JobPosting with a description. Otherwise it is skipped this
  update, like any failed text.
- More than one posting link in twenty that is not in the shape above (one stray link always may)
  fails the source, as does a sitemap that cannot be read or a sitemap of an index that fails.

## Still open

| Spike | Needs |
|---|---|
| E3: the same CLI invocation on a scheduled GitHub Actions runner | A run with a `CLAUDE_CODE_OAUTH_TOKEN` secret. The `ANTHROPIC_API_KEY` path has run on a private test feed: four runs measured at about $0.015 to $0.016 per posting assessed |
| E6: aggregator rate limits from runner IPs | The throwaway repo |
