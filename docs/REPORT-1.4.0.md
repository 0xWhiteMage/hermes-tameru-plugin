# Tameru 1.4.0 for Hermes: what changed, what improved, and what it cost

Report date: 2026-10-06. It covers the plugin (`hermes-tameru-plugin`, branch `claude/funny-albattani-gac36w`)
and the engine it vendors (`tameru-compaction-system`, local commits `4d43819`, `fd8c5b0`, `11b9e5e`, exported in
[upstream-patches/](upstream-patches/README.md)). Every number below was measured in this environment. The scripts
are in the repo or described under [Reproduce](#reproduce).

## 1. Summary

| Question | Answer |
|---|---|
| Did the engine get worse on your benchmarks? | **No.** `run_battery.py` 15/15 with identical per-case savings; `threshold_sweep.py` byte-identical (77.3 / 68.4 / 61.2 / 41.5 / 24.9% at budget 0.1 to 0.7). |
| Did the engine get better? | **Yes, on agent tool output**, which the benchmarks do not cover. It keeps every error template of a log (1.3.0 dropped some), keeps function bodies of code with commas (1.3.0 cut 99% and lost them), and keeps diff hunks and pytest failures whole. A 650 KB log takes 1.0 s instead of 18.7 s. |
| Is the Hermes plugin better than 1.3.0? | **Yes, in a whole session.** Over a 30-turn replay it costs **54 to 56% less** than 1.3.0 after prompt-cache pricing. It makes 1 compaction instead of 4 and breaks the cache prefix 2 times instead of 17. It keeps 7/7 error templates (1.3.0: 5/7). |
| Is it better than stock Hermes? | **Yes:** cost **−19.9%** (equal prune gates) and **−22.5%** (as shipped). Gold facts 13/17 and 12/17 against 10/17. Error templates 7/7 against 0/7. Fewer re-fetches. |
| Where is it worse? | Per single tool result it keeps fewer bytes of some answers than 1.3.0 did: 12 of 14 answer strings against 13 of 14. That is because it compresses much harder (81% saved against 54%). On the engine's own QA corpus it saves less than the engine alone (67% against about 83%), because of Hermes' long gap markers and the row header. The ladder adds about 2.8 s of CPU over a 30-turn session (stock: 0.15 s). |
| Bugs found in this QA round | **19 fixed, each with a regression test.** Plugin: 7 found and fixed by the plugin reviewer, plus 2 fixed by me (the oversized-result fix and a brief-loop hang the reviewer found). Engine: 9 found and fixed by the engine reviewer, plus the log-template over-masking it found, which I fixed. The open items are in section 8. |

## 2. What was compared

| Name | What it is |
|---|---|
| **stock** | Hermes `ContextCompressor` at `1298c8e`, proactive prune at 48K tokens. Its per-result demotion turns an old tool result into a one-line summary. |
| **plugin 1.3.0** | `main` (`d614758`): 87 lines. It ran `compress_context` over every old tool result before Hermes' gates, then Hermes' summarizer, then a post-check that could reject the summary. No tests. |
| **plugin 1.4.0** | This branch: a `ContextCompressor` subclass that hooks Hermes' per-result demotion seam (about 3,800 lines, plus 12,000 lines of tests). |
| **engine 1.3.0 / 1.4.0** | `tameru-compaction-system` at `b480ab3` and at `11b9e5e`. |

## 3. How it was upgraded

1. **Research.** Surveyed 62 compaction repos and 17 papers ([compaction-research.md](compaction-research.md)). The field has converged on one pipeline:
   - lossless folding;
   - structural supersession;
   - per-result extractive briefs inside the host's gates, with stable bytes, markers and a recovery path;
   - age decay;
   - the LLM summary last.

   Hermes already does most of this, so Tameru became Hermes' per-result step instead of a wrapper around it.
2. **Gap analysis.** Found 16 integration gaps (G1 to G16) and 12 engine gaps (E1 to E12). Each was verified by running code against Hermes `main`.
3. **Engine first, upstream.** Fixed E1 to E12 in `tameru-compaction-system` (1.4.0), then vendored the result. Upstream cannot be pushed from this environment (no write access), so the commits ship as patches.
4. **Plugin rebuilt on Hermes' seams.** Hermes' gates, tail protection, archive and pairing repair now decide *when*; Tameru decides *what a row becomes*.
5. **Measurement before tuning.** Built a deterministic 30-turn replay with prompt-cache pricing, gold facts by Factory probe category, error fingerprints, re-fetch, pairing and byte-stability checks. Baselines for stock and plugin 1.3.0 were recorded before any change.
6. **Two QA rounds.**
   - Round 1 found a query defect that made the plugin fail open on 11 of 14 benchmark cases (fixed; [QA-2026-10.md](QA-2026-10.md)).
   - Round 2 is this report. It added a per-tool-result comparison against 1.3.0 and stock on agent outputs, plus two adversarial reviewers.

## 4. What changed

### Engine 1.3.0 → 1.4.0 (54 files, +8,598 / −937; tests 329 → 1,125)

| Area | Change |
|---|---|
| Correctness | Fixed the critical-line regex flag bug (E1). Error lines in logs keep one exemplar per error template (E3). Code is no longer cut as CSV or YAML (E4). Filler queries ("continue", "thanks") no longer cause deep cuts (E6). |
| Speed | Removed quadratic hot spots (E2). A 650 KB log takes 1.0 s instead of 18.7 s. |
| Agent formats | Numbered `cat -n` and `read_file` output. Unified diff: whole hunks, per-file stats. grep/rg: grouped by file. pytest/jest/go/cargo: failure blocks and summaries (E5). |
| New stages | Lossless folding: terminal redraws, ANSI, identical runs, byte-exact JSON minify (E9). Drain-style log templates with a `[last]` instance (E10). Valid-JSON brief of large arrays (E11). |
| Plugin-facing API | `gap_marker`, `recursion_markers`, `content_hint` (E8, E12). Helpers: `error_fingerprints`, `brief_context`, `extract_evidence_lines`, `contains_secret`, payload unwrap/rewrap, `task_query`, `ENGINE_VERSION`. |

### Plugin 1.3.0 → 1.4.0

| Gap | 1.3.0 | 1.4.0 |
|---|---|---|
| G1 prompt cache | Pruned before Hermes' gates and returned a new list every call, so the cache broke on every request. | Runs inside the pass Hermes commits; no-op passes return the input object. |
| G2 compounding loss | Re-compressed rows already pruned under each new query (one "continue" dropped 7 of 8 error lines). | Rendered rows are byte-stable until Hermes itself demotes them. |
| G3 post-check | Rejected the summary after Hermes had mutated state; O(n²), 91 s on a big transcript. | Removed. |
| G5 query | `str(last user message)`; multimodal content became a repr. | Task query from the user's words and the surrounding calls, cleaned, minus parts that would force fail-open. |
| G6 error loss | Not checked. | An extract must keep every error template; the brief grows until it does. |
| G7 / G8 / G9 markers, recovery, envelope | Bare `[…]` gaps; nothing recoverable; `exit_code` lost. | Hermes' own markers. Each row carries a header with a `ref`; `tameru_expand(ref)` returns the original; `exit_code`/`error` survive. |
| G11 config | The user's `compression.*` settings were ignored. | Mirrored, plus `TameruSettings` with a kill switch. |
| G14 / G15 supersession, budget | None. | Stale reads, re-reads and repeated calls become one-line headers; a total body budget makes old rows step down. |
| G13 / G16 tests, metrics | None. | 1,078 stub-mode tests (1,337 against real Hermes), a seam contract test, CI, telemetry, and the replay harness. |

## 5. Results

### 5.1 Engine benchmarks: unchanged

`run_battery.py`: 15/15 on both versions, same savings per case. `threshold_sweep.py`: identical output. The
benchmarks contain no agent tool output, which is why the engine work does not show there.

### 5.2 Engine alone on agent tool output

Default call `compress_context(text, question, ccr=False, citations=False)`. "errs" means error templates kept.

| Output | Size | 1.3.0 saved | gold | errs | time | 1.4.0 saved | gold | errs | time |
|---|---:|---:|---|---|---:|---:|---|---|---:|
| app log, real question | 49 KB | 22% | – | 6/7 | 0.23 s | 16% | – | **7/7** | 0.09 s |
| app log, "continue" | 49 KB | 29% | – | 2/7 | 0.18 s | 0% (kept whole) | – | **7/7** | 0.10 s |
| 650 KB log | 636 KB | 57% | – | 3/5 | 18.7 s | 56% | – | **5/5** | **1.0 s** |
| `cat -n` source | 11 KB | 0% | 2/2 | – | | **34%** | 2/2 | – | |
| source with commas | 9 KB | 99% | **0/2** | – | | 31% | **2/2** | – | |
| `git diff` | 8 KB | 79% | 1/2 | – | | 63% | **2/2** | – | |
| `rg -n` | 12 KB | 0% | 1/1 | – | | 14% | 1/1 | – | |
| pytest failure | 7 KB | 95% | **0/2** | 1/1 | | 0% (kept whole) | **2/2** | 1/1 | |
| pytest -v, all green | 26 KB | 77% | 1/1 | – | | **99%** | 1/1 | – | |
| JSON API dump | 47 KB | 99.6% | 1/1 | – | | 99.6% | 1/1 | – | |
| YAML config | 2 KB | 68% | 1/1 | – | | 68% | 1/1 | – | |
| CJK log | 9 KB | 92% | 1/1 | – | | 84% | 1/1 | – | |
| `git log --stat` | 13 KB | 33% | 1/1 | – | | 33% | 1/1 | – | |

Where 1.3.0 saved more, it did so by dropping the answer or the errors: source with commas (99%, answer lost),
pytest (95%, answer lost) and the logs (error templates lost).

### 5.3 Plugin, one tool result at a time

Each output is wrapped as Hermes would store it and demoted once. "Saved" is the share of the row's characters removed.

- **Stock** writes Hermes' one-liner.
- **Plugin 1.3.0** is its `compress_context` call on the row, without its 800-char floor, its timing gate or its
  last-2-results protection. That flatters it: for example, it would leave `zh_needle` untouched.
- **Plugin 1.4.0** is the ladder with shipped defaults; "rung" is the step it reached.

**13 agent outputs** (14 answer strings, 20 error templates):

| | stock | plugin 1.3.0 | plugin 1.4.0 before QA2 | **plugin 1.4.0** |
|---|---:|---:|---:|---:|
| mean saved | 99.5% | 53.7% | 82.0% | **80.8%** |
| answers kept | 0/14 | 13/14 | 9/14 | **12/14** |
| error templates kept | 0/20 | 12/20 | 14/20 | **20/20** |

Per case for 1.4.0 (rung, saved, answers):
- **brief:** 3 logs (95 to 100%, every error template); `rg` (90%, answer lost); pytest failure (84%, 1 of 2); `git log` (91%).
- **extract:** `cat -n` and source (50 to 53%, 2/2); diff (59%, 2/2); pytest -v (98%); JSON (99%); YAML (55%); CJK (82%).

**Engine QA corpus** (13 cases, 16 gold strings, with distractors):

| | stock | plugin 1.3.0 | plugin 1.4.0 |
|---|---:|---:|---:|
| mean saved | 97.2% | 81.6% | 66.6% |
| gold kept | 0/16 | 16/16 | 15/16 |
| distractors kept | 0 | 0 | 0 |

The one 1.4.0 miss is `zh_needle`, 561 characters. It is under the 800-character floor, so it gets Hermes'
one-liner, exactly as stock does. 1.3.0 would also have skipped it with its own floor.

### 5.4 Whole session: 30-turn replay

Same scripted session, Hermes' real compressor with a fake lossy summarizer, and prompt-cache pricing (cache read
0.1×, write 1.25×). Plugin 1.3.0 ran with engine 1.3.0.

