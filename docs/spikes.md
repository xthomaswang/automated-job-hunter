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

## Still open

| Spike | Needs |
|---|---|
| E1: triage recall at 0 / 600 / 1,200-char snippets | Read-only access to the labeled jobs in the old `uxr-job-radar` database |
| E3: the same CLI invocation on a scheduled GitHub Actions runner | A throwaway private repo and an Actions secret |
| E5: simhash threshold across more snapshots | The old database backups, read-only |
| E6: aggregator rate limits from runner IPs | The throwaway repo |
