# Session replay harness (`tests/eval`)

A deterministic, offline, 30-turn simulated Hermes session that measures a context engine. It exists to
answer one question with numbers: *does an engine cost less, compact less, and remember at least as much as
stock Hermes?* Phase 0 recorded the baselines (stock Hermes and Tameru 1.3.0); later phases compare against
them.

Everything is generated from a seed, no network is used, and the same (engine, seed) always produces
byte-identical metrics.

## Run it

Real Hermes is required (the harness drives the real `ContextCompressor` and its real seams):

```bash
HERMES_REPO_ROOT=/path/to/hermes-agent python -m pytest -q tests/eval        # tests
HERMES_REPO_ROOT=/path/to/hermes-agent python tests/eval/compare.py          # side-by-side table
HERMES_REPO_ROOT=/path/to/hermes-agent python tests/eval/compare.py --json
```

Use the Python version Hermes needs (3.14 at the pinned SHA `1298c8e74baa73e1a2b90124228d017261ac6bc4`).
Without `HERMES_REPO_ROOT` the Hermes-dependent tests are skipped; `test_harness_units.py` (fixtures,
scenario, metric machinery) still runs. A full run takes about 20 s, almost all of it Tameru 1.3.0's own
extraction (the metric `engine seconds`), which the determinism test pays twice.

Regenerate the saved baselines after an intended change (Hermes bump, scenario or metric change):

```bash
TAMERU_WRITE_BASELINES=1 HERMES_REPO_ROOT=... python -m pytest -q tests/eval
```

`baselines/stock.json` is compared **exactly** by `test_stock_matches_saved_baseline`. `baselines/tameru_1_3.json`
freezes the 1.3.0 plugin and is compared only while the vendored engine is still 1.3.x. The nightly job
against Hermes `main` is allowed to fail: a red `stock` comparison there means Hermes behaviour moved and the
baseline needs a conscious re-record.

## What is simulated

| File | Role |
|---|---|
| `../fixtures/hermes_payloads.py` | seeded generators for Hermes-shaped tool results |
| `scenario.py` | the scripted session: turns, tool calls, gold facts, final question |
| `replay.py` | the Hermes-loop mimic, the fake summarizer and all metrics |
| `engines.py` | engine factories (`stock`, `tameru_1_3`; `register()` adds more) |
| `compare.py` | prints a metrics table |
| `test_session_replay.py` | real-Hermes tests and baseline comparison |
| `test_harness_units.py` | Hermes-free unit tests of the harness |

**The session.** Checkout broke at 02:17; the user and agent trace it to a stock-reservation leak, patch it,
re-run tests, look at CI, logs, a CJK console dump, an oversized journal and a skill, then the user asks a
final question that can only be answered from facts planted in earlier tool output. 29 scripted turns + the
final question = 30 user turns, about 62 model requests. Tool results (about 380 KB of payload in total):

- `read_file` JSON with the `N|` gutter (service module read, **patched, re-read**; a test file read,
  patched, re-read; models, API and client modules), `search_files` path-grouped JSON;
- `terminal` JSON: a 43 KB application log with level-tagged ERROR/FATAL/CRITICAL lines (7 distinct error
  fingerprints, ~21 lines), pytest failure output and green runs, `git diff`, `git log --stat`, `rg -n`
  output, `cat -n` of a YAML config, a `gh api` list of 300 objects, a 24 KB CJK (Chinese/Japanese/Korean)
  log, and an oversized result replaced by Hermes' `<persisted-output>` spill block;
- a `skill_view` result;
- **identical repeated calls** (same `cat -n`, same `read_file` of the patched service) to exercise dedupe.

Shapes were checked against the pinned Hermes source (`tools/file_operations_common.py`,
`tools/terminal_tool.py`, `tools/tool_result_storage.py`); a test asserts the spill block equals Hermes'
own `_build_persisted_message` text.

**The loop** mimics `agent/turn_preflight.py`, before every model request. A turn with R tool rounds makes
R + 1 requests: the first is the turn-start preflight (`run_preflight_compression`: the compress gate only), the
other R follow tool results (`compress_after_tool_results`: the compress gate, else the proactive prune).
Hermes calls `prune_tool_results_only` in that second place and nowhere else, so the harness does too:

