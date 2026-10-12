# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semantic versioning.

## [Unreleased] (1.4.1 candidate)

Fixes from the first live Hermes sessions and for the two answers 1.4.0 still lost. Numbers are in
[docs/REPORT-1.4.1.md](docs/REPORT-1.4.1.md). The vendored engine is the local 1.4.1 candidate (`e099d10`, not upstream
yet; patches in [docs/upstream-patches/](docs/upstream-patches/README.md)). After the red-team round the vendored engine
is `a1e8a7f` (`e099d10` plus one secrets-screen fix).

### Fix-all round (same branches, see [REPORT-1.4.1.md §8](docs/REPORT-1.4.1.md))
- **`tameru_expand` grep can no longer hang the agent** (R4, open since 1.4.0). A pattern with a repeat or an
  alternation now runs in a child Python process (`-I -S`, stdlib only) that is killed after
  `expand_grep_timeout` seconds (default 2, `TAMERU_HERMES_EXPAND_GREP_TIMEOUT`, 0.05-600) with
  "grep timed out ... simplify it". `.*.*.*x` on a 3,000-char line (>275 s before) now returns that error after
  2.00 s (1.00 s at a 1 s timeout). Repeat-free patterns stay in-process. Lines are capped at `expand_grep_line_chars` (default
  20,000) before matching (`lines_capped` in the result). The nested-repeat pre-check is unchanged. Cost on a
  5,000-line original (median of 20): repeat-free 3.0-3.8 to 3.4-3.7 ms, child path 3.2-11.4 to 21-26 ms per call. Uses `subprocess.run` with a
  timeout (TerminateProcess on Windows); only tested on Linux.
- **Briefs honour the pass-2 reservation** (R5). The room left for the newer rows of a pass is now taken from an
  estimate of each row's own brief (`max(brief_chars, brief_share * size)`, one growth step for a row with an error
  fingerprint, capped at its extract cap) instead of a flat `brief_chars`, and it limits the brief as well as the
  extract. Reservation probe (6 orders x 4 budgets): the newest row keeps a body in 24/24 cases (7/24 before), no
  brief under 400 chars (6 before); a tight budget is used less fully in some orders (e.g. 2,321 of 3,000).
  Replay product arm: gold 12/17 to 13/17.
- **Secrets screen** (R3, engine `91f00e0`): a whole-line env/shell (`DB_PASSWORD=correct_horse_battery_staple`) or
  YAML (`secret: correct_horse_battery_staple`) assignment is a secret again, whatever the value looks like, unless it
  is an expression or placeholder, ends in `,` `;` `)` (a keyword argument on its own line) or, in YAML, is an
  attribute chain. Hermes probe: 15 of 2,372 non-test `.py` files before and after. Still not flagged: the same values
  inside code (`password = settings_default_password`), which cannot be told apart from code.
- **Brief rung inlines short gaps** (R8, engine `b391ed9`): `brief_context` honours `InliningGapMarker`, and the brief
  rung uses it, so a dropped line shorter than Hermes' ~100-char marker stays verbatim, as in extracts.
- **Spill check tolerates small format changes** (R1): the untrusted-wrapper header may carry any attributes in any
  order, other case, CRLF or trailing blanks, a 0-4 line notice, extra blank lines, or be nested up to 3 deep. The
  notice must still end in a blank line.
- **Refs are 64 bits** (R7): `make_ref` returns 16 hex chars. A pre-change ref is the first 8 of them; headers,
  `tameru_expand`, the store and the session-archive lookup accept both (an 8-hex ref that prefixes two keys is a
  miss). Committed rows keep their bytes. Cost: 8 chars per rendered header.
- Tests (R8): the `[read_file]` case of the bare-tool-name test now fails without the filter; the inline-gap test
  asserts at the brief rung too; `pyyaml`, `setuptools` and `hypothesis` are in the `dev` extra, so the direct
  `config.yaml` and offline packaging tests run.
- Measured against the red-team head `d8fd2d6`: replay `tameru_1_4` 13/17 at 1,019,153 to 13/17 at 1,019,675
  cache-priced; product 12/17 at 990,003 to 13/17 at 989,114; per-result 14/14 answers, 22/22 templates both; engine
  corpus through the plugin 67.5% to 67.2% saved (the longer refs). Baselines re-recorded.