| Metric | stock | plugin 1.3.0 | **1.4.0 equal gates** | **1.4.0 shipped** |
|---|---:|---:|---:|---:|
| cost, cache-priced (char-eq.) | 1,273,007 | 2,215,464 (+74%) | **1,019,153 (−19.9%)** | **986,072 (−22.5%)** |
| total input chars | 6.36 M | 7.31 M | 5.48 M | 5.27 M |
| full compactions (shrinking) | 1 (1) | 4 (0) | 1 (1) | 1 (1) |
| summary LLM calls | 1 | 6 | 1 | 1 |
| prune commits | 2 | 13 | 1 | 1 |
| prefix break events | 3 | 17 | 2 | 2 |
| prefix chars invalidated | 469,751 | 1,080,761 | 286,438 | 276,831 |
| gold facts kept | 10/17 | 12/17 | **13/17** | 12/17 |
| error templates kept | 0/7 | 5/7 | **7/7** | **7/7** |
| re-fetch opportunities (facts) | 3 (4) | 3 (4) | 2 (2) | 2 (2) |
| tool-call pairing violations | 0 | 0 | 0 | 0 |
| rows rewritten between commits | 0 | n/a | 0 | 0 |
| final request chars | 84,080 | 210,345 | 123,115 | 122,126 |
| engine CPU seconds | 0.15 | n/a | 2.80 | 2.84 |

