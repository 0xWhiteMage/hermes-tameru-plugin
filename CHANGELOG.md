# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semantic versioning.

## [1.4.0] - 2026-10-06

### Fixed (QA round 2, same release; see [docs/REPORT-1.4.0.md](docs/REPORT-1.4.0.md))
- **Oversized results lost the answer.** An extract over `max_extract_chars` got one fixed-budget retry. Log, grep
  and pytest output cannot shrink under it, so those rows fell to a fixed 1,200-char brief. Over 13 agent outputs,
  5 of 14 answers and 6 of 20 error templates were lost. Now:
  - fixed-budget retries iterate;
  - a forced cut must keep one of the engine's top query-evidence lines;
  - the brief grows in steps up to the cap until it holds every error template.

  After the fix: 12/14 answers, 20/20 error templates. New opt-in `brief_share`.
- `tameru_expand` refuses catastrophic-backtracking `grep` patterns (`(x|x)*y` hung the agent).
- Supersession no longer hides a result that answered a different question:
  - quoted spacing differs (`grep "a  b"` vs `grep "a b"`);
  - the same relative command before and after a `cd`;
  - `ls -R` followed by `ls`.
- Telemetry no longer counts renders for passes Hermes declined to commit.
- The status message honours Hermes' suppress flag and the kill switch.
- A note quoting `[tameru:` cannot hijack header parsing.
- Engine re-vendored at `11b9e5e`:
  - test-runner reduction keeps errors printed around a run;
  - log templates no longer merge messages that differ by a quoted name or path;
  - zero-count failure lines keep error protection;
  - JSON fold is byte-exact;
  - recursion markers are found inside JSON payloads.

### Fixed (QA round, same release)
- **Benchmark parity.** The task query included the tool's own command (`cat report.txt`); the engine reads a
  file name that is absent from the output as "the answer is not here" and keeps the whole result, so 11 of 14
  cases of the engine's QA corpus came back uncompressed and fell to the query-blind brief, which kept
  distractors in 5 and lost the answer in 2. Query parts that name something absent from the output are now
  dropped (`query.usable_query`), and the defaults match the engine's own: `max_risk` `high`, `min_savings` `0.10`.
  `tests/test_benchmark_parity.py` runs the corpus through the plugin: every gold string kept, no distractor.

Rebuilt on Hermes' own demotion seam, on the 1.4.0 Tameru engine. The "G" numbers refer to the gap matrix in
[docs/compaction-research.md](docs/compaction-research.md).

### Breaking changes

- **Requires Python >= 3.11** (was 3.10), matching Hermes.
- **The post-check is gone.** 1.3.0 ran its own prune, called Hermes' summarizer, and returned the pruned
  transcript if the summary lost "query facts" or bulky tool content. 1.4.0 hooks Hermes' per-result demotion
  instead and no longer second-guesses Hermes' compaction (G3).
- **`prune_tail` default is `tokens`.** The proactive prune protects the shorter of `protect_last_n` and the full
  compaction's tail token budget, and also runs Hermes' pass 4, instead of protecting 20 messages whatever they
  weigh. Set `prune_tail: count` for Hermes' behaviour.
- **`ledger` is on by default**: Hermes' compaction summary gains an exact artifact trail section, including an
  `Errors:` block.
- **New tool `tameru_expand` appears in the model's tool list** (`expand_tool: false` removes it).
- **Pruned rows look different**: Hermes' one-line summary followed by `[tameru:<rung> kept K/N lines ... ref=xxxxxxxx]`
  and an optional compact meta line, instead of bare `[…]` gaps. 1.3.0-era rows in existing sessions are
  recognized and never re-extracted.
- `proactive_prune_tokens` defaults to 48,000 and `proactive_prune_min_result_chars` to 2,000 unless the user's
  config sets them.
- Packaging: the invalid `kind: context-engine` manifest entry is gone; `plugin.yaml` carries only the name,
  description, version, licence and homepage.

### Added

- **Per-result ladder** inside Hermes' demotion step: supersession, query-aware extract, structural brief,
  header-only, then Hermes' own line. Hermes' gates, tail protection, `archive_and_compact` commit and pairing
  repair apply unchanged (G1, G14).
- **Structural supersession** (`supersession`): stale reads after a later write/patch, repeated reads of the same
  path and range, repeated identical calls and old state snapshots become header-only rows that say what
  superseded them.