```
est = estimate_messages_tokens_rough(messages) + 3000      # 3000 = tool-schema overhead
if the engine awaits real usage after a compaction: nothing runs   # update_from_response clears it
elif engine.should_compress(est):
    messages = engine.compress(messages, current_tokens=est)
elif the request follows tool results:                      # last message is a tool row
    new, n = engine.prune_tool_results_only(messages, current_tokens=est)
    if n and new is not messages: messages = new           # Hermes' no-op contract
[select_context / request / update_from_response / on_turn_complete(usage) as Hermes does, when the engine overrides them]
```

The engine is configured like `agent_init` does: `update_model(model="test-model", context_length=64_000)`,
then `on_session_start`. That gives `threshold_tokens = 54,400`; stock's proactive prune fires from 48,000
estimated tokens. Exceptions from the engine are not swallowed (Hermes swallows prune errors; a harness
should not).

**The summary LLM.** `ContextCompressor._call_summary_llm` calls the module-level `call_llm` of
`agent.context_compressor` (`task="compression"`). The harness swaps that name for `FakeSummarizer` while a
session runs, so Hermes' own prompt building, redaction, anchor index, verbatim-user section and recovery
footer still run. The fake is lossy on purpose: each summary keeps the **first 200 characters of every turn**
(and the newest user ask as the task snapshot); on iterative updates the previous summary's entries are cut to
**120 characters**, and a summary never exceeds 9,000 characters. `test_fake_summarizer_patches_the_name_hermes_calls`
fails loudly if Hermes renames that seam (otherwise the fake would stop intercepting and calls would reach the
network).

## Metrics

All sizes are characters of the **wire form** of each message (`role`, `content`, `name`, `tool_calls`,
`tool_call_id`, serialized as sorted-key JSON; internal underscore keys and sidecars are dropped). They are
input-side only; output tokens are not priced. `metrics` and `series` in the result are deterministic; `timing`
is wall clock and is stored in the baselines for information only.

| Metric | Meaning |
|---|---|
| `requests` | model requests sent (one per tool round plus one final per user turn) |
| `input_chars_total` / `_max` / `_final` | sum / largest / last of the per-request input size |
| `cost_cache_priced` | simulated prefix-cache price in character-equivalents, below |
| `cost_uncached`, `cost_ratio_vs_uncached` | everything at 1.0x, and the ratio of the two |
| `cached_chars_total` | characters served from the simulated cache (sum of per-request common prefixes) |
| `compress_calls`, `compactions`, `compactions_shrinking` | calls to `engine.compress`; those that returned a different list (Hermes treats that as a compaction); and those whose list has fewer messages than the input. An engine that calls the summarizer and then returns its pruned input (1.3.0 does when it distrusts the summary) counts under `compactions` but not `compactions_shrinking` |
| `summary_llm_calls`, `summary_prompt_chars`, `summary_unparsed_prompts` | fake summarizer calls, the prompt volume Hermes sent to it, and prompts whose turn records the fake could not read (must be 0: it means Hermes changed its summary prompt format) |
| `prune_calls`, `prune_commits`, `prune_rows_demoted` | `prune_tool_results_only` calls; commits (new list, n > 0); tool rows demoted |
| `prefix_invalidated_chars`, `prefix_break_events` | previous request length minus common prefix, summed; and how many requests broke the prefix |
| `gold` | gold-fact retention overall and per Factory probe category (`recall`, `artifact`, `continuation`, `decision`) with the missing ids |
| `error_fingerprints` | how many of the large log's error fingerprints still have an exemplar line in the final request |
| `refetch` | `needed_facts`: facts the final question depends on; `missing_needed_facts` / `refetch_rate`: those absent from the final request; `refetch_opportunities`: distinct tool calls the agent would have to re-run to recover them |
| `pairing` | tool-call/result integrity violations over every request (unanswered call, orphan or duplicate result, a result that does not directly follow the assistant message that made the call, leading tool message) |
| `timing.engine_seconds` | wall time inside `should_compress` / `compress` / `prune_tool_results_only` |