Gold by probe category for 1.4.0 equal gates (stock in brackets): recall 2/6 (0/6), artifact 4/4 (3/4),
continuation 3/3 (3/3), decision 4/4 (4/4).

## 6. What is better, in short

- **Cheaper sessions.** About 20 to 23% below stock and 54 to 56% below plugin 1.3.0 after cache pricing. The plugin
  no longer breaks the prompt cache on its own schedule.
- **Errors survive.** Every distinct error template is kept, in the replay (7/7) and in every agent-output case (20/20).
- **Code, diffs and test failures stay readable.** No more 99% cuts through a source file; hunks and failure blocks stay whole.
- **Recoverable.** Every rendered row has a `ref`, and `tameru_expand` returns the exact original lines.
- **Safer on filler turns.** "continue" no longer triggers deep cuts.
- **Faster engine** on large logs: 18×.
- **Tested.** From 0 to over 1,300 plugin tests, including a contract test that detects a Hermes refactor of the seams.

## 7. QA round 2: findings and fixes

### Found by the per-result comparison (fixed by me)

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | Results whose extract was over the 6,000-character cap got one fixed-budget retry. Log, grep and pytest output cannot shrink under it, and code overshot by a few characters. They fell to a fixed 1,200-character brief, which lost the answer in 5 of 14 cases and dropped 6 of 20 error templates. | Up to two fixed-budget retries. A forced cut must keep one of the engine's top query-evidence lines. The brief grows (×1.5 steps, up to the cap) until it holds every error template. |
| 2 | Medium | Within one pass, Hermes visits rows oldest first, so the retained budget goes oldest first. | Measured both orders. Newest first cost 2 of 6 recall facts in the replay, so the order is kept and documented. |
| 3 | High | The new brief's growth loop never ended with `brief_chars=0` (found by the plugin reviewer). | It always grows; regression test added. |