- **`tameru_expand`** recovery tool and `ref=` on every rendered row; originals come from a per-agent store
  (`store_max_entries`, `store_max_chars`) and from the Hermes compaction archive (G8).
- **Artifact-trail ledger** in compaction summaries (files, commands with exit codes, error exemplars).
- **Retained-extract budget** (`retained_extract_budget_chars`, default 6,000): older rendered rows step down to a
  header with their ref kept, only inside a pass Hermes is already committing (G15).
- **Configuration**: `TameruSettings` with environment (`TAMERU_HERMES_<FIELD>`), `ctx.get_config` and
  `plugins.entries.tameru.settings` layers, and a kill switch (G13). The user's `compression.*` section is mirrored
  into the compressor (G11).
- **Telemetry** in `get_status()["tameru"]` and an optional JSONL log (`telemetry_log`).
- Lean-tail support: tail rows Hermes stubs keep a Tameru header with their ref.
- Seam probe at start-up with a stock-behaviour fallback and a stated reason when Hermes' internals differ.
- Test suite (stub Hermes and real Hermes), a seam contract test, a deterministic 30-turn replay harness
  (`tests/eval`) with saved baselines, and CI (`.github/workflows/ci.yml`).
- `docs/compaction-research.md`, `docs/DEVELOPMENT.md`, and the upstream commits as patches in
  `docs/upstream-patches/`.

### Fixed (user-visible behaviour)

- **G1** The plugin no longer prunes before Hermes' gates and returns a new list when Hermes would do nothing; the
  prompt-cache prefix is only broken in a pass Hermes commits.
- **G2** Rows already rendered are not re-compressed under a new query; committed bytes stay stable between
  commits, so loss no longer compounds (one "continue" turn used to drop most error lines from a result).
- **G3** No more wasted summarizer call whose result is then rejected after Hermes mutated its state, and no
  quadratic post-check (91 s on a large transcript).
- **G4** `compress(..., bypass_cooldown=...)` and other keyword arguments reach Hermes.
- **G5** The task query is built from the latest real user request: multimodal content, base64 and Hermes' own
  synthetic or summary rows no longer produce a repr or a filler query ("continue", "thanks", "ok").
- **G6** Error loss is now checked plugin-side: an extract must keep an exemplar of every ERROR/FAIL fingerprint
  of the original and stay within `max_risk`; otherwise the row falls to a brief.
- **G7** Gaps and cuts carry Hermes' own elision marker, and every rendered row has a header, so Hermes' guard
  against side-effecting calls that paste pruned text applies.
- **G9** Hermes' wrapper fields (`exit_code`, `error`, `total_lines`) survive on the row, and Hermes' own
  `"exit_code":` matching keeps working.
- **G10** One orphan tool-call id no longer disables pruning for the rest of the session.
- **G11** The user's `compression.*` settings are honoured by the plugin engine.
- **G12** pip installs work once enabled (`plugins.enabled`); sibling modules import safely under Hermes' directory
  loader; per-agent state, `clone_for_agent` and session reset are handled; session-store isolation is detected.
- **G14** Stale reads and repeated calls are demoted structurally.
- **G15** Retained extracts are budgeted and age down.
- **G16** Replay metrics after cache pricing, re-fetch rate and saved baselines exist; on the 30-turn replay the
  plugin costs 20.0% less than stock Hermes at equal gates (22.5% shipped), keeps 7/7 error fingerprints against
  stock's 0/7, and breaks no committed row. See `tests/eval/README.md`.

### Engine (Tameru 1.4.0, vendored)

- Critical-line regex flag bug fixed; quadratic hot spots removed (a 650 KB log: 22 s to under 1 s).
- Logs keep one exemplar per error fingerprint, in order; Drain3-style template masking with a `[last]` exemplar.
- Numbered file reads (`cat -n`, `N|`, `N:`), unified diffs, grep/rg output and pytest/jest/go/cargo summaries have
  adapters; code is no longer read as CSV/TSV/YAML. A query that matches nothing fails open.
- Lossless folding (CR redraws, ANSI codes, trailing whitespace, identical runs, round-trip-checked JSON minify)
  and a JSON brief.
- Filler queries no longer cause high-risk cuts.
- Vendored from upstream commit `11b9e5e` (see `VENDORED_FROM`). The three upstream commits are in
  `docs/upstream-patches/`.

## [1.3.0]

Initial release: standalone Hermes plugin wrapping Tameru 1.3.0's extractive tool-result prune around Hermes'
summarizer, with a post-check that returned the pruned transcript when the summary lost query facts.