**Cache price.** Each request is compared with the previous one as serialized JSON, message by message; the
longest common prefix (in characters) is *cached* and costs **0.10x**; everything after it is a *cache write*
and costs **1.25x**. The first request, which shares nothing, pays 1.25x on everything. There is no TTL and no
breakpoint model; the point is to make prefix-breaking rewrites (every prune or compaction) visible in the
price. `cost_cache_priced` is accumulated in integer centi-characters, so it is exact.

**Gold facts** are literal strings (17 in total: 6 recall, 4 artifact, 3 continuation, 4 decision) planted at a
known place: a user message, an assistant message, or deep inside a tool result. Retention is checked against
the text of the **final request** (message contents and tool-call arguments; tool JSON is searched both raw and
with its newline/quote escapes undone). 11 of them are `needed` by the final question. Where a fact lives
matters: facts in user messages survive Hermes' verbatim-user summary section; facts only in tool output survive
only if the engine keeps or can recover that output.

**Error fingerprints** follow the engine helper's contract (level-tagged ERROR/FATAL/CRITICAL/PANIC/FAIL lines;
lowercase, uuids, hex runs of 6+ and digit runs masked; capped at 160 characters) and are reimplemented in
`replay.py` so the harness does not depend on the engine under test. A test compares the two when the vendored
engine has `error_fingerprints`.

## Baselines (Hermes pin `1298c8e`, seed 0)

| | stock Hermes | Tameru 1.3.0 |
|---|---|---|
| requests | 62 | 62 |
| input chars (total) | 6,361,016 | 7,306,918 |
| cost, cache-priced (char-eq) | 1,273,007 | 2,215,464 |
| cost / uncached | 0.200 | 0.303 |
| compress calls / changed list / shrank | 1 / 1 / 1 | 6 / 4 / 0 |
| summary LLM calls | 1 | 6 |
| prune commits (rows demoted) | 2 (8) | 13 (20) |
| prefix invalidated (chars) | 469,751 | 1,080,761 |
| gold retained | 10/17 (recall 0/6, artifact 3/4, continuation 3/3, decision 4/4) | 12/17 (recall 2/6, artifact 3/4, continuation 3/3, decision 4/4) |
| error fingerprints retained | 0/7 | 5/7 |
| re-fetch opportunities | 3 calls (4 of 11 needed facts) | 3 calls (4 of 11) |
| pairing violations | 0 | 0 |
| engine seconds | ~0.1 | ~10 |

Reading them: 1.3.0 keeps more facts, but its prune commits a rewrite about every second tool round (13 against
stock's 2), so it breaks the prompt-cache prefix again and again and pays about **1.7x** stock's cache-priced
cost. Its compaction path is worse than stock's: from request 53 on the transcript stays over the threshold and
`compress()` fires on 6 of the last 10 requests. Each call spends a summarizer call and then returns the *pruned
input* because `query_facts_lost` distrusts the summary (G3): the list changes (4 times) but never shrinks.
Stock compacts once, from 198 KB to 42 KB. The session also costs 1.3.0 about 10 s of engine time (stock about
0.1 s).

## Adding an engine (Phase 2 and later)

```python
# engines.py, or a conftest
register("tameru_1_4", lambda: hermes_tameru_plugin.ExtractiveContextEngine(quiet_mode=True))
```

Then add the name to `BASELINE_ENGINES`, record `baselines/tameru_1_4.json` with `TAMERU_WRITE_BASELINES=1`,
and compare with `compare.py`. Phase 5's acceptance numbers come from this harness: cost after cache pricing,
compactions that shrink the transcript (`compactions_shrinking`) and re-fetch opportunities no worse than
`stock`; gold and error retention no worse than `stock` in every category; pairing violations 0.

## Caveats

- The summarizer is a fake. Absolute retention numbers say how much each *engine* preserves around a lossy
  summary, not how a real model would summarize.
- Token pressure uses Hermes' rough character-based estimator plus a fixed overhead, not provider usage.
- The scenario is one story at one seed. Seeds change the random content (log lines, ids, source filler) but
  not the script; pass `seed=` to `run_session` to check that a result is not an artifact of seed 0.