`brief_share` (opt-in, default 0) starts the brief at a share of the result. At 0.25 it keeps one more answer
per result but costs 2 recall facts in the replay, so it is off by default.

### Plugin reviewer (fixed, with tests)

| Severity | Finding |
|---|---|
| High | `tameru_expand` ran any `grep` regex: `(x\|x)*y` took 13 s on 26 characters, doubling per character, and hung the agent. Nested repeats are now refused. |
| Medium | Supersession hid results that answered different questions: `grep "a  b"` and `grep "a b"` (spaces inside quotes), and the same relative command before and after a `cd`. |
| Medium | Telemetry counted renders and saved characters for passes that Hermes declined to commit, twice with the escalation re-run. |
| Low | A shallow `ls` superseded an earlier `ls -R`. The status message ignored Hermes' suppress flag and the kill switch. A note quoting `[tameru:` text could hijack header parsing. |

### Engine reviewer (fixed upstream in `11b9e5e`, with tests)

| Severity | Finding |
|---|---|
| High | Test-runner reduction dropped errors printed around a run: a `=====` rule in captured output ended the pytest failure section; compiler, `make`, tox, cargo `error[E…]` and `npm ERR!` lines were lost. |
| High (fixed by me) | Log templates masked every quoted value and path, merging distinct messages and errors (`table "orders"` / `"payments"`, `a.db` / `b.db`). These are now masked only when they hold a digit. |
| Medium | A line such as `Failures: 2, Errors: 0 … FAILURE!` counted as negated, so it lost error protection. Recursion markers were missed inside JSON payloads. JSON fold re-serialised numbers and strings (`1.10` → `1.1`, lone surrogates); it is now byte-exact. A 4,300-digit integer crashed a JSON scan. |
| Low | Fold idempotence with literal run markers; a trailing CR run erased a line; `content_hint="code"` on numbered reads. |

