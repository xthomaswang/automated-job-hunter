# automated-job-hunter: an installable, agent-driven job radar

## Context

`uxr-job-radar` proved the idea: fetch public job boards, let a model judge relevance, publish a
feed repo that people and agents job-hunt from. It does not scale beyond one person:

- It depends on one laptop being awake. The feed has been frozen since 2026-10-12, local inference
  has been off since Oct 6, and almost all judgments came from manual Colab batches.
- Every posting gets a full-description model review: about 22,000 per full fetch, 87% off-target.
  That is tolerable on a local model and unaffordable on Claude or Codex.
- Noisy aggregators change descriptions every fetch, which wipes cached verdicts
  (`store.py:99-101`); about 9,000 live jobs are unassessed as a result.
- The published file is rewritten hourly with volatile timestamps (about 0.5 MB of git growth per
  hour) and has no posting text, numeric score, tombstones or schema version.
- It is deliberately anonymous and hard-wired to UXR and to this machine.

The new project keeps the pattern and changes who does the work. Anyone installs a skill into
Claude Code (or Codex), runs setup once, and gets a private GitHub repo that updates every 1/4/8
hours with jobs matched to their experience. Cheap subagents do the judging; a deterministic engine
does everything else.

## Decisions

| Topic | Decision |
|---|---|
| Name | `automated-job-hunter` for the plugin, skill and repo, built in `~/Developer/projects/automated-job-hunter/` |
| Default mode | Private repo, personal matching. A contact-free profile distilled from the résumé is committed to the user's private repo. Public/anonymous mode is deferred. |
| Default scheduler | GitHub Actions in the user's feed repo. Claude Code Routines and a local timer are documented alternatives behind the same commands. |
| Tool support | Claude Code tested end to end. Codex gets the same skill plus its own subagent definitions and workflow, smoke-tested only. |

## How it works

Two repos:

- **Plugin repo** (`automated-job-hunter`, public): the skill, subagent definitions, the engine, and
  a template for feed repos.
- **Feed repo** (one per user, private, default name `job-feed`): profile, sources, compact state,
  the published feed, and a pinned copy of the engine so scheduled runs are self-contained.

**Inversion of control.** The old code called a model. Now the agent orchestrates and calls a
deterministic engine; the engine never calls a model API (the local `hunter tick` wrapper may launch
the agent CLI, nothing more). Fetching, diffing, rendering and git are plain Python, so most
scheduled ticks use no model at all, and the same engine works under any runner.

Skill verbs: `setup` (below), `run` (one full tick in the current session), `status`, `tune` (edit
the profile or criteria conversationally, preview with `hunter profile check` what would be
re-judged, commit), `upgrade` (re-vendor the engine into a feed repo).

### One tick

```
collect ──► judge: triage ──► advance ──► judge: assess ──► apply ──► publish
engine      subagents         engine      subagents         engine    engine
```

| Step | Writes to | What it does |
|---|---|---|
| `collect` | untracked `.hunter/work/` only | Fetch boards, normalize, group duplicates, diff against committed state, apply hard constraints, build the queue, write triage packets and a manifest, emit a digest seal |
| judge | `.hunter/work/results/` only | An orchestrator reads the manifest and hands each packet to a subagent. Runs once per stage and is skipped when the stage has no packets. |
| `advance` | untracked `.hunter/work/` only | Validate triage results, fetch lazy details for kept jobs, write assess packets and a new manifest |
| `apply` | tracked files | The single mutation point. Checks the tracked tree is pristine and the seal holds, validates assess results, folds verdicts into state, updates the feed incrementally. Always runs, so a judge step that was cut off still lands its partial progress. |
| `publish` | git | Audit, commit generated paths and state only when something material changed, push. Never force-pushes; a stale base aborts instead of rebasing. |

### The funnel