### Red-team round (see [REPORT-1.4.1.md §7](docs/REPORT-1.4.1.md))
- **A spilled web/browser/MCP result was no longer recognised as a spill** (regression in this candidate). Hermes wraps
  `web_extract`, `web_search`, `browser_*` and `mcp_*` results in `<untrusted_tool_result>` after the spill, so the
  "starts with the tag" test missed them: they took the brief rung (~1k of the external preview) instead of the 1.4.0
  `spilled to <path>` header. `is_persisted_block` now looks past that wrapper header.
- **`/compress terminal` lost its focus on `terminal` rows** (regression in this candidate): the query de-duplicates the
  focus and the call name into one part, and the bare-tool-name filter dropped it. A part equal to the focus stays.
- **The recovery store refused ordinary source files** (regression from engine `173a948`): the new unquoted
  `KEY=value` secrets pattern matched code such as `password = get_password_from_vault()` (141 of 2,372 non-test
  Hermes `.py` files, 9 before), and the store never keeps an original with a secret, so `tameru_expand` could not serve
  them from the store. Engine `a1e8a7f` excludes code expressions (15 of 2,372 files, all literal fixture keys).
- Added `tests/test_fuzz_redteam.py` (hypothesis; skipped without it) and `tests/test_redteam_141.py`.
- Left open in this round, fixed in the fix-all round above: polynomial-time `grep` regexes in `tameru_expand`
  (`.*.*x` on a long line), briefs not covered by the pass-2 reservation, very small briefs (~250-330 chars) at the end
  of a tight budget.

### Fixed
- **A result that quoted `<persisted-output>` was taken for a Hermes spill.** A live 29.5K `git log` that mentioned
  the tag was reduced to the spill one-liner. A result is a spill only when it starts with the tag, or with Hermes'
  untrusted-data wrapper header followed by the tag (`hermes_compat.is_persisted_block`; the wrapper case was added in
  the red-team round). Hermes may append subdirectory hints after the closing tag, so "ends with" would be wrong.
- **Both answers 1.4.0 lost are kept** (per-result QA set, `tests/eval/per_result.py`: 12/14 answers in 1.4.0,
  14/14 now, 22/22 error templates both):
  - a pytest failure with a long captured log now takes the extract (the engine collapses the captured section);
  - a saturated `rg -n Name` keeps the definition line (the engine no longer treats `-n` as the query's selector,
    and one host marker covers a run of fully omitted files instead of one ~100-char marker each).
- **Replay regression in the 1.4.1 work, before release.** With the larger pytest extract, one old row used the
  whole retained budget in pass 2 (Hermes visits oldest first) and the newer rows fell to headers: `tameru_1_4`
  gold 13/17 went to 10/17. An extract in pass 2 now leaves `brief_chars` per later row that could still be
  rendered. Gold is back to 13/17 (equal gates) and 12/17 (product), as in 1.4.0.
- `tests/eval/compare.py` always uses a throwaway `HERMES_HOME` (a replay writes Hermes state). Opt out with
  `TAMERU_EVAL_HERMES_HOME=<dir>` or `TAMERU_EVAL_KEEP_HERMES_HOME=1`.

### Changed
- The extract query no longer includes the bare tool name (`terminal`, `read_file`): every row of a tool shares it,
  so it only selected incidental lines. Engine corpus through the plugin: mean 65.9% to 67.5% saved, `git_log`
  69.1% to 87.6%, gold 15/15 before and after.
- Short gaps are inlined when Hermes' ~100-char marker would be longer than the text it replaces (engine
  `inline_short_gaps`). Engine corpus: `travis_yaml` 13.5% to 23.4%, `jp_musubi` 26.5% to 29.4%.
- Product-arm replay cost: 986,072 to 990,003 cache-priced (+0.4%), baseline re-recorded.

### Added
- `tests/eval/per_result.py`: one demotion per agent output (rung, saved %, answers, error templates) and the
  engine corpus through the plugin and through the engine alone.

### Notes
- `tameru_expand` is only offered when Hermes injects context-engine tools: no `-t`/`enabled_toolsets` restriction,
  or one that includes `context_engine`. `hermes chat -t terminal,file` hides it.
- Newer Hermes logs `capability_check ... capability=tools.override decision=deny evidence=not granted` at every
  plugin load. That is Hermes probing the tool-override policy; it is not about `tameru_expand` and needs no action.

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