## 8. Trade-offs and open items

- **The engine reaches less of its own corpus savings through the plugin:** 67% against about 83%. Causes:
  - Hermes' elision marker is about 100 characters per gap, against `[…]`;
  - each row carries a header.

  Worst cases: `travis_yaml` (14% against 64%) and `jp_musubi` (27%).
- **Two answers still lost per result**, both on the brief rung:
  - an `rg -n` hit, where every line matches the search term, so the extract cannot shrink;
  - one pytest assertion line, because the engine keeps a failure block whole, including 60 lines of captured
    log, so it fails open.

  Possible engine follow-up: collapse captured-log sections inside failure blocks.
- **Hex runs of 6+ characters are still masked in log templates**, so two errors that differ only by a hash
  (`deadbeef01` / `cafebabe02`) share a template. That is intended for trace and request ids; quoted names and
  paths without digits are no longer merged.
- **The diff adapter is bypassed for plain-word queries**, as in 1.3.0: generic scoring then splits hunks. YAML
  with many shell keywords can be read as text.
- **The final request is 1.46× stock's.** Two large late results arrive after the last cache-breaking commit and
  stay verbatim. Total and cache-priced cost are still lower.
- **Engine CPU:** about 2.8 s over 30 turns (stock 0.15 s).
- **The evidence is a deterministic replay, a corpus and fixtures.** Nothing has run in a live Hermes session with
  a real model, and savings are counted in characters, not tokenizer tokens.
- **Upstream is not pushed.** The three commits are in [upstream-patches/](upstream-patches/README.md), and the
  local `v1.4.0` tag points at `11b9e5e`. The plugin does not depend on them being applied.

## Reproduce

```bash
# plugin suites
python3 -m pytest -q -p no:cacheprovider                                  # stub: 1078 passed, 266 skipped
HERMES_REPO_ROOT=<hermes@1298c8e> python3.14 -m pytest -q -p no:cacheprovider   # real: 1339 passed, 5 skipped
HERMES_REPO_ROOT=<hermes@1298c8e> python3.14 tests/eval/compare.py        # the replay table above

# engine (in tameru-compaction-system)
python3 -m pytest -q -p no:cacheprovider     # 1125 passed, 3 skipped
python3 benchmarks/run_battery.py            # 15/15 (rewrites production-qa-v3-results.json timings)
python3 benchmarks/threshold_sweep.py
```

The per-result comparison (sections 5.2 and 5.3) builds its inputs with `tests/fixtures/hermes_payloads.py` and the
650 KB log generator in upstream `tests/test_log_error_retention.py`. It calls each engine version on the same
text and demotes each output once through stock Hermes and through the plugin, as in
`tests/test_benchmark_parity.py`.