| Stage | Who | Sees | Decides |
|---|---|---|---|
| 0 | engine | every posting | Drops only on positive evidence against the user's explicit hard constraints (excluded companies, employment type, location/remote). A title that says "Intern" counts as evidence of an internship. Unknown passes. No relevance regexes. A posting's age never drops it: boards keep postings open for a year or more, so age only orders the queue (newest first) and is shown in the feed. |
| 1 Triage | Haiku subagent | title, company, location, department, and a ~1,200-char description snippet anchored at the role section; about 33 per packet at that snippet size (the 48 KB cap), 45 at most | `keep`, `drop`, or `drop_title` (unrelated whatever the duties). Only `drop_title` is cached, and only when every posting with that title got it. One in fifty drops, chosen by the posting's key, is sent to stage 2 as a recall canary, and the miss rate is shown in the README. |
| 2 Assess | Sonnet subagent | full posting, up to 5 per packet | Fit 0–100, role match, seniority, required/preferred years, 1–4 matches and 0–4 gaps each with a verbatim quote, quoted notes. Tier is computed in code: a posting whose work is a wanted role is always listed (as a stretch when the fit is low), related work is listed from `publish_min` up, and unrelated work never. |

Why triage sees a snippet: measured on 500 postings labeled by the old radar (spike E1 in
[spikes.md](spikes.md)), Haiku kept 149 of 150 core-role postings at every snippet size, and the
snippet mainly bought precision: 94.5% of off-target postings dropped with a snippet against 87% on
titles alone. Later postings repeat an earlier title only about 25% of the time, so a title cache
saves little; the saving comes from roughly 400 tokens per job on Haiku instead of roughly 2,000 on
a larger model. Measured cost: about $0.13 per 1,000 postings triaged.

Per-run caps (`max_triage` 1,500, `max_assess` 100) with a persistent priority queue (newest and
preferred companies first) let a first backfill drain over several ticks.

**First backfill.** By default every posting found on the first run is judged, newest first, at the
per-tick caps, so a large backlog drains over several ticks. A live test showed why age cannot be
the default limit: on four real boards, 138 of the 205 postings that passed the hard constraints
were first published more than 45 days earlier and were still open. To cap the first run's cost,
set `backfill.days`: postings with a known date older than that are then parked when a source is
first read, meaning recorded in state but not judged or published. The README counts them and
`hunter requeue --parked` judges them later. A posting with no date is never parked, and after a
source's first snapshot every newly listed posting is judged whatever date it carries.

**Lazy details.** Workday and SmartRecruiters need one request per posting for the description (one
old source alone took 937). The engine fetches a detail only for ids not yet in state, for ids
queued for assessment this tick, and for known ids whose listing fields changed, under a per-tick
request budget.

**Measured cost.** At API list prices (Haiku 5.5 $0.10/$0.50 and Sonnet 5.5 $2/$10 per million
input/output tokens, price table dated 2026-10-06), triage costs about $0.14 per 1,000 postings and
assessment about $0.017 per posting, orchestrator included (see [spikes.md](spikes.md)). A fully
capped tick of 1,500 triaged and 100 assessed is therefore about $2; a steady-state tick with a few
dozen new postings is a few cents, and most ticks are fetch-only and cost nothing. On a
subscription token the same work draws on plan usage instead.

### Packet protocol (generalizes `uxr_radar/batch.py`)

- The engine writes self-contained packets: instructions, the profile brief, the jobs (marked as
  untrusted data), and the required output format, using packet-local ids and a nonce. A manifest
  lists each packet, its result path and which subagent takes it.
- Packets are hard-wrapped Markdown sized by bytes (48 KB / 1,500 lines at most). The measured read
  limit is 25,000 tokens per call, and an over-limit read returns a partial page that a subagent
  could answer from (see [spikes.md](spikes.md)).
- Results are line-oriented text, not JSON. Invalid JSON was 60% of the old model failures, and
  verbatim quotes containing quote characters make it worse.
- The orchestrator hands each subagent only "process packet X, write the result to Y, reply with a
  one-line count". Posting text never enters the orchestrator's context, and the orchestrator holds
  no state: "done" means the result file validates per id.
- `advance` and `apply` validate every verdict: ids, enums, quotes are verbatim substrings of the
  posting (after Unicode and whitespace normalization), year numbers appear in their quote. Missing
  ids are re-issued at no charge; invalid ones are re-queued with feedback from a fixed vocabulary;
  after 3 attempts the job is published as an unscored, source-only record. Re-issues happen on the
  next tick, or in the same session when a person runs the skill interactively.
