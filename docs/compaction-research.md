# Context compaction research: survey, gaps and plan

**Status:** Phase R draft, written 2026-10-04. Section 7 holds the Phase 5 replay results (re-recorded 2026-10-06).
**Scope:** this plugin (`hermes-tameru-plugin`, 1.3.0 at the time of writing) and the upstream engine
[`0xWhiteMage/tameru-compaction-system`](https://github.com/0xWhiteMage/tameru-compaction-system).

This document is the single place for the survey. Upstream `docs/RESEARCH.md` keeps only engine-specific
takeaways and a pointer here.

Contents:

1. [Why this exists, and the method](#1-why-this-exists-and-the-method)
2. [The converged pipeline](#2-the-converged-pipeline)
3. [Survey table](#3-survey-table)
4. [Gap matrix](#4-gap-matrix)
5. [Adoption map](#5-adoption-map)
6. [Not adopted, and why](#6-not-adopted-and-why)
7. [Results](#7-results)
8. [Sources](#8-sources)

---

## 1. Why this exists, and the method

### Why

The request was to survey recent GitHub projects that do context compaction, learn from them, find where this
plugin falls short inside Hermes Agent, and fix it. This document records what was looked at, how each claim
was checked, which gaps were found, and for each lesson whether it was adopted, was already present, or was
rejected and why. It is meant to be audited: every row names its evidence and how that evidence was obtained.

The short version of the findings:

- Tameru's extractive core is sound. Most of the damage in 1.3.0 comes from how the plugin is wired into
  Hermes (it runs outside Hermes' prune gates and post-checks summaries after Hermes has already changed its
  state), plus engine bugs that hit the outputs agents actually produce: logs, numbered file reads, diffs, grep
  output and JSON.
- The surveyed projects have converged on one pipeline (section 2). Hermes already implements most of it. The
  plan is for Tameru to become Hermes' per-result step through a seam Hermes already calls, not to wrap
  Hermes from outside.

### Method

1. **Survey (three agents, 2026-10-04).**
   - *Harness survey* [HS]: compaction in agent harnesses, including Hermes itself. Code was read from
     `raw.githubusercontent.com`; activity dates come from GitHub commit pages.
   - *Library survey* [LS]: compressors, memory systems, harness plugins, paper code and guides.
   - *Engine inventory* [INV]: every file of the Tameru 1.3.0 engine and plugin, with the vendored engine run
     in-process to check behaviour (`ccr=False`, no bytecode written).
2. **Design review** [DR]: one agent answered seven questions about Hermes' seams, config path, engine tools,
   elision marker, `session_search` recovery and stable ids against Hermes `main` as fetched on 2026-10-04.
3. **Adversarial verification** [V]: four agents tried to refute 38 load-bearing claims, in four groups
   (Hermes seam; Hermes loading and config; Hermes tools and markers; engine bugs), by reading Hermes `main`
   and probing a local Hermes checkout and the engine in-process.
   - Result: **18 confirmed, 18 partially confirmed with corrections, 2 refuted**; 37 new risks raised.
4. **Plan critique**: two critics reviewed the plan, one for feasibility [CF] (15 issues, 9 high) and one for
   coverage of the survey's lessons [CC] (14 issues, 4 high). Their findings added G14–G16, E9–E12, the
   Phase R / Phase 0 / Phase 1a ordering, and most of the "not adopted" list in section 6.

The raw reports are working files and are not committed. Every fact this document relies on is restated
here with its pointer. Tags used in the Evidence columns: [HS], [LS], [INV], [DR], [CF], [CC], and `V:<id>` for
a verification claim (for example `V:S5`).

**Line numbers.** Hermes line numbers refer to `main` as fetched on 2026-10-04 and will drift; the plugin's
tests pin Hermes at `1298c8e74baa73e1a2b90124228d017261ac6bc4`. Engine line numbers refer to the vendored
1.3.0 files: CC = `tameru/compress_context.py`, TR = `tameru/transcript.py`, FA = `tameru/format_adapters.py`,
PL = `hermes_tameru_plugin/__init__.py`.

### Corrections that verification forced

These changed the plan; they are listed so a reader of the earlier drafts knows what moved.

- **Seam A shape.** `ContextCompressor._demote_tool_result_at(result, idx, call_id_to_tool, min_prune_chars,
  protected_skills=None)` is a `@staticmethod` reached through `self.`. Pass 2 calls it with 5 arguments from
  inside a generator expression, so the name is *not* in `_prune_old_tool_results.__code__.co_names`; a
  contract test must walk `co_consts` recursively. Pass 4 (`_pressure_demote_tail`) calls it with 4 arguments,
  runs only from `compress()`, and can visit the same index twice in one pass. (`V:S2`)
- **Gate order.** `prune_tool_results_only` runs the seam *before* its nothing-eligible, min-reclaim,
  stale-generation and DB-failure no-ops, so Tameru work can be thrown away. "Hermes no-op means zero Tameru
  calls" holds only for the threshold, tail-only, rearm and store-capability gates. (`V:S5`)
- **Second demotion path.** Lean tail mode (the default) rewrites tail tool results of 1,500+ chars through
  `_demote_stale_tail_tools`, which never calls seam A. This became seam B. (`V` hermes-seam risks; [CF])
- **Non-string content** goes through an image-strip branch, not a per-tool one-liner. (`V:S1`, `V:S4`)
- **Stub shape.** A header is treated by Hermes as a summary stub only if it starts with `[`, contains
  ` chars)` and is under 400 chars. Parent one-liners embed unbounded arguments (paths, patterns), so the
  header must be capped (the plan uses 380). The terminal one-liner counts newlines of the JSON envelope and
  usually says "1 lines output". (`V:S3`, `V:S9`)
- **Config.** Constructor-level `compression.*` keys never reach a plugin engine, but the three micro-compact
  attributes do (Hermes sets them with `setattr` when present). The `ctx` passed to `register()` differs by
  load path, so `ctx.get_config` must be called inside `try/except`. Whether the user set
  `proactive_prune_tokens` can only be told with `read_raw_config_readonly()`. Config key names differ from
  constructor names (`threshold` vs `threshold_percent`, `target_ratio` vs `summary_target_ratio`,
  `threshold_tokens` vs `threshold_tokens_cap`), so a signature filter would drop them silently.
  (`V:L1`, `V:L2`, `V:L7`, loading-config risks)
- **Loading.** pip installs also need `plugins.enabled: [tameru]`; `kind: context-engine` is not a valid
  manifest kind; directory installs never clone, so module-level state is shared across agents; under
  `plugins.isolation: host` the engine gets an opaque `session_db` and prunes never commit. (`V:L4`, `V:L6`,
  `V:L8`)
- **Engine tools.** Injected once at init, gated on `enabled_toolsets`, silently skipped on a name collision;
  the engine cannot observe whether its tool was injected. Hermes classes any tool not on its no-effect list,
  `tameru_expand` included, as side-effecting. (`V:T1`, `V:T3`)
- **Recovery.** `session_search(role_filter='tool')` bypasses the 8,192-char FTS prefix and does a full
  `LIKE` scan; it returns one hit per lineage, newest first, so a later rendered copy can shadow the original.
  `message_uid` and `_tool_call_uid` survive copies but are not unique keys, so refs must also hash content.
  (`V:T4`, `V:T5`)
- **Overstated.** A stale `api_content` sidecar on a tool row has no wire effect; dropping it is hygiene, not
  a bug fix. (`V:S8`)
- **Engine claims.** E2's 28–35 s holds when most of a 650 KB log is kept (28.5 s with about 88% kept; 8.6 s
  with about 22% kept). E3's cause is the stale-error cap and the `_important` exclusion, not E1; fixing E1
  alone still kept 0/30 errors and cut savings from 54% to 17% on one probe. Diffs and grep output do *not*
  fail open; they produce wrong-hunk or mis-shaped extracts with `fail_open=False`. The `N|code` damage comes
  from `compress_context._looks_like_csv`, which also shreds unnumbered code. (`V:E2`, `V:E3a`, `V:E5a`,
  `V:E5b`, `V:E4b`)
- **Upstream test count.** 38 test modules (39 entries including `conftest.py`). (engine-bugs risks)

### Counting rules and honest counts

**Rule.** One row per distinct GitHub repository that a report gives a finding for. Sections the reports
grouped (for example "Provence, RECOMP, EXIT, CompAct", or "LangChain") are split into one row per repo. A
renamed or moved repo is one row. A repo is counted once even if both surveys covered it.

The surveys' own tallies were "22 repos" [HS] and "27 projects, 21 of them code repos looked at directly"
[LS]. The plan's "roughly 43" is 22 + 21; it double-counts the three repos both surveys covered
(`stephenschoettler/hermes-lcm`, `martian-engineering/lossless-claw`, `JetBrains-Research/the-complexity-trap`)
and under-counts the grouped sections. The count below replaces it.

| | Rows |
|---|---|
| **Distinct repos in the table** | **62** |
| by category: harness / library / paper-code / guide | 28 / 17 / 14 / 3 |
| verified by reading source code | 27 (4 of them without a file named in the report) |
| verified by README or in-repo docs only | 30 |
| found by search only (page or paper not opened) | 5 |
| with an activity date in 2026 recorded by the reports | 21 (one is an archive date, one is CI-only commits) |
| already covered by upstream `docs/RESEARCH.md` | 2 (Headroom, LLMLingua) |
| **new to the project's research record** | **60** |

Not counted as repos:

- **Covered earlier by upstream `docs/RESEARCH.md` and not re-surveyed:** the JEV-related repos
  (`lucasmartins-ai/lcc`, `tamaratran/fast-jev-compaction` and about 20 forks, `Waxmell114514/jev-compaction`,
  `kerpopule/hermes-jev-skills`, `yoza10635/dsh-argp`, `yangyu666/dsh-jev-prune`,
  `wjw66/dsh-jev-pre-compaction`, the `anneheartrecord/claude-code-docs` teardown), `codexstar69/pi-lcm` and
  `tianjianl/selfcompact`. What Tameru took from them (CCR, `pin_recent`, `min_savings_ratio`,
  `degraded_view`, `strategy="auto"`, threshold replay, `list_ccr`, paginated retrieve, secrets screen before
  CCR, recursion guard, `trajectory_gate`) appears as **already had** in the table.
- **Name mismatch, not reconciled:** upstream `RESEARCH.md` lists Headroom as `headroomlabs-ai/headroom`
  (PyPI `headroom-ai`); this survey read `chopratejas/headroom`. Both are treated as one project, as the plan
  does, but the relationship between the two names was not checked.
- **Mentioned without analysis:** `Kilo-Org/kilocode` (described only as an opencode fork) and "hermes-lcm-x"
  (listed in the Hermes plugin docs; the page could not be opened; unverified).
- **Not repositories:** Anthropic API features (context editing `clear_tool_uses_20250919`,
  `clear_thinking_20251015`, server compaction `compact_20260112`), Claude Code (internals unverified),
  Factory.ai's compression evaluation (read through a summary, see row D1), and the papers below.

**Papers (listed separately, not counted as repos).** A dagger (†) marks a paper whose code repo has a row in
section 3.

| Paper | Id | Repo row | Read via |
|---|---|---|---|
| The Complexity Trap (NeurIPS 2025 DL4Code) | arXiv 2508.21433 | † P1 | search snippets (arxiv blocked) |
| Masking regime map ("helps until it doesn't") | arXiv 2606.00408 | none | search snippets |
| ACON | arXiv 2510.00615 | † P2 | search snippets |
| Provence / XProvence (ICLR 2025) | arXiv 2501.16214 | † P5 | search snippets |
| EXIT (ACL Findings 2025) | arXiv 2412.12559 | † P7 | search snippets |
| CompAct (EMNLP 2024) | arXiv 2407.09014 | † P8 | search snippets |
| LongCodeZip (ASE 2025) | arXiv 2510.00446 | † P9 | search snippets |
| SWE-Pruner | arXiv 2601.16746 | † P10 | search snippets |
| PCToolkit | arXiv 2403.17411 | † P4 | search snippets |
| ReSum | arXiv 2509.13313 | † P13 | search snippets |
| AgentFold (ICLR 2026) | arXiv 2510.24699 | none (no code repo found) | search snippets |
| Context-Folding / FoldGRPO (ICML 2026) | arXiv 2510.11967 | none (no code repo found) | search snippets |
| Lost in Compression (Aug 2026; 10 languages, 11 target models) | arXiv 2608.26175 | none | search snippets |
| Lossless Context Management (Voltropy) | arXiv 2605.04050 | implemented by L6, L7 | search snippets |
| Zep: temporal knowledge graph | arXiv 2501.13956 | † L14 | search snippets |
| Chroma "Context Rot" technical report | — | † P14 | not re-read; in upstream `RESEARCH.md` |
| RECOMP | — | † P6 | 6% figure recalled from the abstract, unverified |

### Known limits of this survey

- The egress proxy blocked `arxiv.org`, `huggingface.co`, `factory.ai`, `docs.headroomlabs.ai`, `pi.dev`,
  `pydantic.dev`, `learn.microsoft.com` and the Hermes docs site. Paper figures come from search snippets;
  Factory's per-dimension scores were not checked.
- The fetch tool printed impossible years for some releases (graphiti, repomix, TOON, claude-mem); those dates
  are marked unverified.
- Star counts were not cross-checked and are not used anywhere in this document.
- Reported benchmark numbers (Headroom, pi-fold, whittle, TOON, Complexity Trap, mem0, Mastra) are the
  projects' own; none was reproduced.
- Hermes moves fast (issue references up to #121548 on the survey date). Anything that depends on a private
  Hermes method is pinned by contract tests (section 5), not by this document.

---

## 2. The converged pipeline

Across harnesses and libraries the order of operations is the same, from cheapest and safest to most lossy:

```text
 tool result arrives
   1. write-time / lossless fold   strip ANSI and CR redraws, fold identical runs, minify JSON (round-trip checked)
   2. dedupe + supersession        identical outputs; stale reads after a write; re-reads; repeated calls
   3. per-result extract / brief   inside the host's gates; stable bytes; marker says what was cut; ref to original
   4. age decay                    older results step down: extract -> brief -> header -> placeholder
   5. LLM summary (last)           structured, iterative, with deterministic anchors appended
   +  retrieval path               originals archived; a tool or search brings them back on demand
```

Where Hermes already is [HS], [DR]:

| Stage | Hermes today | Gap for Tameru |
|---|---|---|
| 1 lossless fold | none | E9 |
| 2 dedupe / supersession | pass 1: md5 of byte-identical results ≥200 chars, keeps the newest | structural supersession, G14 |
| 3 per-result | pass 2: per-tool one-liner via `_summarize_tool_result`; pass 4 pressure demote | seam A: informative extract or brief instead of a one-liner (G1–G9) |
| 4 age decay | lean tail: results ≥1,500 chars older than 6 rounds become recovery stubs | seam B; retained-extract budget and tiers, G15 |
| 5 LLM summary | structured iterative summary; lean mode appends an Anchor Index, verbatim user messages and a recovery footer | none; only an artifact trail (Phase 3) |
| retrieval | originals archived by `archive_and_compact`; `session_search` | `tameru_expand` by ref, G8 |

---

## 3. Survey table

**Legend.**

- **Category:** harness = an agent framework or CLI; library = a compressor, proxy, memory system or harness
  plugin; paper-code = code released with a paper; guide = documentation-only repo.
- **Stage:** write-time · dedupe · fold (lossless) · supersede · extract (per-result extract or brief) · decay
  (age decay) · summary (LLM) · retrieval · eval.
- **Det / Rev / Cache:** deterministic / reversible (the original can be recovered) / cache-aware (designed
  around provider prefix caching). Values: Y, N, partial; `?` = the report does not say; `–` = not applicable.
  These describe the technique in the row, not every feature of the repo.
- **Verified by:** code = source files read; README = README or in-repo docs only; search-only = the page or
  paper was not opened.
- **†** after a repo name: the report did not print the owner; the canonical GitHub owner is given for
  navigation only.
- **Tameru status:** **already had** (in Tameru or in the Hermes host) / **adopted** (gap id and plan phase;
  "adopted" means scheduled in the plan, see section 5 for the tests) / **not adopted** (with the reason).

### 3.1 Harnesses (28)

| # | Repo | Cat. | Activity seen | Stage | Technique | Det | Rev | Cache | Evidence | Verified by | Takeaway | Tameru status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | harness (host) | last commit 2026-10-04; tests pin `1298c8e` | dedupe · extract · decay · summary · retrieval | prune passes (md5 dedupe; per-tool one-liners; pressure demote), lean-tail recovery stubs, structured iterative summary with Anchor Index, originals archived and searchable via `session_search`; non-imitable elision marker blocks side-effecting calls that paste it | partial | partial | Y | `agent/context_compressor.py` (3268-3297 seam A, 3480-3559 prune gates, 3746-3772 lean tail, 5656-5707 `compress`); `agent/compression_marker.py`; `agent/agent_init.py:1993-2182` | code + probe | Most of the pipeline already exists; improve the per-result step from inside | **adopted:** seams A and B (G1, G3, G4), Hermes marker (G7), config mirroring (G11); Phase 2 |
| H2 | [openai/codex](https://github.com/openai/codex) | harness | last commit 2026-10-01 | summary | handoff summary prompt; real user messages re-inserted newest-first up to 20k tokens; on overflow drops the oldest item "to preserve cache"; ~90% cap and remote `encrypted_content` compaction from secondary sources (unverified) | N | N | partial | `codex-rs/core/src/compact.rs`; `codex-rs/prompts/templates/compact/prompt.md` | code | Re-insert user messages verbatim after a summary | **not adopted:** the summary stays Hermes'; Hermes lean mode already appends user messages verbatim |
| H3 | [anomalyco/opencode](https://github.com/anomalyco/opencode) (was sst/opencode) | harness | `compaction.ts` last commit 2026-08-12 | extract · summary | prune walks back, skips the last 2 user turns, protects 40k recent tool tokens and `skill`; commits only if >20k tokens freed (`PRUNE_MINIMUM`); placeholder `[Old tool result content cleared]`, originals kept in storage; fixed Markdown summary template | partial | partial | partial | `packages/opencode/src/session/compaction.ts`, `overflow.ts` | code | Commit a prune only when it frees enough; protect recent output by token budget | **adopted:** inherit Hermes' min-reclaim gate (G1); `skill_view` exempt (ladder rung 2); Phase 2 |
| H4 | [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands) | harness | legacy V0 at tag 1.0.0 | decay · summary | V0 condensers: ObservationMasking (`attention_window=5` → `<MASKED>`), RecentEvents, AmortizedForgetting, LLMAttention (explicitly disables prompt caching), LLM and structured summaries, Pipeline | partial | ? | ? | `openhands/memory/condenser/impl/` (tag 1.0.0) | code | Age-based masking window | **adopted:** age tiers (G15), Phase 2 |
| H5 | [OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) | harness | condenser commit 2026-09-28 | summary | `LLMSummarizingCondenser` (`max_size=240`, `keep_first=2`, `minimum_progress=0.1`), `PipelineCondenser`; `View.manipulation_indices` never splits call/observation pairs | partial | ? | ? | `openhands/sdk/context/condenser/` | code | Cut only at safe indices | **already had** (host): boundary alignment and `_sanitize_tool_pairs`; replay checks pairing (Phase 0) |
| H6 | [aaif-goose/goose](https://github.com/aaif-goose/goose) (was block/goose) | harness | 2026-09-22 | summary · extract | auto-compact (default threshold about 0.8, unverified); originals stay user-visible but not agent-visible; optional LLM one-liner per call+result in batches of 10, off by default | N | partial | ? | `crates/goose/src/context_mgmt/mod.rs`; `compaction.md`, `compaction_summary.md` | code | Hide originals from the agent instead of deleting them | **not adopted:** per-result LLM summaries are not deterministic; Hermes already archives originals |
| H7 | [cline/cline](https://github.com/cline/cline) | harness | main 2026-10-02; analysed at tag v3.40.0 (new SDK layout unverified) | supersede · extract | superseded file reads become `duplicateFileReadNotice`; if that saves ≥30% of chars truncation is skipped, else half/quarter ranges are dropped with a notice; edits kept as a timestamped overlay that can be rolled back | Y | Y | ? | `ContextManager.ts` (tag v3.40.0) | code | Cheap supersession first; stop once it meets the target | **adopted:** structural supersession (G14), Phase 2 |
| H8 | [RooCodeInc/Roo-Code](https://github.com/RooCodeInc/Roo-Code) | harness | archived 2026-05-15 | summary · decay | non-destructive condense and truncation via `condenseParent` / `truncationParent` tags (rewind restores); sliding-window fallback with a `[Sliding window truncation: N messages hidden…]` marker; `injectSyntheticToolResults`; tree-sitter folded file context (≤50k chars) | ? | Y | ? | `src/core/condense/index.ts`, `context-management/index.ts` | code | Tag instead of delete; mark every truncation | **already had** (host): originals archived; **adopted:** explicit markers (G7), Phase 2 |
| H9 | [Aider-AI/aider](https://github.com/Aider-AI/aider) | harness | v0.86.0, Aug 9 (year not shown; probably 2025) | summary | `ChatSummary(max_tokens=1024)`: keep about half the budget as tail, summarize the head in first person, recurse to depth 3; no tool messages | N | ? | ? | `aider/history.py` | code | Little that applies to tool-heavy sessions | **not adopted:** no tool-result handling to learn from |
| H10 | [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | harness | 2026-09-24 | extract · decay · summary · retrieval | `collapseOlderFunctionResponses` (2 KB cap with marker; last 3 tool turns and retrieval tools such as `read_file`, `grep`, `glob` exempt); `truncateHistoryToBudget` (50k-token newest-first budget, last 30 lines kept, rest saved to a file); output masking to files; `<state_snapshot>` summary with a "Probe" self-critique; rejects empty or token-inflating summaries | partial | partial | ? | `packages/core/src/context/` (`toolOutputMaskingService`, `toolDistillationService`, `loopDetectionService.ts`) | code | Exempt retrieval tools; reject summaries that inflate | **adopted:** exempt list incl. `session_search` (Phase 2 rung 2); **not adopted:** truncate-to-file (secrets) |
| H11 | [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) | harness | not recorded | summary | `DEFAULT_PCT 0.85`, `AUTOCOMPACT_BUFFER 13_000`, circuit breaker after 3 consecutive failures; a summary without a closed `</state_snapshot>` is invalid | N | ? | ? | `services/chatCompressionService.ts` | code | Circuit-break repeated failures | **already had** (host): cooldown ladder and anti-thrash breaker |
| H12 | [openclaw/openclaw](https://github.com/openclaw/openclaw) | harness | docs 2026-10-03 | extract · decay · summary | Anthropic route: server-side `clear_tool_uses_20250919`; other routes: cache-TTL-gated soft-trim (results >4,000 chars → first + last 1,500) then hard-clear; edits stored as projections that replay byte-identically; `tools.{allow,deny}`; "safeguard" compaction keeps the original unless headings and identifiers survive and history strictly shrinks | partial | Y | Y | `docs/concepts/session-pruning.md`, `docs/concepts/compaction.md` | README (docs) | Byte-identical projections; soft-trim then hard-clear; check before commit | **adopted:** byte-stable rows (G2), tiers (G15), exempt list (G13), no post-commit check (G3), Phase 2; **not adopted:** TTL-cold pruning |
| H13 | [letta-ai/letta](https://github.com/letta-ai/letta) | harness | V1 server moved to the `archive` branch | summary | summarizer modes (`sliding_window` default); eviction escalates to the goal with a 10% floor; cut points keep approvals and tool groups whole; `clip_chars` 50,000; cheap summarizer model | N | ? | ? | `letta/services/summarizer/` (archive branch) | code | Cut points respect approvals and tool groups | **already had** (host): pairing |
| H14 | [letta-ai/letta-code](https://github.com/letta-ai/letta-code) | harness | not recorded | summary | memory blocks the agent rewrites, `/sleeptime`, `/reflect`, git-tracked context (internals unverified) | N | ? | ? | README | README | — | **not adopted:** agent-written memory is out of scope |
| H15 | [langchain-ai/langchain](https://github.com/langchain-ai/langchain)† | harness | not recorded | decay · summary | `ClearToolUsesEdit(trigger=100_000, clear_at_least=0, keep=3, clear_tool_inputs=False, exclude_tools=(), placeholder="[cleared]")`, mirroring Anthropic's API; `SummarizationMiddleware` with AND/OR triggers and an AI/Tool-safe cutoff | partial | ? | partial | `libs/langchain_v1/langchain/agents/middleware/` | code | `exclude_tools`; never clear tool inputs by default | **adopted:** `exempt_tools` config (G13), Phase 2; tool inputs: **already had** (host never rewrites them) |
| H16 | [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)† | harness | 2026-09-22 | write-time · summary · retrieval | `FilesystemMiddleware(tool_token_limit_before_evict=20000)` writes large results to `/large_tool_results/{id}` and leaves a head/tail 5-line numbered preview with `[N lines truncated]`; summarizes at 0.85, keeps 0.10, offloads history to a file; `truncate_args_settings` shortens old tool-call arguments | partial | Y | ? | `FilesystemMiddleware` (file not named) | code (file not named) | Head/tail preview with a count marker | **adopted:** head/tail in `brief_context` (Phase 1a); **not adopted:** evict-to-file (secrets), argument truncation (host never rewrites arguments) |
| H17 | [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent) | harness | 2026-07-16 | decay | `LastNObservations(n, polling=…)` → `Old environment output: (N lines omitted)`; `polling` batches elisions so the cache breaks only every k steps; keep/remove tags; `ClosedWindowHistoryProcessor`; `CacheControlHistoryProcessor` | Y | ? | Y | `sweagent/agent/history_processors.py` | code | Batch rewrites so each cache break buys a large saving | **adopted:** inherit Hermes' rearm and min-reclaim gates (G1); tier changes only inside committing passes (G15); Phase 2 |
| H18 | [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)† | harness | not recorded | — | deliberately keeps a linear history with no processors | – | – | ? | README-level statement | README | A no-compaction baseline exists | **not adopted:** nothing to adopt |
| H19 | [microsoft/autogen](https://github.com/microsoft/autogen)† | harness | maintenance mode | decay | `Buffered`, `HeadAndTail` (avoids splitting function calls) and `TokenLimited` chat-completion contexts | Y | ? | ? | ChatCompletionContext classes (file not named) | code (file not named) | Head-and-tail windows that keep calls whole | **already had** (host): head/tail protection and pairing |
| H20 | [microsoft/semantic-kernel](https://github.com/microsoft/semantic-kernel)† | harness | not recorded | summary | `ChatHistoryTruncationReducer`, `ChatHistorySummarizationReducer(..., include_function_content_in_summary=False)`; `locate_safe_reduction_index`, `get_call_result_pairs` | partial | ? | ? | `contents/history_reducer/` | code | Reduce only at a safe index | **already had** (host) |
| H21 | [microsoft/agent-framework](https://github.com/microsoft/agent-framework)† | harness | not recorded | extract · summary | messages annotated with atomic group kinds; strategies exclude by annotation instead of deleting; `ToolResultCompaction` (`[Tool results: name: …]`, 4,096-char cap, `... [truncated]`); `TokenBudgetComposed` deterministic fallback | partial | partial | ? | `python/packages/core/agent_framework/_compaction.py` (learn.microsoft.com blocked) | code | Atomic groups; a per-tool header line | **adopted:** pending-call scope (G10, Phase 1a); header line (G7, Phase 2) |
| H22 | [strands-agents/sdk-python](https://github.com/strands-agents/sdk-python) | harness | not recorded (now `strands-py/`) | extract · summary | `SlidingWindowConversationManager` truncates the oldest tool results first, keeping head and tail with `... [truncated: N chars removed] ...` (reactive overflow only); `SummarizingConversationManager(summary_ratio=0.3, preserve_recent_messages=10)` | partial | ? | ? | class names (file not named) | code (file not named) | Head + tail with a char count | **adopted:** head/tail and counts in `brief_context`, Phase 1a |
| H23 | [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | harness | not recorded | summary | `OpenAIResponsesCompactionSession` (modes `previous_response_id`, `input`, `auto`; default trigger ≥10 candidate items); overflow leaves history unchanged; trimming/summarizing variants are in the cookbook (unverified) | N | ? | ? | `src/agents/memory/` | code | Leave history unchanged on failure | **already had:** Tameru fails open by design |
| H24 | [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai)† | harness | not recorded (pydantic-ai-harness merged in) | dedupe · supersede · decay · summary | zero-LLM `ClampOversizedMessages`, `SlidingWindowCompaction`, `ClearToolResults(keep_pairs)`, `DeduplicateFileReads(file_key)`; `TieredCompaction` re-measures after each tier and stops early ("clear and dedupe first, then summarize"); triggers by messages, tokens or fraction | partial | ? | ? | `docs/harness/compaction.md` (pydantic.dev blocked) | README (docs) | Cheapest tier first; stop at the first tier that fits | **adopted:** ladder stops at the first rung that fits (Phase 2); file-read supersession (G14); config surface (G13) |
| H25 | [mastra-ai/mastra](https://github.com/mastra-ai/mastra)† | harness | not recorded | summary | memory processors `ToolCallFilter`, `TokenLimiter`; Observational Memory (Observer at 30k tokens, Reflector at 40k, 5–40× compression, LongMemEval 94.87% with gpt-5-mini), source path unverified | ? | ? | ? | mastra.ai docs via search | search-only | — | **not adopted:** observation memory is out of scope for a per-result step |
| H26 | [charmbracelet/crush](https://github.com/charmbracelet/crush) | harness | 2026-09-28 | summary | auto-summarize at ≤20k tokens remaining (windows >200k) or ≤20%; template sections Current State, Files & Changes, Technical Context, Strategy, Exact Next Steps | N | ? | ? | `internal/agent/agent.go`, `templates/summary.md` | code | Files & Changes as a summary section | **already had** (host): Relevant Files section; artifact trail (Phase 3) |
| H27 | [continuedev/continue](https://github.com/continuedev/continue) | harness | not recorded | summary | `AUTO_COMPACT_BUFFER_RATIO 0.8`, `AUTO_COMPACT_BUFFER_CAP 15_000`, single-prompt summary; `countTokens.ts` keeps the trailing tool sequence intact | N | ? | ? | `extensions/cli/src/compaction.ts`; core `countTokens.ts` | code | Keep the trailing tool sequence whole | **already had** (host): the pending tool round is spared |
| H28 | [zed-industries/zed](https://github.com/zed-industries/zed) | harness | not recorded | summary | `AutoCompactThreshold::{Percentage, TokensUsed, TokensRemaining}`; auto-compaction only for windows ≥80k; keeps user messages within an 80k-byte budget; provider-native or summary compaction | N | ? | ? | `crates/agent/src/thread.rs` | code | Keep user messages verbatim within a budget | **already had** (host): lean mode keeps user messages verbatim |

### 3.2 Libraries, plugins and memory systems (17)

| # | Repo | Cat. | Activity seen | Stage | Technique | Det | Rev | Cache | Evidence | Verified by | Takeaway | Tameru status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| L1 | [chopratejas/headroom](https://github.com/chopratejas/headroom) | library | v0.39.1 (2026-09-26) | fold · dedupe · supersede · extract · retrieval | content routing to about 45 transforms: SmartCrusher for JSON (first/last fractions, variance outliers, change points, lossless tabular path kept if it saves ≥15%); `log_compressor` (ERROR 1.0 / WARN 0.5 / INFO 0.1, first and last error kept, `[N lines omitted: M ERROR, K WARN...]`); `error_detection` strips "0 errors" / "failed=0" first; `cross_turn_dedup` keep-earliest `[↑{n}L same as msg {k}: …]`; `read_lifecycle` STALE / SUPERSEDED; `compression_summary`; `lossless_compaction` with round-trip check; CCR store and `headroom_retrieve`; `audit_safe` with `fail_closed_on_protected_loss`; frozen prefix left byte-identical | Y (optional Kompress model) | Y | Y | `headroom/transforms/{smart_crusher,log_compressor,error_detection,cross_turn_dedup,read_lifecycle,compression_summary,lossless_compaction,cache_aligner}.py`; `headroom/ccr/tool_injection.py` | code | Closest analogue: lossless first, structural supersession, say what was dropped, fail closed on protected loss | **already had:** CCR, freeze, benchmark arm (upstream `RESEARCH.md`); **adopted:** G6 and G7 categories (Phase 2), G14 (Phase 2), zero-count rule in `error_fingerprints` (Phase 1a), E9 and E11 (Phase 1b); **not adopted:** keep-earliest dedupe, Kompress |
| L2 | [rtk-ai/rtk](https://github.com/rtk-ai/rtk) | library | v0.51.0 (2026-10-02) | write-time · retrieval | command-rewrite hook with per-command filters (filter, group, truncate, dedupe with counts; tests show failures only; grep grouped by file); raw output saved to SQLite on failure or truncation → `[full output: rtk recall <id>]`; exit codes preserved; ships a Hermes plugin (`rtk-rewrite`); README warns savings ≠ bill reduction | Y | partial | ? | README; `src/cmds`, `src/filters` | README | Pick the filter from the command, not the content; keep exit codes | **adopted:** `content_hint` (E12, Phase 1a and 2), wrapper metadata (G9, Phase 1a and 2); RTK recall lines can be pinned via `protect_patterns` (not a default) |
| L3 | [firstops-dev/whittle](https://github.com/firstops-dev/whittle) | library | July 2026; Go module v0.2.0 | write-time · fold | PostToolUse hook: JSON minified and columnar (lossless, 15,846/15,846 items in their test), logs keep errors, warnings and traces with `... [N lines omitted]`, ANSI stripped, CR overwrites collapsed, code untouched; fail-open; 22% tool-output reduction over 5,000 sessions reported as only 3–5% of session cost under caching | Y | partial | Y | README | README | Report savings net of cache pricing | **adopted:** cache-priced cost metric (G16, Phase 0 replay); folds (E9, Phase 1b); **not adopted:** write-time compression |
| L4 | [Compresr-ai/Context-Gateway](https://github.com/Compresr-ai/Context-Gateway) | library | released Feb 2026; 63 commits | summary | Go proxy; conversation summaries precomputed in the background, trigger at 75%; tool-output compression claimed (ratio 0.5 per third-party write-ups); retrieval and cache handling unverified | N | ? | ? | README | README | Precompute summaries off the critical path | **not adopted:** the LLM summary is Hermes' |
| L5 | [Opencode-DCP/opencode-dynamic-context-pruning](https://github.com/Opencode-DCP/opencode-dynamic-context-pruning) | library (opencode plugin) | npm 3.2.x betas; development slowed | dedupe · supersede · summary | dedupe repeated identical calls (keep newest); drop write inputs once the file is read back; purge inputs of failed calls after 4 turns but keep the error; LLM `compress` tool; default protected tools; README warns pruning can increase cache misses; AGPL-3.0 | partial | ? | partial | README | README | Supersession keyed on tool-call arguments | **adopted:** repeated identical calls (G14, Phase 2); **not adopted:** purging tool inputs; no code reused (AGPL) |
| L6 | [stephenschoettler/hermes-lcm](https://github.com/stephenschoettler/hermes-lcm) | library (Hermes engine) | v1.0.0-rc.1 | summary · retrieval | Hermes `ContextEngine` (`context.engine: lcm`); every message in SQLite with FTS5; DAG of LLM summaries; fresh tail 32, threshold 0.35; 15 tools (`lcm_grep`, `lcm_expand`, `lcm_describe`, …); circuit breaker (2 failures, 300 s); defers compaction instead of a lossy trim; "cache-friendly, not fully cache-aware"; top-level `lcm:` config block | partial | Y | partial | README | README | Hermes engines can expose their own tools; config block | **adopted:** `tameru_expand` (G8, Phase 3); config (G11, Phase 2) |
| L7 | [martian-engineering/lossless-claw](https://github.com/martian-engineering/lossless-claw) | library (OpenClaw engine) | 2026-09-28 | summary · retrieval | every message in SQLite; leaf and condensed summary DAG (`leafChunkTokens` 20k, `contextThreshold` 0.75, `freshTailCount` 64); file blocks >25k tokens stored separately with "exploration summaries"; `lcm_grep`, `lcm_describe`, `lcm_expand`; `/lossless doctor` | N | Y | ? | README | README | Keep large blocks out of line behind a retrieval tool | **adopted:** expand tool (G8), Phase 3 |
| L8 | [shaneconner/pi-fold](https://github.com/shaneconner/pi-fold) | library (Pi agent) | 494 commits (date not recorded) | extract · retrieval · eval | pending marks recorded outside the context, applied in one commit at 0.80 occupancy; folded spans become runtime-written deterministic briefs ≤2,000 chars; originals on disk, SHA-256 verified; `pi_fold_context` tool; reported on one 64-stage task: deterministic briefs 11.3/16, compaction 4/16, agent-written briefs 3/16 | Y | Y | Y | README | README | Runtime-written deterministic briefs beat agent-written ones; batch commits | **adopted:** brief rung (`brief_context` Phase 1a, ladder rung 6 Phase 2); expand (G8, Phase 3) |
| L9 | [langchain-ai/langmem](https://github.com/langchain-ai/langmem)† | library | not recorded | summary | `summarize_messages`, `RunningSummary` (`max_tokens`, `max_tokens_before_summary`, `max_summary_tokens`) | N | ? | ? | function names (file not named) | code (file not named) | — | **not adopted:** Hermes already keeps an iterative summary |
| L10 | [microsoft/LLMLingua](https://github.com/microsoft/LLMLingua) | library | last commits 2026-09-08 and 09-10 (CI pinning); last feature Oct 2025 | extract | small-LM perplexity decides which tokens to drop (LLMLingua, LongLLMLingua); LLMLingua-2 is a distilled token classifier; `force_tokens` | N | N | ? | README; `llmlingua/prompt_compressor.py` | README | — | **already covered** in upstream `RESEARCH.md`; **not adopted:** learned compressor (section 6) |
| L11 | [yamadashy/repomix](https://github.com/yamadashy/repomix) | library | v1.18.1 (date unverified) | extract | `--compress` keeps signatures, imports and comments via tree-sitter, drops function bodies, marks cuts `⋮----` | Y | N | ? | README | README | Code skeletons for old file reads | **not adopted:** needs tree-sitter; the engine is stdlib-only |
| L12 | [toon-format/toon](https://github.com/toon-format/toon) | library | spec v4.1, v4.1.1 (date unverified) | fold | tabular encoding `users[2]{id,name,role}:` plus CSV-like rows; reported 42.6% fewer tokens than JSON on uniform arrays at equal or slightly better accuracy; worse on nested data, and CSV is smaller | Y | Y | ? | `SPEC.md` | README | Lossless re-encoding pays only on uniform arrays | **adopted, opt-in:** TOON-style encoding inside the JSON brief, measured before default-on (E11, Phase 1b) |
| L13 | [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) | library | v13.29.0 early Oct 2026; v13.28.0 2026-09-26 | summary · retrieval | lifecycle hooks; an LLM turns tool use into "observations" (SQLite + Chroma); progressive disclosure: index → timeline → full detail; experimental "Endless Mode" replaces tool outputs with observations (efficiency claims from simulations) | N | partial | ? | README; docs.claude-mem.ai | README | Progressive disclosure for retrieval | **not adopted:** LLM observations |
| L14 | [getzep/graphiti](https://github.com/getzep/graphiti) | library | v0.30.2 (date unverified) | retrieval | bi-temporal knowledge graph; contradicted edges are invalidated, not deleted (LLM-detected); BM25 + embeddings + graph traversal | N | partial | ? | `graphiti_core/utils/maintenance/edge_operations.py` | code | Demote, don't delete | **adopted (principle):** a superseded row keeps a "superseded by msg N" note and its original in the store (G14, Phase 2) |
| L15 | [mem0ai/mem0](https://github.com/mem0ai/mem0) | library | Python SDK v2.2.1 (2026-09-25) | retrieval | since April 2026 single-pass ADD-only extraction ("nothing is overwritten"); time-aware retrieval picks the right dated version | ? | partial | ? | README; mem0 blog | README | Keep history; resolve at read time | **not adopted** (memory store); principle as L14 |
| L16 | [logpai/Drain3](https://github.com/logpai/Drain3) | library | v0.9.11; low maintenance | fold | online log-template mining (fixed-depth tree, `sim_th=0.4`, `depth=4`); regex masking to `<:IP:>`, `<:NUM:>`; parameter extraction | Y | ? | ? | README | README | Mask variables before fingerprinting | **adopted:** Drain3-style masking reimplemented in stdlib (E10, Phase 1b); licence MIT per upstream but unverified, so nothing reused |
| L17 | [NVIDIA/kvpress](https://github.com/NVIDIA/kvpress) | library | not recorded | – (KV cache) | 40+ KV-cache "presses" (SnapKV, ExpectedAttention, KVzip, StreamingLLM, …) | – | – | – | README | README | StreamingLLM's start "sink" + recent tokens ≈ keep first and last | **not adopted:** works inside the model's KV cache, which API-hosted models do not expose |

### 3.3 Paper code (14)

| # | Repo | Cat. | Activity seen | Stage | Technique | Det | Rev | Cache | Evidence | Verified by | Takeaway | Tameru status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1 | [JetBrains-Research/the-complexity-trap](https://github.com/JetBrains-Research/the-complexity-trap) | paper-code | NeurIPS 2025 DL4Code | decay · eval | SWE-agent fork comparing observation masking (keep last M=10), LLM summary (N=21, M=10) and a hybrid on SWE-bench Verified: masking roughly halves cost and matches or beats summary (Qwen3-Coder 480B 53.8% → 54.8%); summaries lengthen trajectories by 13–15%; hybrid is a further 7–11% cheaper | partial | N | ? | `sweagent/agent/history_processors.py`; configs `…N=21_M=10.yaml`; arXiv 2508.21433 | code + paper | Plain masking is the baseline to beat; watch trajectory length | **adopted:** age tiers (G15, Phase 2); stock Hermes baseline in the replay (G16, Phase 0). The fixed-script replay cannot measure trajectory lengthening |
| P2 | [microsoft/acon](https://github.com/microsoft/acon) | paper-code | 2 commits | summary | an LLM compresses history and observations; the compression guideline is optimised from failure pairs, then distilled with LoRA; 26–54% lower peak tokens | N | N | ? | README; arXiv 2510.00615 | README | Tune rules offline from failures | **not adopted:** LLM-tuned guidelines (the paper is already in upstream `RESEARCH.md`) |
| P3 | [liyucheng09/Selective_Context](https://github.com/liyucheng09/Selective_Context) | paper-code | inactive since 2023 | extract | GPT-2 self-information prunes token, phrase or sentence units by `reduce_ratio` | N | ? | ? | README | README | Low-surprisal text is safe to drop; IDF approximates it | **already had:** rare-term IDF boost (upstream `RESEARCH.md`, CompactPrompt row) |
| P4 | [3DAgentWorld/Toolkit-for-Prompt-Compression](https://github.com/3DAgentWorld/Toolkit-for-Prompt-Compression) | paper-code | quiet since 2024 | eval | one Compressor/Dataset/Metric/Runner interface over Selective Context, LLMLingua, LongLLMLingua, SCRL, KiS; 11 datasets | – | – | – | README | README | A harness to benchmark against learned compressors | **not adopted:** benchmarking against learned compressors is not scheduled |
| P5 | [naver/bergen](https://github.com/naver/bergen/tree/main/scripts/provence) (Provence) | paper-code | ICLR 2025 | extract | DeBERTa model labels sentences keep/drop for a query and reranks; decides how much to prune per context; XProvence is multilingual | N | ? | ? | `scripts/provence`; model card `naver/provence-reranker-debertav3-v1` (blocked) | search-only | Pruning and ranking are one operation | **not adopted:** learned compressor |
| P6 | [carriex/recomp](https://github.com/carriex/recomp) | paper-code | 9 commits, dormant | extract · summary | extractive and abstractive compressors; "selective augmentation" may output nothing; ~6% compression figure recalled from the abstract (unverified) | N | ? | ? | README | README | "Nothing relevant" is a valid output | **not adopted:** learned compressor |
| P7 | [ThisIsHwang/EXIT](https://github.com/ThisIsHwang/EXIT) | paper-code | ACL Findings 2025 | extract | sentence classification that looks at neighbouring sentences | N | ? | ? | arXiv 2412.12559 | search-only | — | **not adopted:** learned compressor |
| P8 | [dmis-lab/CompAct](https://github.com/dmis-lab/CompAct) | paper-code | EMNLP 2024 | summary | iterative segment-by-segment document compression; reports 47× | N | ? | ? | arXiv 2407.09014 | search-only | — | **not adopted:** learned compressor |
| P9 | [YerbaPage/LongCodeZip](https://github.com/YerbaPage/LongCodeZip) | paper-code | ASE 2025; 31 commits | extract | functions ranked by conditional perplexity against the instruction, then blocks chosen by 0/1 knapsack under an adaptive budget; up to 5.6× | N | ? | ? | README; arXiv 2510.00446 | README | Knapsack selection under a budget | **not adopted:** scores need a model; knapsack not scheduled (the plan retries with `mode="fixed"` and a budget instead) |
| P10 | [Ayanami1314/swe-pruner](https://github.com/Ayanami1314/swe-pruner) | paper-code | training code June 2026; dataset Aug 2026 | extract | the agent writes a goal hint; a 0.6B "skimmer" selects lines of tool output; 23–54% fewer tokens on SWE-bench Verified | N | ? | ? | README; arXiv 2601.16746 | README | Use the goal hint as the query | **adopted:** goal-hint signal via `task_query` (G5, Phase 1a); **not adopted:** the model |
| P11 | [ace-agent/ace](https://github.com/ace-agent/ace) | paper-code | November 2025 | summary | Generator, Reflector, Curator roles; the Curator emits delta updates that are merged, de-duplicated and pruned deterministically | partial | ? | ? | README | README | Delta updates avoid collapse from repeated full rewrites | **not adopted:** LLM roles |
| P12 | [BytedTsinghua-SIA/MemAgent](https://github.com/BytedTsinghua-SIA/MemAgent) | paper-code | last activity mid-2025 | summary | reads input in chunks and overwrites a fixed-size memory each step; trained with RL (multi-conversation DAPO) | N | N | ? | README | README | — | **not adopted:** needs RL training |
| P13 | [Alibaba-NLP/DeepResearch](https://github.com/Alibaba-NLP/DeepResearch) (ReSum) | paper-code | not recorded | summary | periodic summarization with ReSumTool-30B trained by ReSum-GRPO | N | ? | ? | arXiv 2509.13313; repo "per search" | search-only | — | **not adopted:** needs RL training |
| P14 | [chroma-core/context-rot](https://github.com/chroma-core/context-rot) | paper-code | 4 commits | eval | scripts reproducing Chroma's "context rot" report (semantic needles, distractors, shuffled vs coherent haystacks, LongMemEval focused vs full) | – | – | – | README | README | Performance drops as input grows, even on trivial tasks | **already had:** the report is cited in upstream `RESEARCH.md` as the project's motivation |

### 3.4 Guides (3)

| # | Repo | Cat. | Activity seen | Stage | Technique | Det | Rev | Cache | Evidence | Verified by | Takeaway | Tameru status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D1 | [muratcankoylan/Agent-Skills-for-Context-Engineering](https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering) | guide | not recorded | eval | summarises Factory.ai's probe evaluation (36k+ production messages; probe types Recall, Artifact, Continuation, Decision; anchored iterative summarisation 3.70 vs Anthropic 3.44 vs OpenAI 3.35); advice: measure tokens per task, use the re-fetch rate, keep a separate artifact index; losses compound across cycles | – | – | – | `skills/context-compression/SKILL.md` (factory.ai blocked; per-dimension scores unverified) | README (doc) | Probe categories; re-fetch rate; artifact index | **adopted:** probe-tagged gold facts and re-fetch metric (G16, Phase 0); artifact trail (Phase 3 `ledger.py`); **not adopted:** LLM-judged probes |
| D2 | [humanlayer/12-factor-agents](https://github.com/humanlayer/12-factor-agents) | guide | not recorded | — | Factor 3 "Own your context window", Factor 9 "Compact errors into context window" | – | – | – | README | README | Principles only | **not adopted:** nothing implementable |
| D3 | [davidkimai/Context-Engineering](https://github.com/davidkimai/Context-Engineering) | guide | not recorded | — | course/handbook ("pruning beats padding") | – | – | – | README | README | — | **not adopted:** nothing implementable |

---

## 4. Gap matrix

Copied from the plan. Every row was verified by running code or by reading Hermes `main`; the Evidence
column says which.

### 4.1 Integration gaps (plugin repo)

| # | Gap | Evidence | Learned from |
|---|---|---|---|
| G1 | Tameru runs before Hermes' prune gates and returns a new list even when Hermes no-ops, which breaks the prompt cache on every call. (`__init__.py:47-55`; Hermes `context_compressor.py:3480-3560`) | PL:47-55 calls `apply_extractive_tool_prune` before `super().prune_tool_results_only` [HS risk 3]; Hermes gates and returns the input object on every no-op (`V:S5`) | opencode `PRUNE_MINIMUM`, Anthropic `clear_at_least`, SWE-agent `polling` |
| G2 | Already-pruned rows are re-compressed under each new query, so loss compounds. (Measured: one "continue" pass dropped 7 of 8 ERROR lines per result.) | 5 results × 19,143 chars: "continue" cut 3 of them to 6,413 chars (ERROR 8→1 each); "why are requests failing?" then re-cut the same 3 to 2,477 (a further 61%); `[…]` is not a recursion marker (`V:G2`) | Headroom keep-earliest, OpenClaw byte-identical projections |
| G3 | The post-check rejects the summary *after* Hermes has mutated its state. The LLM call is wasted, context is not reduced, and the check is O(n²) (91 s). | rejection returns `pruned` (PL:82) after `_previous_summary` (4131), `compression_count` and the rearm mark changed (`V:S6`); `_summary_preserves_required_facts` list membership: 40 tool messages (3.7 MB) took 91.9 s [INV §11] | OpenClaw safeguard, Factory artifact index |
| G4 | The `compress()` override drops `bypass_cooldown`. | Hermes signature `compress(messages, current_tokens=None, focus_topic=None, force=False, memory_context="", bypass_cooldown=False)` at 5656-5659 (`V:S6`); plugin override lacks it [HS risk 1] | — |
| G5 | The query is `str(last user content)`: multimodal content becomes a repr, base64 means no pruning at all, and filler replies produce risky cuts. | PL:51, 69; TR:48. List content becomes a Python repr; a base64 image makes a 40K-char query, trips the query limit, changed=0 (tested) [INV §12.2]; filler: E6 | SWE-Pruner goal hint, PAACE |
| G6 | The engine's risk rating is ignored, and the verifier can't see error loss (it reports risk "medium" with 0/96 errors kept). | TR:289 checks only `fail_open` and length [INV §12.1]; `_extract_critical_lines` counts every timestamped line as critical, so 0/96 ERROR kept reports `important_kept_pct` 0.877, risk medium; risk "low" seen with 7/10 ERROR dropped (engine-bugs risks) | Headroom `fail_closed_on_protected_loss` |
| G7 | Gaps are shown as a bare `[…]`; adapter cuts have no marker; there is no header. The Hermes marker blocks side-effecting calls that paste pruned text, but `[…]` gets no such protection. | bare gap CC:2265; adapter elision silent [INV §10]; Hermes guard `tool_dispatch_helpers.py:69-99` checks string arguments of side-effecting tools for `_COMPRESSION_MARKER_ARTIFACT_RE` (`V:T2`, `V:T3`) | Hermes `compression_marker`, Headroom `compression_summary` |
| G8 | Nothing that was pruned can be recovered (CCR is off and there is no tool). | CCR and citations off at TR:284-287 because no retrieval tool is registered [INV §5]; Hermes engines can expose tools via `get_tool_schemas` / `handle_tool_call` (`V:T1`) | headroom_retrieve, hermes-lcm `lcm_expand`, pi-fold, `rtk recall` |
| G9 | Hermes wrapper fields (`exit_code`, `error`, `total_lines`) are lost, which also breaks Hermes' own `"exit_code":` regexes. | `unwrap_hermes_tool` CC:706-721 returns only the inner text; `exit_code` missing in 3/3 non-fail-open runs; a `read_file` wrapper loses `path` and `total_lines` (`V:G9`) | RTK, Hermes `_summarize_tool_result` |
| G10 | One orphan `tool_call` id disables pruning for the rest of the session. | `_pending_tool_calls` TR:191-201 unions all ids; one early orphan then 6 answered rounds → `(False, 'pending-tool-calls')`, 0 changed (`V:G10`) | MS Agent Framework atomic groups |
| G11 | The user's `compression.*` config is ignored: plugin engines receive only `model_thresholds` (`agent_init.py:1993-2019`). | `V:L1`: constructor-level keys (threshold, protect_*, target_ratio, threshold_tokens, proactive_prune_*, tail_mode, min_tail_user_messages, abort_on_summary_failure) never reach a plugin engine; the micro-compact trio does via `setattr` | hermes-lcm config block |
| G12 | Packaging and runtime problems: the invalid `kind: context-engine`; pip users must also set `plugins.enabled`; sibling modules are executed eagerly; per-agent state, clone and isolation are not handled; `requires-python` is 3.10 (Hermes is 3.11–3.14). | invalid kind `plugins_manifest.py:27, 507-512`; `plugins_discovery.py:297-301` (`V:L6`); eager siblings `plugin_loader.py:122-131`; shared module state and clone path (`V:L4`); host isolation (`V:L8`); Hermes `requires-python = ">=3.11,<3.15"` (`V:L5`) | — |
| G13 | No config, telemetry, tests or CI; knobs are hard-coded. | `MIN_TOOL_CHARS`, `PROTECT_LAST_TOOL` hard-coded TR:27-28; no tests, no CI; receipt, verifier, reasons and `log_dir` discarded [INV §12.9] | pydantic-ai harness, OpenClaw `tools.allow/deny` |
| G14 | No structural supersession: stale reads after a later write or patch, re-reads of the same path or range, repeated identical calls and state-snapshot tools are not demoted. Hermes only de-duplicates byte-identical output. | Hermes pass 1 is md5 of byte-identical content ≥200 chars, keeping the newest [HS §1]; the engine's `supersession.py` only matches text cues such as "is now" and "deprecated" [CC] | Headroom `read_lifecycle`, OpenCode-DCP |
| G15 | No limit on total retained extracts and no decay with age: extracts accumulate, and full compaction triggers sooner. | the planned per-pass char budget bounds compute only; normal passes leave each extract (up to `max_extract_chars`) byte-stable indefinitely [CC] | OpenClaw soft-trim then hard-clear, OpenHands attention window, Complexity Trap |
| G16 | No honest metrics: no cost after cache pricing, no re-fetch rate, no baselines. | no telemetry and no replay harness in 1.3.0 [INV §12.9]; whittle's 22% vs 3–5% framing [LS]; Factory re-fetch signal (D1) [CC] | whittle, Factory probe eval |

### 4.2 Engine gaps (upstream repo)

| # | Gap | Evidence |
|---|---|---|
| E1 | `_CRIT_LINE_RE.search(line, re.IGNORECASE)` passes the flag as the `pos` argument (`compress_context.py:1123`). A naive fix also boosts every timestamped line, because the date alternative starts matching; scope the fix accordingly. | `re.IGNORECASE == 2`: `'Error: boom'`, `'ERROR boom'`, `'error: boom'`, `'FATAL x'` all fail to match as written; all match with the flag compiled in; a date at column 0 is skipped too (`V:E1`) |
| E2 | Quadratic hot spots: `_critical_line_recall` lowercases per line (about 10× slower with the non-ASCII `[…]`); `_summary_preserves_required_facts` uses list membership; three backtracking regexes. A 650 KB log takes 28–35 s. | 651,023 B, 9,600 lines, 1% ERROR: 28.50 s total, 27.62 s in `_critical_line_recall` (about 88% kept); a variant with 78% saved: 8.56 s. 20K-char runs: `_PATH_RE` 0.77 s, `_SUMMARY_STRUCTURED_TOKEN_RE` 1.03 s, `[\w]+[=:]` 2.18 s (5K: 0.06–0.14 s) (`V:E2`) |
| E3 | Error lines are lost in logs. Causes: the stale-error cap (1333), the `_important` exclusion (1373) and the floor-saturated path. The N6 restore re-adds collapsed duplicates at the top, out of order. The fix must keep `test_hardening_pass_v07` (traceback purge) passing. | 651 KB log: 96 ERROR in, 0 out, selection `floor`, 12.5% saved, `fail_open=False` (`V:E3a`); 200-line probe: N6 output puts 49 restored lines before the `[×50]` exemplar, double-reporting them (`V:E3b`); `OldErrorPurgeTests` asserts traceback purge (engine-bugs risks) |
| E4 | `cat -n` output is detected as TSV; `N\|code,` as CSV. `_looks_like_csv`/`preprocess_csv` (569-599) shreds even unnumbered code with commas (99.88% cut, risk "low"). `detect_format` classifies Python code as YAML. | `cat -n` of 300 functions: `tsv`, 99.9% saved, risk low, body lost (`V:E4a`); `N\|code` with commas: 99.87% saved via `_looks_like_csv` (`V:E4b`); unnumbered JS: 99.88% saved, risk low (engine-bugs risks) |
| E5 | Diffs are mis-detected as YAML and hunks get split. Grep output is mis-shaped (first hit taken as a CSV header, no per-file grouping). Test summaries are detected for vitest only. | diff with `@@ … def f():` context → `yaml`, wrong hunk returned, 99.95% saved, risk high (`V:E5a`, "fails open" refuted); grep → irrelevant head/tail rows or first hit as CSV header (`V:E5b`); `_TEST_SUMMARY_RE` CC:627 matches vitest only (`V:E5c`) |
| E6 | `_WEAK_NEXT_CHAT` lacks continue/thanks/ahead/proceed/ok/sure/lgtm. | `_topic_terms`: continue→`['continue']`, "go ahead"→`['ahead']`, sure, lgtm likewise; 400-line log: risk high, 67.25% saved, 1/8 ERROR kept; "ok", "fix it", "yes" already yield no terms (`V:E6`) |
| E7 | When query terms are absent, non-log text keeps everything, while logs return a collapsed version with `fail_open=False`. | CC:3668-3676 checks distinctive terms only; CC:3699-3706 flips logs back to `fail_open=False` (96.88% saved on a 400-line log); absent topic-only terms are not checked (`V:E7`) |
| E8 | No gap-marker or recursion hook; leading and trailing gaps are unmarked. | `_render` CC:2200 marks gaps only between kept blocks [DR]; `_RECURSION_MARKERS` CC:88 is fixed [INV §7] |
| E9 | No lossless fold stage (CR redraws, ANSI codes, trailing whitespace, identical runs, JSON minify with round-trip check). | `_norm_newlines` turns every `\r` into `\n`; `preprocess_json` re-serialises with `indent=2` and never minifies; ANSI stripped only on the test-runner path; `[×N]` only for logs [CC] |
| E10 | The log fingerprint masks digits only (no hex/UUID/IP/timestamp masking, no first/last instance). | `_log_fingerprint` replaces digits with `#` only [CC] |
| E11 | JSON that doesn't match the query is kept whole; there's no structural JSON brief. | `_crush_value` / `_crush_json_items` keep the whole array when no item matches, and skip entirely without distinctive selectors [CC] |
| E12 | Format is guessed from content; the tool name and command are not used as a hint. | `route_content_type` CC:249-271 guesses from the first 50 lines [INV §2]; the plugin knows the tool and command [CC] |

---

## 5. Adoption map

"Phase" refers to the plan's phases: 0 scaffold and baselines, 1a minimal upstream API, 1b upstream engine
quality, 2 seam ladder, 3 recovery tool and artifact trail, 4 telemetry and docs, 5 evaluate and tune. Test
files marked *(up)* live in the upstream repo; the rest are in this repo. Phase 0 and 1a files are being
written in the current workstream; files for Phases 1b and 2–5 do not exist yet.

| Lesson (source rows) | Gap | Phase | Test file(s) |
|---|---|---|---|
| Run inside the host's gates; return the input object on a no-op (H3, H17, L5; Anthropic `clear_at_least`) | G1 | 0 (pin), 2 | `tests/test_characterization.py` (xfail until fixed), `test_gates_respected.py` |
| Committed bytes never change; projections replay identically (H12, L1) | G2 | 2 | `test_idempotency.py` |
| Decide before committing; never post-check after the host changed state (H12 safeguard, H10 inflation check) | G3 | 0 (pin), 2 | `tests/test_characterization.py`, `test_compress_passthrough.py` |
| Pass host kwargs through untouched | G4 | 0 (pin), 2 | `tests/test_characterization.py`, `test_compress_passthrough.py` |
| Build the query from the task: focus, this call's args, next assistant text, real user text (P10, PAACE) | G5, E6 | 1a, 2 | `test_task_query.py` *(up)*, `test_weak_queries.py` *(up)* |
| Fail closed when protected lines are lost: every error fingerprint keeps an exemplar (L1 `audit_safe`) | G6 | 1a, 2 | `test_engine_helpers.py` *(up)*, `test_log_error_retention.py` *(up)*, `test_error_retention_gate.py` |
| Use the host's non-imitable marker; say what was dropped; mark every gap (H1, L1 `compression_summary`, H8) | G7, E8 | 0, 1a, 2 | `tests/test_seam_contract.py`, `test_gap_marker_hook.py` *(up)*, `test_markers.py` |
| A retrieval path for every cut (L1, L6, L7, L8, L2) | G8 | 3 | `test_expand_tool.py` |
| Keep wrapper metadata as compact JSON so host regexes still work (L2, H1) | G9 | 1a, 2 | `test_tool_payload_envelope.py` *(up)*, `test_hermes_payload.py` |
| Scope pending-call checks to the trailing turn (H21 atomic groups) | G10 | 1a | `test_trajectory_gate_scope.py` *(up)* |
| Honour the host's config; read plugin settings from the host's config file (L6) | G11 | 0 (pin), 2 | `tests/test_characterization.py`, `test_config.py` |
| Packaging and loader hygiene: valid manifest, Python ≥3.11, import-safe siblings, per-agent state | G12 | 0, 2, 4 | `tests/test_packaging.py`, `test_clone.py`, `test_seam_fallback.py` |
| Config surface, exempt tools, CI (H24, H12, H15) | G13 | 0, 2, 4 | `.github/workflows/ci.yml`, `test_config.py` |
| Structural supersession from tool-call arguments; demote, don't delete (L1 `read_lifecycle`, L5, H7, H24, L14) | G14 | 2 | `test_supersession.py` |
| Retained-extract budget; older results step down a tier (H12, H4, P1) | G15 | 2 | `test_retained_budget.py` |
| Cost after cache pricing, re-fetch rate, probe-category retention, baselines first (L3, D1, P1) | G16 | 0, 4, 5 | `tests/eval/test_session_replay.py`, `tests/eval/test_harness_units.py` |
| Case-insensitive error detection that ignores bare timestamps (L1 `error_detection`) | E1 | 1a | `test_crit_line_regex.py` *(up)* |
| Linear-time hot paths; bounded regexes | E2 | 1a | `test_perf_regressions.py` *(up)*, marked `perf` |
| Level-tagged error lines survive selection; restored in original order (L1 `log_compressor`) | E3 | 1a | `test_log_error_retention.py` *(up)*, `test_log_order.py` *(up)*, existing `test_hardening_pass_v07.py` *(up)* |
| Numbered reads and code are not tables; diff and grep adapters; more test runners (L2 grouping) | E4, E5 | 1b | `test_numbered_code_reads.py`, `test_code_not_csv.py`, `test_diff_grep_adapters.py`, `test_test_runner_preprocess.py` *(all up)* |
| Report the log-collapse escape instead of hiding it | E7 | 1a | covered by the 1a engine tests; no dedicated file named in the plan |
| Recursion guard only on explicit, anchored markers; the host's marker alone must not block compression | E8 | 1a | `test_recursion_markers.py` *(up)* |
| Lossless fold first, round-trip checked (L1 `lossless_compaction`, L3, H24 tiers) | E9 | 1b, 2 | `test_fold_lossless.py` *(up)*, `test_ladder.py` |
| Template masking before fingerprinting; first/last instance (L16) | E10 | 1b | `test_log_templates.py` *(up)* |
| Structural JSON brief; tabular encoding opt-in and measured (L1 SmartCrusher, L12) | E11 | 1b | `test_json_brief.py` *(up)* |
| Choose the format from the tool and command (L2) | E12 | 1a (kwarg), 2 (`payload.py`) | no dedicated file named yet; exercised by `test_ladder.py` |
| Deterministic briefs as the fallback rung, not a bare placeholder (L8, H16, H22) | ladder rung 6 | 1a, 2 | `test_engine_helpers.py` *(up)*, `test_ladder.py` |
| Second host demotion path (lean tail) must keep our ref (verification) | seam B | 2 | `test_lean_tail.py` |
| Artifact trail kept outside the summarizer (D1, H26) | ledger | 3 | `test_ledger.py` |

The nine retired upstream Hermes-gated tests (`test_compress_keeps_gold`, `test_compress_slash_agent`,
`test_init_agent_selects_extractive`, `test_new_agent_engine_load`, `test_prune_below_48k`,
`test_compress_preflight`, `test_live_engine_prune_holdout`, `test_extractive_engine_e2e`,
`test_install_extractive_engine`) are ported as intent to this repo's real-Hermes end-to-end tests.

---

## 6. Not adopted, and why

**Learned compressors** (LLMLingua L10, Provence P5, SWE-Pruner's skimmer P10, Headroom's Kompress, and in
the same family Selective Context P3, RECOMP P6, EXIT P7, CompAct P8, LongCodeZip's scorer P9).

- *Cross-lingual risk.* "Lost in Compression" (arXiv 2608.26175; 10 languages, 11 target models) found that
  English-trained learned compressors degrade badly on non-English context: at keep-rate 0.33, Chinese
  retained "essentially none". Deterministic methods showed no comparable gap. XProvence v2 emptied 92% of
  Chinese contexts at its aggressive threshold. (Figures from search snippets; the paper page was blocked.)
- *Determinism.* Committed rows must be byte-stable so the prompt cache survives and every decision can be
  replayed. A model in the per-result step makes output depend on model version and hardware.
- *Dependencies.* The engine is stdlib-only.

The goal-hint idea from SWE-Pruner is adopted without the model (G5).

**RL-trained folding** (AgentFold, Context-Folding/FoldGRPO, MemAgent P12, ReSum P13). All need trained
models; no code repo was found for AgentFold or Context-Folding. The transferable heuristic, folding at
sub-task boundaries, was noted but is not scheduled.

**Keep-earliest dedupe** (Headroom `cross_turn_dedup`). Hermes' pass 1 already de-duplicates and keeps the
*newest* copy. Prune commits are gated by Hermes, so the cache cost of keep-newest is bounded; doing the
opposite inside the per-result seam would fight the host's own dedupe. Tameru instead keeps committed bytes
stable (G2).

**Purging tool-call inputs** (OpenCode-DCP "purge errors" and "supersede writes" on inputs; deepagents
`truncate_args_settings`). Hermes never rewrites tool-call arguments, consistent with
`clear_tool_inputs=False` in LangChain's defaults and in OpenClaw's Anthropic configuration. The seam Tameru
hooks only sees tool results.

**Write-time compression** (whittle L3, RTK L2, deepagents eviction H16). Hermes deliberately spares the
pending tool round verbatim (unless it alone exceeds 20% of the window), so a fresh result reaches the model
before anything is cut. (Results over Hermes' tool-result budget are already replaced by a
`<persisted-output>` preview before any engine sees them.) Tameru acts only on results Hermes has already
decided to demote. Users who
want write-time filtering can run RTK's own Hermes plugin alongside; RTK recall lines can be pinned with
`protect_patterns`.

**Truncate-to-file** (gemini-cli H10, deepagents H16). Writing raw tool output to disk can persist secrets.
Originals go instead to a per-agent in-memory store that skips anything `contains_secret` flags, backed by
Hermes' own session-DB archive. Known limit: the engine's secret regex misses `AIza…`, `sk_live_…`, Bearer
headers and unquoted values [INV §7], so the store is conservative by design but not exhaustive.

**TTL-cold pruning** (OpenClaw H12). If more time than the provider's cache TTL has passed since the last
request, the prefix cache is already gone and a rewrite costs nothing extra. Using this needs a change to
Hermes' prune gates (threshold, rearm runway, min-reclaim), which a plugin cannot make cleanly through the
seam. It should be proposed upstream to Hermes, or later shipped as an opt-in setting.

**LLM-judged probe evaluation** (Factory, D1). The replay harness uses literal gold strings tagged with
Factory's four probe categories and runs offline in CI. An LLM-judged probe run is allowed only as an
optional offline job.

**Other items considered and not adopted:**

- LLM-tuned guidelines and roles (ACON P2, ACE P11): need an LLM in the loop; offline rule tuning from failure
  pairs was noted but not scheduled.
- Memory stores (mem0 L15, Graphiti L14, claude-mem L13, letta-code H14, Mastra observational memory H25):
  out of scope for a per-result step. Only the "demote, don't delete" principle is adopted (G14).
- KV-cache compression (kvpress L17): API-hosted models do not expose the KV cache.
- Code skeletons (repomix L11): need tree-sitter; the engine is stdlib-only.
- Knapsack selection (LongCodeZip P9): not scheduled; the plan retries with `mode="fixed"` and a budget.
- Per-result LLM summaries (goose H6) and background summaries (Context-Gateway L4): not deterministic; the
  LLM summary remains Hermes' job.

---

## 7. Results

Measured with the deterministic replay in `tests/eval` (definitions, metrics and caveats in
`tests/eval/README.md`): one scripted 30-turn session (62 model requests, about 380 KB of tool output), a fake lossy
summarizer, Hermes' rough token estimator, seed 0, Hermes pinned at `1298c8e`. Numbers are from the saved baselines
`tests/eval/baselines/{stock,tameru_1_4,tameru_1_4_product}.json`, re-recorded on the commit that vendors engine 1.4.0
( `VENDORED_FROM` `11b9e5e`; the
baselines pin the engine's output and must be re-recorded after every re-sync). Reproduce:

```bash
HERMES_REPO_ROOT=/path/to/hermes-agent python tests/eval/compare.py
HERMES_REPO_ROOT=/path/to/hermes-agent python -m pytest -q tests/eval      # includes the acceptance checks
```

`tameru_1_4` uses the same Hermes prune gates as stock (48K tokens, 8,000-character minimum result);
`tameru_1_4_product` is the plugin as shipped (2,000-character minimum result).

| | stock Hermes | plugin, equal gates | plugin, as shipped |
|---|---|---|---|
| cost after cache pricing (char-eq) | 1,273,007 | 1,015,225 (-20.3%) | 983,687 (-22.7%) |
| input chars, total | 6,361,016 | 5,457,084 | 5,257,665 |
| input chars, final request | 84,080 | 122,789 (1.46x) | 122,042 (1.45x) |
| compactions that shrank the list | 1 | 1 | 1 |
| prune commits (rows demoted) | 2 (8) | 1 (7) | 1 (9) |
| prefix chars invalidated | 469,751 | 285,486 | 276,150 |
| gold retained, of 17 | 10 | 13 | 12 |
| recall / artifact / continuation / decision | 0/6, 3/4, 3/3, 4/4 | 2/6, 4/4, 3/3, 4/4 | 1/6, 4/4, 3/3, 4/4 |
| error fingerprints retained | 0/7 | 7/7 | 7/7 |
| needed facts missing / calls to redo | 4 / 3 | 2 / 2 | 2 / 2 |
| committed rows rewritten between commits | 0 | 0 | 0 |
| tool-call pairing violations | 0 | 0 | 0 |
| engine time | about 0.15 s | about 2.1 s | about 2.0 s |

Tameru 1.3.0 on the same session (recorded in Phase 0, `baselines/tameru_1_3.json`): cost 2,215,464 (+74% over
stock), 13 prune commits, 6 summarizer calls, 0 shrinking compactions in 6 `compress()` calls, 5/7 fingerprints.

**Acceptance checks** (`test_acceptance_*` in `tests/eval/test_session_replay.py`; the release blocks on any
failure). All pass for both plugin configurations: cost after cache pricing <= stock; shrinking compactions <=
stock; gold retention >= stock in every probe category; error-fingerprint retention >= stock; re-fetch rate and
calls <= stock; committed rows byte-stable between commits; tool-call pairs intact. Over seeds 0 to 5 the cost
advantage stays between about -20% and -25%.

**What is not a win, stated plainly.** The final request is 1.45x to 1.46x stock's, because two large late results
arrive after the plugin's last cache-breaking rewrite and stay verbatim. The recall category beats stock only
narrowly (1 to 2 of 6 against 0 of 6). The replay is one story with a fake summarizer; it supports "cheaper at
equal or better retention in this scenario", not a claim about live sessions.

**Tuned defaults and how they were chosen** (details and rejected alternatives in `tests/eval/README.md`):
`retained_extract_budget_chars` 6,000 (swept 3,000 to 12,000; 3,000 lost fingerprints on one seed, 8,000 and up cost
about 1% more); `prune_tail` `tokens` (removes the dependence on where the one commit falls relative to a 20-message
tail and carries most of the cost win); `ledger` on (an `Errors:` block turned fingerprints from 0/7 to 7/7 for
about 1 KB once); `brief_chars` 1,200, `max_extract_chars` 6,000 and `max_risk` `medium` unchanged (moved cost by
under 1% or made it worse).

---

## 8. Sources

Accessed 2026-10-04. Repository links are in the section 3 tables; this list adds file-level links, papers,
docs and articles named in the reports. Pages marked "blocked" could not be opened from the survey
environment and were read through search results.

**Hermes**

- [Hermes context-engine plugin guide (raw)](https://raw.githubusercontent.com/NousResearch/hermes-agent/main/website/docs/developer-guide/context-engine-plugin.md)
- [Hermes `agent/context_engine.py` (raw)](https://raw.githubusercontent.com/NousResearch/hermes-agent/main/agent/context_engine.py)
- [RTK Hermes plugin page (search result; blocked)](https://hermes-agent.nousresearch.com/docs/plugins/rtk-rewrite)

**Headroom**

- [releases](https://github.com/chopratejas/headroom/releases) ·
  [smart_crusher.py](https://raw.githubusercontent.com/chopratejas/headroom/main/headroom/transforms/smart_crusher.py) ·
  [cross_turn_dedup.py](https://raw.githubusercontent.com/chopratejas/headroom/main/headroom/transforms/cross_turn_dedup.py) ·
  [read_lifecycle.py](https://raw.githubusercontent.com/chopratejas/headroom/main/headroom/transforms/read_lifecycle.py) ·
  [log_compressor.py](https://raw.githubusercontent.com/chopratejas/headroom/main/headroom/transforms/log_compressor.py) ·
  [lossless_compaction.py](https://raw.githubusercontent.com/chopratejas/headroom/main/headroom/transforms/lossless_compaction.py) ·
  [cache_aligner.py](https://raw.githubusercontent.com/chopratejas/headroom/main/headroom/transforms/cache_aligner.py) ·
  [ccr/tool_injection.py](https://raw.githubusercontent.com/chopratejas/headroom/main/headroom/ccr/tool_injection.py) ·
  [instagit CCR explainer](https://instagit.com/chopratejas/headroom/how-does-headrooms-ccr-architecture-enable-reversible-compression)

**Other tools and docs**

- [rtk releases](https://github.com/rtk-ai/rtk/releases)
- [Context Gateway article](https://emelia.io/hub/context-gateway-ai-agent-cost-reduction)
- [Codex compaction deep dive (secondary source)](https://codex.danielvaughan.com/2026/04/14/context-compaction-deep-dive-codex-cli-claude-code-opencode/)
- [Mastra observational memory docs](https://mastra.ai/docs/memory/observational-memory)
- [claude-mem beta features](https://docs.claude-mem.ai/beta-features)
- [Zep paper blog](https://blog.getzep.com/zep-a-temporal-knowledge-graph-architecture-for-agent-memory)
- [mem0 token-efficient memory algorithm](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm)
- [Factory: evaluating compression (blocked)](https://factory.ai/news/evaluating-compression)
- [Agent-Skills context-compression SKILL.md (raw)](https://raw.githubusercontent.com/muratcankoylan/Agent-Skills-for-Context-Engineering/main/skills/context-compression/SKILL.md)
- [Provence model card (blocked)](https://huggingface.co/naver/provence-reranker-debertav3-v1)

**Papers**

- [The Complexity Trap, arXiv 2508.21433](https://arxiv.org/abs/2508.21433) ([pdf](https://arxiv.org/pdf/2508.21433))
- [Masking regime map, arXiv 2606.00408](https://arxiv.org/pdf/2606.00408)
- [Lossless Context Management, arXiv 2605.04050](https://arxiv.org/pdf/2605.04050)
- [ACON, arXiv 2510.00615](https://arxiv.org/pdf/2510.00615)
- [Provence, arXiv 2501.16214](https://arxiv.org/html/2501.16214v1)
- [EXIT, arXiv 2412.12559](https://arxiv.org/html/2412.12559v3)
- [CompAct, arXiv 2407.09014](https://arxiv.org/pdf/2407.09014)
- [Lost in Compression, arXiv 2608.26175](https://huggingface.co/papers/2608.26175)
- [LongCodeZip, arXiv 2510.00446](https://arxiv.org/html/2510.00446v1)
- [SWE-Pruner, arXiv 2601.16746](https://arxiv.org/html/2601.16746v1)
- [ReSum, arXiv 2509.13313](https://arxiv.org/pdf/2509.13313)
- [AgentFold, arXiv 2510.24699](https://arxiv.org/pdf/2510.24699)
- [Context-Folding, arXiv 2510.11967](https://arxiv.org/pdf/2510.11967)

**Earlier research in this project**

- `tameru-compaction-system/docs/RESEARCH.md`: the JEV-related repos, pi-lcm, SelfCompact, Headroom and
  LLMLingua, and the papers Context Rot, LLMLingua-2, Provence, Lost in Compression, ACON, PAACE, TPC,
  CompactPrompt, Compactor, SelfCompact, CompactionRL and the memory-in-agents surveys.
