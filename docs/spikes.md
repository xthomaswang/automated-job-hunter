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

## Still open

| Spike | Needs |
|---|---|
| E3: the same CLI invocation on a scheduled GitHub Actions runner | A run with a `CLAUDE_CODE_OAUTH_TOKEN` secret. The `ANTHROPIC_API_KEY` path has run on a private test feed: four runs measured at about $0.015 to $0.016 per posting assessed |
| E6: aggregator rate limits from runner IPs | The throwaway repo |