- Published quotes are the source's own span, not the model's rendering, completed to the end of
  the sentence when the model stopped at a line wrap. URLs never pass through the model. Sponsorship and citizenship flags come from fixed rules over the posting text.

### Trust boundary for the model stage

The judge stage has no shell, no network tools and no git credentials (`persist-credentials:
false`). Its whole tool set is `Agent`, `Read` and `Write`. Three layers enforce the boundary, the
first two verified headless in [spikes.md](spikes.md):

1. `--restricted --tools "Agent,Read,Write"` removes every other tool and confines file access to
   the feed-repo checkout, for subagents too.
2. Under `--permission-mode dontAsk`, the only write rule is `Edit(./.hunter/work/results/**)`
   (`Write(...)` is not a valid scoped rule). Deny rules keep reads out of `.git/`.
3. `apply` checks the tracked tree is pristine and the seal over the snapshot and packets holds.

Free-text fields are length-capped and stripped of URLs and secret-shaped strings. A posting that
attempts prompt injection can at worst skew verdicts within that run.

## Plugin repo layout

```
automated-job-hunter/
├── .claude-plugin/{plugin.json, marketplace.json}   # repo root is marketplace and plugin
├── .codex-plugin/plugin.json
├── agents/{hunter-triage,hunter-assess}.md          # copies of assets/agents/claude, equality tested
├── skills/automated-job-hunter/
│   ├── SKILL.md                    # router: setup | run | status | tune | upgrade
│   ├── references/{setup,run,schedule,troubleshooting}.md
│   ├── scripts/{hunter.py, ajh/}                    # stdlib-only engine
│   └── assets/
│       ├── agents/{claude/*.md, codex/*.toml}       # haiku triage, sonnet assess; Read + Write only
│       ├── template/               # feed-repo scaffold: workflow, AGENTS.md, HANDOFF.md, RUNBOOK.md, settings
│       └── seeds/{registry.json, places.json}       # known boards; small location alias table
├── tests/{fixtures/, test_*.py}    # pytest, dev only
├── pyproject.toml (dev tooling via uv), AGENTS.md, README.md, LICENSE, docs/
```

The engine is stdlib-only Python (3.9+), so it runs with no install step on GitHub runners, Routine
VMs, Codex sandboxes and macOS system Python. It is a port of the old modules, not a copy: `urllib`
with a thread pool replaces httpx, `html.parser` replaces bs4, hand-written validators replace
pydantic, committed text files replace SQLite.

Engine modules (`scripts/ajh/`): `model` (Job, keys, path-safe ids), `textutil` (HTML to text,
normalization, wrapping, simhash, snippet), `http` (retries, per-host delay, injectable transport),
`sources/` (ATS adapters, aggregators, lazy details, probing, registry), `state`, `criteria`
(schema, stage-0 filters, profile hashes), `rules` (flag rules, years, seniority), `queue`,
`packets`, `verdicts`, `render`, `publish`, `audit`, `scaffold`, `cli`.

### CLI (stdout is one JSON object; exit 0 ok, 2 needs a human)

| Command | Purpose |
|---|---|
| `hunter --repo D init [--cadence 1h\|4h\|8h]` | Scaffold a feed repo from the template and vendor the engine |
| `hunter sources list` / `add` / `remove` / `test` | The bundled registry of known boards, and which ones this feed watches |
| `hunter sources probe "Company"` | Try slug candidates against public ATS endpoints; report matches |
| `hunter sources hosts` | List job-board hosts (for a Routine's network allowlist) |
| `hunter audit` | Check the profile and config for contact details and credentials |
| `hunter collect [--github-output]` | Stage 0 and triage packets; reports new/changed/closed, `triage_packets`, `seal` |
| `hunter advance [--github-output]` | Validate triage results, fetch lazy details, write assess packets; reports accepted/invalid/missing, `assess_packets` |
| `hunter apply --seal X` | Validate assess results, fold verdicts into state and feed; report added/closed/rescored |
| `hunter publish [--dry-run]` | Audit, commit, push; reports `published`, `unchanged`, `stale_base` or `blocked` |
| `hunter tick [--no-push]` | The whole tick locally with headless Claude Code, for a terminal or a timer |
| `hunter status`, `upgrade [--cadence]` | Health and counts; replace the feed repo's engine copy |

Not built yet: `hunter profile check` (report what a profile change would re-judge before committing
it), `hunter requeue --parked`, and a Codex runner for `tick`.

## Feed repo layout and published contract

```
<user>/job-feed (private)
├── README.md                    # dashboard: new this week, tiers, source health, recall canary
├── positions.jsonl              # open matched jobs; sorted by key; no observation times
├── jobs/<source>/<id>.md        # per-job brief: facts, assessment, posting text
├── feed/latest.md               # digest of the latest material update
├── feed/changes/YYYY-MM.jsonl   # append-only added / closed / rescored events (tombstones)
├── feed/meta.json               # schema_version, as_of (day), counts, source staleness
├── HANDOFF.md, AGENTS.md, CLAUDE.md   # contract and rules for downstream agents
├── profile/{profile.md, criteria.json}
├── config/{hunter.json, sources.json}
├── state/{meta.json, sources.json, profile.json, titles.tsv, jobs/<source>.tsv}
├── .hunter/{VERSION, MANIFEST.sha256, RUNBOOK.md, agents.json, engine/}   # pinned engine; work/ is gitignored
├── .claude/agents/, .codex/agents/       # the same subagents for Routines, interactive use and Codex
└── .github/workflows/job-hunter.yml
```

The engine is vendored but the skill is not, so there is no name collision with the installed
plugin and no symlink. The scheduled prompt is path-based ("read `.hunter/work/manifest.md` and hand
each packet to its subagent") and the headless run loads its subagents from `.hunter/agents.json`,
so nothing depends on skill or settings discovery. Inside a feed repo the plugin skill delegates to
`.hunter/engine`. The subagent definitions have one source in the plugin; the Markdown, JSON and
TOML forms are generated from it and checked by a test.

`positions.jsonl` record:

```json
{"v":1,"key":"figma:5551234","source":"figma","source_kind":"official","company":"Figma",
 "title":"…","location":"…","employment":"full_time","url":"…","posted":"2026-10-08",
 "first_seen":"2026-10-09","status":"scored","tier":"strong","fit":82,"role_match":"direct",
 "seniority":"mid","required_years":3,"preferred_years":null,
 "flags":{"sponsorship_not_offered":false,"citizenship_required":false},
 "why":"…","matches":[{"req":"…","quote":"…"}],"gaps":[],"notes":[],
 "variants":[{"key":"…","location":"…","url":"…"}],"also_on":[],
 "brief":"jobs/figma/5551234.md","judged":{"assess":"a7","rubric":"assess-v1"}}
```

`profile/criteria.json`:

```json
{"schema_version":1,
 "targets":{"roles":[],"adjacent":[],"avoid":[]},
 "seniority":{"min":"junior","max":"senior","years":2},
 "employment":["full_time"],
 "locations":{"remote":"any","regions":["US"],"cities":[]},
 "eligibility":{"needs_sponsorship":true,"rule_packs":["us"]},
 "companies":{"prefer":[],"exclude":[]},
 "thresholds":{"strong":75,"good":55,"publish_min":40},
 "caps":{"max_triage":1500,"max_assess":100,"max_attempts":3},
 "backfill":{"days":null}}
```

## State and change detection

Scheduled runs start from a fresh clone, so state is committed text: compact, sorted, low-churn.

- `state/jobs/<source>.tsv`: `id, status, first_seen, gk, tfp, dsh, fit, tg, ag, attempts, miss, note`.
  Only jobs that passed stage 0 are stored. No descriptions, no per-run timestamps. Status is one
  of pending triage, dropped, kept, published, below threshold, unscored, variant, parked or
  tombstone; `tg` and `ag` are the profile generations a verdict was made under.
- `collect` never edits these files. It writes the proposed next state to
  `.hunter/work/state-next/`, and `apply` adopts it.
- **Fingerprint.** `gk` hashes company and title (postings that may be the same opening). `tfp`
  hashes the normalized title, company and location. `dsh` is a 64-bit
  simhash of the description, frozen when the job is judged. A job is re-judged only when `tfp`
  changes or the simhash distance exceeds 12. Measured across five old snapshots (spike E5): 99.9%
  of 8,425 cosmetic edits stay at 12 or below, and real rewrites land at 26–32.
- **Published records are state.** A source that was skipped or failed this tick has no descriptions
  available, so the feed is updated incrementally and `positions.jsonl` plus the briefs are the
  canonical store of matched-job facts. README is re-rendered from them.
- **Closing a job.** Two consecutive misses close it, with a 14-day tombstone so a posting that
  returns keeps its triage verdict (a published one is assessed again, because its details went
  with the record). Rolling feeds (Remote OK, Jobicy) expire by age, never by absence. A source that
  shrinks by more than 40% is held until the shrink repeats. Changing a query-scoped source's
  queries re-baselines it without closures. A failed fetch keeps the previous snapshot.
- **Duplicates.** An aggregator copy of an opening an employer's own board lists (same company and
  title) defers to the official posting. Within one source, postings with the same company and
  title and near-identical text defer to one representative. Variants are recomputed every tick;
  when a representative closes, a variant takes over and is judged.
- **Profile changes.** Stage 0 is re-run on every tick, so a change to the hard limits costs
  nothing. Two hashes decide what else is judged again: the triage hash covers `targets` and
  `seniority`, and the assess hash covers the triage hash plus the profile text, `eligibility`,
  `locations` and `employment`. A target or seniority change therefore re-asks triage for every
  dropped posting and every assessment; a profile-text or preference change re-asks every
  assessment. Excluded companies are a stage-0 filter, so excluding one costs nothing. Thresholds,
  caps and preferred companies invalidate nothing. Stale verdicts stay published until refreshed.
- **Churn.** The only volatile datum is a day-granular `as_of`, so a quiet day produces at most one
  heartbeat commit.

## Scheduling

Default: `.github/workflows/job-hunter.yml`, cadence 1 h / 4 h / 8 h chosen at setup (default 4 h).

```yaml
on:
  schedule: [{cron: "17 */4 * * *"}]          # minute randomized at setup
  workflow_dispatch: {}
  push: {branches: [main], paths: ["profile/**", "config/**"]}
permissions: {contents: write}
concurrency: {group: job-hunter, cancel-in-progress: false}
jobs:
  tick:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@v4
        with: {persist-credentials: false}
      - id: collect
        run: python3 .hunter/engine/hunter.py collect --github-output
      - if: steps.collect.outputs.judge_needed == 'true'
        run: npm i -g @anthropic-ai/claude-code@X.Y.Z      # pinned to the version in spikes.md
      - name: Judge (triage)
        if: steps.collect.outputs.triage_packets != '0'
        timeout-minutes: 15
        continue-on-error: true
        env: {CLAUDE_CODE_OAUTH_TOKEN: "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"}
        run: sh .hunter/engine/judge.sh
      - id: advance
        if: steps.collect.outputs.judge_needed == 'true'
        run: python3 .hunter/engine/hunter.py advance --github-output
      - name: Judge (assess)
        if: steps.advance.outputs.assess_packets != '0' && steps.advance.outputs.assess_packets != ''
        timeout-minutes: 20
        continue-on-error: true
        env: {CLAUDE_CODE_OAUTH_TOKEN: "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"}
        run: sh .hunter/engine/judge.sh
      - if: always() && steps.collect.outcome == 'success'
        env: {GH_TOKEN: "${{ github.token }}"}
        run: |
          python3 .hunter/engine/hunter.py apply --seal "${{ steps.collect.outputs.seal }}"
          python3 .hunter/engine/hunter.py publish
```

- `judge.sh` holds the one headless invocation, verified locally in [spikes.md](spikes.md), so CI
  and a local `hunter tick` run the same command:

  ```sh
  claude -p "Read .hunter/work/manifest.md and hand each packet to its subagent, as .hunter/RUNBOOK.md describes." \
    --model haiku --restricted --tools "Agent,Read,Write" --agents .hunter/agents.json \
    --strict-mcp-config --permission-mode dontAsk \
    --allowedTools "Agent" "Edit(./.hunter/work/results/**)" --disallowedTools "Read(./.git/**)" \
    --output-format json --no-session-persistence < /dev/null
  ```

  The orchestrator runs on Haiku because its job is only dispatch; the subagents carry their own
  models. `anthropics/claude-code-action` is the fallback if spike E3 shows the direct CLI is a
  problem on a runner.
- Checkout leaves no credentials on disk. `hunter publish` reads `GH_TOKEN` from its environment and
  passes it to that one `git push` only; it is never written to `.git/config`.
- Pushes made with the workflow token do not trigger workflows, so the `push` trigger fires only
  when a person edits the profile or config. That is intended.
- Source failures exit 0; only auth or audit failures fail the run.
- The workflow token cannot edit workflow files, so `upgrade` runs locally.
- Constraints to state in the docs: GitHub cron is best-effort and can be delayed; hourly cadence on
  a private repo can approach the free Actions-minutes quota, which is why 4 h is the default.

Alternatives using the same commands:

| Option | How | Caveats |
|---|---|---|
| Claude Code Routine | Prompt: read the runbook, run the full tick, push to `main` | 1 h minimum; research preview; the cloud environment's network must allow the hosts from `hunter sources hosts`; plugins do not load there (hence the vendored engine); the session runs the engine itself, so the tool restrictions of the headless judge stage do not apply; subagents there are unverified, so the runbook falls back to inline packets |
| Local timer | launchd/cron running `hunter tick` | Pauses when the machine sleeps |
| Codex | `codex exec` or `openai/codex-action` with the same runbook; TOML subagents on a cheap OpenAI model | API key only in CI; no Haiku/Sonnet; smoke-tested only |

## Setup flow (`/automated-job-hunter setup`)

1. Read the résumé the user points to; interview for target roles, seniority, locations/remote,
   employment types, sponsorship needs, companies to target or avoid, cadence.
2. Write `profile/profile.md` (contact-free matching brief) and `criteria.json`; the raw résumé is
   never committed. `hunter audit` checks the profile for emails, phone numbers and profile URLs.
3. Build `config/sources.json` from the bundled registry, plus `hunter sources probe` for companies
   the user names. Aggregators are opt-in.
4. `hunter init`, then `gh repo create --private` after checking `gh` has the `workflow` scope.
5. Run one bounded tick in the session so the user sees results immediately; push.
6. Print the two commands for the user to run in their own terminal (`claude setup-token`, then
   `gh secret set CLAUDE_CODE_OAUTH_TOKEN`), so the token never enters the agent's context.

## Reuse from uxr-job-radar

| Old module | Ported (logic and tests, rewritten on the standard library) | Dropped |
|---|---|---|
| `core.py` | Quote and years validation (`:95-134`), `explicit_zero_experience`, `experience_level`, `title_seniority`, `public_https`, `digest`; `text_content` reworked; `messages()` becomes packet templates | `MODEL_ALIASES`, `link_result`, `anonymous_policy` |
| `sources.py` | `feed_url`, `parse_feed` (Greenhouse, Ashby, Lever, Recruitee); SmartRecruiters, Workday, Amazon with profile-derived queries and lazy details | `fetch_google` |
| `aggregators.py` | All four providers (Remotive, Remote OK, Jobicy, Himalayas) with their pagination, identity and count checks; `membership_mode` | `company_kinds` |
| `store.py` | `try_lock`; interval-claim and snapshot semantics (`:73-104`) re-expressed over text state | SQLite, queue tables |
| `pipeline.py` | `posting_flags` and its regexes, `company_key`, `load_exclusions`, range logic, age and location formatting, render skeleton | `infer`, `process_task`, `priority()` regexes, `verify_links`, `FAANG_PLUS`, UXR sections, `FEED_URL` |
| `publish.py` | `Git`, `Skip`, private-index stage-and-audit, compare-and-swap commit, `unpushed`, `source_guard`, material-change fingerprint (`:62-65`) | 6-hour refresh commits |
| `batch.py` | Re-validate-on-import and its outcome counts, as the result validation in `advance` and `apply` | SQLite transport |
| `scripts/audit_public.py` | Credential patterns, denylist matching | Anonymity and history scans |
| `service.py`, `screen.py`, `launchd.py`, notebook | Nothing | All |

Only tracked tests and fixtures are ported. Nothing from the old repo's ignored `state/` is copied
into the public plugin repo.

## Phases

Each phase ends in something runnable and tested.

| Phase | Deliverable | Verification |
|---|---|---|
| 0 Spikes | Answers to E1–E4 below; repo scaffold (git, manifests, uv dev tooling, contributor rules) | Written results in [spikes.md](spikes.md); go/no-go on snippet size, packet size, and tool-rule syntax. All go; only E3 (a scheduled run on a GitHub runner) is still open. |
| 1 Core + ATS | `model`, `textutil`, `http`, `rules`, four ATS adapters | Ported adapter, experience and integrity tests on Python 3.9 and 3.13; live fetch of three boards |
| 2 State + stage 0 | Text state, aggregators, grouping, `collect` end to end | Two-fresh-clone test covering new, changed, closed, revived, gating and the shrink breaker; replay of the old DB snapshots shows near-zero re-judges on a noisy aggregator (needs spike E5) |
| 3 Packets + verdicts | Packet and manifest writing, `advance`, result validation, rubric templates | Scripted fake runner feeding malformed, partial, wrong-nonce and injected results; cut-off and resume |
| 4 Apply + publish | `apply`, `render`, `publish`, `audit` | Ported publisher tests on temp git repos; a no-op tick makes no commit; a stale base aborts |
| 5 Skill + agents | Plugin installable; interactive `/automated-job-hunter run` | End to end on a sample profile; headless `claude -p`; a canary packet that tries to read outside the work dir and write outside results is denied |
| 6 Setup + Actions | Interview, scaffold, vendoring, workflow, secrets | A real private repo: bounded first run, three scheduled ticks, a killed judge step still publishes partial progress, Actions minutes and token usage measured |
| 7 Codex + the rest | TOML agents and Codex workflow, Routine and local-timer docs, SmartRecruiters/Workday/Amazon, README | Codex smoke run with the desktop-bundled CLI; one Routine run |

## End-to-end verification

1. `uv run pytest` passes, including the idempotence test (a second tick with unchanged inputs
   produces byte-identical tracked files).
2. `hunter collect` against three real boards in a temp feed repo; inspect counts and state files.
3. In that repo, run the skill with `max_assess` 5; `apply` reports no unresolved invalids,
   the briefs contain verbatim quotes, and the run's JSON output shows the subagents on Haiku and Sonnet.
4. `gh workflow run job-hunter.yml` on a throwaway private repo: a commit lands; an immediate re-run
   reports `unchanged`.
5. Dogfood with a real profile for several days; check the recall canary and what triage dropped.

## Open risks, each with its cheapest experiment

| Risk | Experiment |
|---|---|
| E1: Haiku triage recall with a snippet | **Resolved**, see [spikes.md](spikes.md): 149 of 150 core-role postings kept; 1,200 characters stays the default |
| E2: Headless named subagents: is `model: haiku` honored, do 20 run in parallel, do path-scoped write rules apply? | **Resolved**, see [spikes.md](spikes.md): yes to all three, with `Edit(...)` as the rule form |
| E3: Pinned CLI versus `claude-code-action` on a scheduled trigger | A ten-line smoke workflow |
| E4: Read-tool size cap for packets | **Resolved**, see [spikes.md](spikes.md): 25,000 tokens per read; the 48 KB cap holds |
| E5: The simhash threshold comes from one snapshot pair | **Resolved**, see [spikes.md](spikes.md): threshold set to 12 from 8,643 changed pairs across five snapshots |
| E6: Aggregators may rate-limit shared runner IPs | Three runner fetches a day apart |
| E7: Subscription usage limits during a backfill | Measure tokens per packet in Phase 6 and tune the caps |

## Deferred past v1

Public/anonymous mode (with its full privacy audit and a keepalive for the 60-day cron disable),
per-link verification, the Google adapter, application tracking and a `hunt` procedure,
notifications, automated local-timer install, Batch API or local-model judge backends, Windows, and
an optional mirror of the job-facts layer (no profile, fit or gaps) to an unlisted URL for agents
that can only fetch a link.
