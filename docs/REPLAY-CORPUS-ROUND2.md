# Round-2 session corpus in the Hermes replay (P2c, replay only)

**Every figure here is replay-only.** Costs come from a deterministic offline replay of Hermes' conversation loop, priced after the
fact under dated price profiles; no live probe has been billed, so nothing below is a dollar claim. "Facts kept" means the fact's line
is in the final prompt of the replay; no model answered any question (no S3 probe ran).

## What was run

- **Corpus.** The engine's host-neutral session corpus (engine `release/1.10.0` at 184a46f, generator `session-corpus-3`), **dev
  (12 families, 132 scenarios) and validation (8 families, 88 scenarios) only**, materialised locally with
  `python -m benchmarks.session_corpus.build --split dev|validation --out <plugin>/build/session_corpus` (ignored, about 1.4 GB, not
  committed). The sealed holdout was not built, read or run; the loader refuses a `holdout` path, any holdout family and any split
  other than dev/validation. No Hermes holdout or blind shape was touched.
- **Adapter** (`tests/eval/corpus_adapter.py`, the engine's ADAPTERS.md mapping for Hermes): window slice `steps[0 : windows[W].steps]`;
  turn 1 is the task, later turns `continue` / `继续`; rounds of one tool call, turns of 6 steps; Hermes envelopes (`terminal` JSON,
  `read_file` with the `N|` gutter; `gh_run_view`, `kubectl_logs`, `sql_query` and `ansible_playbook` run as terminal commands);
  `call_key(tool, args, nth)`; the closing question is the answerable question set. Fact check: the full fact line verbatim (raw or
  JSON-escaped), else one prompt line (whitespace collapsed, envelope `\n` escapes counted as line breaks) holding key and value (real
  facts: the value line); value-only presence is a secondary column. Aggregate questions need every fact; absent questions are not
  scored here. Tests: `tests/eval/test_corpus_adapter.py` (exact counts, agreement with the corpus gold including every 7th local dev
  scenario at 128K and 1M, determinism, holdout refusal, the subsample rule, the scoring grep equals the recovery child). All 880
  dev/validation scenario-window pairs map.
- **Runner** `tests/eval/corpus_run.py` (one row per arm, scenario and window, with the P1c pricing ledger) and report
  `tests/eval/corpus_report.py`; summary numbers in [`docs/results/replay-corpus-round2.json`](results/replay-corpus-round2.json).
  The 2,640 rows (37 MB; deterministic apart from timing) are kept locally in `build/replay-corpus-round2/`, not committed.
- **Arms**, no tuning and no new setting: `stock_defaults` (Hermes as shipped), `engine_1_9_0` (this plugin at 836c5d3, run from a
  scratch worktree with this commit's `tests/eval` harness), `engine_1_10_0` (this tree). Hermes 3b42157858; tool block 39,993 chars.
- **Windows**: 128K, 252K, 500K and 1M on the corpus (all 220 scenarios × 3 arms at every window). The corpus has no 64K slice; the
  64K cache-break check uses the development matrix's `refactor` and `smallrows` shapes (seeds 0-5): the committed P1c rows, whose
  stock and 1.10.0 rows were re-run here and reproduced exactly (24 of 24).
- **Prices**: `repricing.py` under the five registered Hermes profiles (provider prefix rules, no expiry; weights 1/5 each) and the
  replay's own price. TTL sensitivities were not run on this corpus. Every scenario is one cluster (one seed each), so intervals are
  cluster bootstraps by scenario: 10,000 resamples for `effective_real` (2,000 for the other two prices), seed 0.

## Hermes' trigger at 500K and 1M

`update_model(context_length)` works at every window with the local checkout: the replay passes the window explicitly, so no model
metadata is needed. The trigger is **75% below 512K and Hermes' base 50% from 512K up** (`_SMALL_CTX_WINDOW_LIMIT = 512_000` in
`agent/context_compressor.py`): 96,000 tokens at 128K, 189,000 at 252K, 375,000 at 500K and **500,000 at 1M** (not 750,000, the
corpus's planning value). Nothing broke.

## Run time (16-core development PC; 15 workers: 9 for stock + 1.10.0, 6 for 1.9.0)

Elapsed for all 660 runs of a window: **128K 324 s, 252K 425 s, 500K 703 s, 1M 1,494 s (25 min)**, all under the 60-minute rule, so
the full set ran at every window and the deterministic stratified subsample (`corpus_adapter.stratified_subsample`) was **not used**.
Mean per run on the loaded machine: 1.10.0 7.5 s at 128K, 10.4 s at 252K, 17.1 s at 500K, 38.5 s at 1M (stock 3.6 / 4.0 / 5.2 /
10.9 s; 1.9.0 the same as 1.10.0 within 1.2 s).

## Findings

1. **Trigger crossing (stock arm, sessions with ≥1 / ≥2 compactions): 100% / 100% at 128K, 100% / 90% at 252K, 100% / 95% at 500K,
   100% / 100% at 1M**, alike in dev and validation. Every window clears the plan's 70% / 30% bar, so **252K now crosses enough to be
   gated** with this corpus (Hermes 500K would too; the plan keeps it report-only).
2. **Arm vs itself is exactly zero** in all 216 checks (3 arms × 4 windows × 6 price profiles × 3 prices), and every row's ledger
   reproduces that row's prices at the replay's own price.
3. **1.10.0 vs 1.9.0: no cost difference that matters.** Weighted `effective_real` −0.2% [−0.3, −0.1] at 128K, −0.0% [−0.2, +0.1] at
   252K, −0.1% at 500K and at 1M; no profile moves it by more than 0.2 points. Facts kept +1.0 to +1.3 points per scenario (one-sided
   0.0125 lower bound ≥ 0.0 at every window), mostly error lines (+3 to +5 points) and the `jest` and `trace-node` families. Four to
   six of the 20 families are dearer than with 1.9.0, by at most +0.5% (`gitlog` at 252K).
4. **Both engines are cheaper than stock at every corpus window** under every registered profile (weighted `effective_real` −11.7% at
   128K, −30.4% at 252K, −28.0% at 500K, −22.9% at 1M; per profile from −8.8% (DeepSeek, 128K) to −72% (Haiku at 252K, where stock's
   prompts above 100K tokens hit the ×5 tier)). **But cache breaks are higher than stock**: 6.5 vs 2.1 per session at 128K, 5.0 vs
   1.9 at 252K, 4.5 vs 2.0 at 500K, 7.6 vs 3.1 at 1M (prunes replace compactions; both engines compact 0 times from 252K up), and at
   64K `refactor` 6.67 vs 2.00, with +6.7% [+3.6, +11.3] weighted `effective_real` over the two 64K shapes. **The registered gate
   "cache breaks and compactions ≤ stock for every shape and window" fails for both engines at every window** (compactions alone pass).
5. **`effective_finder` turns against both engines as the window grows**: vs stock +4.9% at 128K, +12.4% at 252K, **+69.5% at 500K,
   +138% at 1M**. The weak finder tries every ref whose header names the tool, and almost every ref here is `terminal` (36 and 25 tries
   for the two recovered facts of one 252K row), each priced as a re-read of the whole prompt. With a finder that knows the ref
   (`effective_real`) the plugin wins; with one that does not, recovery dominates the cost at 500K and 1M.
6. **Facts are low in every arm, and shortened rows rarely keep them.** Facts kept: stock 8.6-16.8%, 1.9.0 34.4-36.2%, 1.10.0
   35.8-37.3% (kept + recoverable 91-98% for both engines). **S5 8(a)-style retention** (a fact whose row is still in the final prompt
   but shortened, and whose line that shortened row still holds) is **22-24% for both engines at every window** (vague 20-22%,
   specific 25-27%; en, zh and mixed alike), far below the plan's 85% bar: natural facts 11-12%, buried S5 singletons 10-17%, real
   Squeez lines 8-12%, error lines 62-69%. This is line presence in a shortened row, the closest proxy the replay exposes, not the
   engine's own accepted-extract record that the gate names.
7. **Worst families** (1.10.0): lowest facts kept `metrics` (6.8% at 128K, 9.1% at 252K, 15.2% at 500K) and `pglog` (11.9% at 1M);
   smallest saving vs stock `pytest` at 128K (−8.2%) and `jest` from 252K up (−17.6% to −28.4%). Validation keeps fewer facts than
   dev (30-32% vs 39-41% for 1.10.0); the cost changes are the same in both splits, every language and both task styles.

## Bug found and fixed (harness only)

The first pass scored read-backs through the plugin's recovery grep, which runs each regex in a child process under a wall-clock
timeout. On the loaded machine some of the weak finder's greps timed out, so `effective_finder` and `finder_found` depended on machine
load (57 of 660 rows at 128K differed between two passes; no other field did). Scoring now runs the child's exact code in-process
(`re.compile(p, re.IGNORECASE)`, after the session, never during it) and answers identical read-back calls once; a test checks that it
equals the child. All numbers here come from the second pass. The product's recovery tool is unchanged.

## Not verified

- No model answered anything: facts are line presence in the final prompt, and stock is scored with the replay's crude stand-in
  summarizer, which favours the plugin. No S3 quality probe ran.
- No TTL or idle sensitivity and no live cache probe; DeepSeek's 64-token prefix unit is assumed; characters stand in for tokens at
  4 per token.
- 8(a) is a proxy (finding 6). The engine's accept/decline records, decline reasons and calibration strata are not in these rows.
- The 64K check uses the old development-matrix shapes, not corpus sessions.

## What P3 must fix first (facts only; nothing was tuned here)

The largest measured gaps, all the same for 1.9.0 and 1.10.0: (a) shortened rows drop the fact line (8(a)-style retention 22-24%;
natural, buried and real facts 8-17%, specific and vague, en and zh); (b) cache breaks above stock at every window and at 64K, which
fails a registered gate; (c) recovery with a finder that does not know the ref costs more than stock at 500K and 1M.

## Tables

Generated by `python tests/eval/corpus_report.py build/replay-corpus-round2/rows-*.jsonl --matrix64
tests/eval/results/repriced-1.10.0-rows.json --elapsed build/replay-corpus-round2/elapsed.txt --json docs/results/replay-corpus-round2.json`.

### Trigger crossing, stock arm (sessions with ≥1 / ≥2 compactions)

| Split | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| dev | 100% / 99% (n 132) | 100% / 90% (n 132) | 100% / 95% (n 132) | 100% / 100% (n 132) |
| validation | 100% / 100% (n 88) | 100% / 90% (n 88) | 100% / 93% (n 88) | 100% / 100% (n 88) |
| all | 100% / 100% (n 220) | 100% / 90% (n 220) | 100% / 95% (n 220) | 100% / 100% (n 220) |

### Wall time per run (seconds: mean / median / p95 / max)

| Arm | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| stock_defaults | 3.6 / 1.3 / 15.2 / 20.2 | 4.0 / 2.0 / 15.6 / 21.4 | 5.2 / 3.7 / 15.1 / 20.4 | 10.9 / 9.0 / 24.6 / 31.5 |
| engine_1_9_0 | 7.7 / 6.1 / 19.7 / 29.1 | 10.4 / 9.0 / 22.4 / 28.4 | 16.9 / 16.2 / 27.8 / 34.7 | 37.3 / 35.6 / 58.9 / 70.8 |
| engine_1_10_0 | 7.5 / 5.8 / 20.8 / 30.8 | 10.4 / 9.1 / 23.8 / 33.5 | 17.1 / 15.6 / 29.7 / 36.0 | 38.5 / 37.5 / 56.4 / 74.6 |
| elapsed per window, all 660 runs (15 workers) | 324 s | 425 s | 703 s | 1494 s |

### Primary: registered weighted `effective_real` change % (Hermes 1/5 each, no expiry), cluster 95% interval by scenario

| Comparison | Field | 128K | 252K | 500K | 1M |
|---|---|---|---|---|---|
| engine_1_10_0 vs engine_1_9_0 | effective_real | -0.2 [-0.3, -0.1] (-0.2) | -0.0 [-0.2, +0.1] (-0.1) | -0.1 [-0.1, -0.0] (-0.1) | -0.1 [-0.1, -0.0] (-0.1) |
| engine_1_10_0 vs engine_1_9_0 | effective | -0.2 [-0.3, -0.1] (-0.2) | -0.0 [-0.1, +0.1] (-0.0) | -0.0 [-0.1, +0.0] (-0.0) | -0.0 [-0.1, +0.0] (-0.0) |
| engine_1_10_0 vs engine_1_9_0 | effective_finder | -0.4 [-0.8, -0.0] (-0.5) | -0.5 [-1.2, +0.1] (-0.7) | -1.0 [-1.8, -0.3] (-1.1) | -1.2 [-1.8, -0.6] (-1.3) |
| engine_1_10_0 vs stock_defaults | effective_real | -11.7 [-12.4, -11.1] (-11.9) | -30.4 [-30.8, -29.9] (-30.5) | -28.0 [-28.4, -27.7] (-28.1) | -22.9 [-23.3, -22.4] (-23.0) |
| engine_1_10_0 vs stock_defaults | effective | -10.9 [-11.6, -10.3] (-11.1) | -29.4 [-29.9, -29.0] (-29.6) | -26.8 [-27.1, -26.4] (-26.9) | -21.8 [-22.3, -21.4] (-22.0) |
| engine_1_10_0 vs stock_defaults | effective_finder | +4.9 [+3.5, +6.3] (+4.7) | +12.4 [+10.2, +14.7] (+12.1) | +69.5 [+65.1, +73.9] (+69.5) | +138.3 [+131.9, +145.1] (+138.2) |
| engine_1_9_0 vs stock_defaults | effective_real | -11.6 [-12.2, -10.9] (-11.7) | -30.3 [-30.7, -29.9] (-30.5) | -28.0 [-28.4, -27.6] (-28.1) | -22.8 [-23.3, -22.4] (-23.0) |
| engine_1_9_0 vs stock_defaults | effective | -10.8 [-11.4, -10.1] (-10.9) | -29.4 [-29.8, -29.0] (-29.6) | -26.8 [-27.1, -26.4] (-26.9) | -21.8 [-22.2, -21.3] (-21.9) |
| engine_1_9_0 vs stock_defaults | effective_finder | +5.4 [+3.9, +6.9] (+5.2) | +13.2 [+10.9, +15.6] (+12.8) | +71.5 [+67.1, +75.8] (+71.4) | +141.5 [+134.9, +148.4] (+141.3) |

### `effective_real`, engine_1_10_0 vs engine_1_9_0, per profile (mean change %, 95% interval; pooled D)

| Profile | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| original | -0.1 [-0.2, -0.1] (-0.1) | -0.1 [-0.1, +0.0] (-0.1) | -0.1 [-0.1, -0.0] (-0.1) | -0.1 [-0.1, -0.0] (-0.1) |
| anthropic-opus-5.5-5m | -0.2 [-0.3, -0.1] (-0.2) | -0.1 [-0.2, +0.1] (-0.1) | -0.0 [-0.1, +0.0] (-0.0) | -0.1 [-0.1, -0.0] (-0.1) |
| anthropic-sonnet-5.5-5m | -0.2 [-0.3, -0.1] (-0.2) | -0.1 [-0.2, +0.1] (-0.1) | -0.0 [-0.1, +0.0] (-0.0) | -0.1 [-0.1, -0.0] (-0.1) |
| anthropic-haiku-5.5-5m | -0.2 [-0.3, -0.1] (-0.2) | +0.0 [-0.4, +0.4] (-0.0) | -0.1 [-0.2, -0.0] (-0.1) | -0.1 [-0.2, -0.1] (-0.1) |
| openai-generic-30m | -0.2 [-0.3, -0.1] (-0.1) | -0.1 [-0.1, +0.0] (-0.1) | -0.1 [-0.1, -0.0] (-0.1) | -0.1 [-0.1, +0.1] (-0.1) |
| deepseek-v4-pro | -0.1 [-0.2, -0.1] (-0.1) | -0.0 [-0.1, +0.0] (-0.0) | -0.1 [-0.1, -0.0] (-0.1) | -0.1 [-0.1, -0.0] (-0.1) |

### `effective_real`, engine_1_10_0 vs stock_defaults, per profile (mean change %, 95% interval; pooled D)

| Profile | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| original | -12.6 [-13.2, -12.0] (-12.7) | -23.3 [-23.7, -22.9] (-23.4) | -25.7 [-26.1, -25.4] (-25.8) | -23.6 [-23.9, -23.3] (-23.7) |
| anthropic-opus-5.5-5m | -10.7 [-11.4, -10.0] (-10.9) | -19.1 [-19.6, -18.6] (-19.3) | -20.2 [-20.6, -19.8] (-20.3) | -13.6 [-14.1, -13.1] (-13.7) |
| anthropic-sonnet-5.5-5m | -10.7 [-11.4, -10.0] (-10.9) | -19.1 [-19.6, -18.6] (-19.3) | -20.2 [-20.6, -19.8] (-20.3) | -13.6 [-14.1, -13.1] (-13.7) |
| anthropic-haiku-5.5-5m | -14.5 [-15.1, -13.8] (-14.6) | -71.7 [-72.2, -71.2] (-72.0) | -34.5 [-35.0, -34.0] (-34.6) | -22.0 [-22.6, -21.4] (-22.2) |
| openai-generic-30m | -13.9 [-14.5, -13.3] (-14.0) | -24.1 [-24.6, -23.7] (-24.3) | -45.5 [-45.9, -45.1] (-45.6) | -47.6 [-47.9, -47.2] (-47.7) |
| deepseek-v4-pro | -8.8 [-9.5, -8.2] (-9.0) | -17.7 [-18.1, -17.3] (-17.9) | -19.8 [-20.1, -19.5] (-19.9) | -17.6 [-17.9, -17.3] (-17.7) |

### `effective_real`, engine_1_9_0 vs stock_defaults, per profile (mean change %, 95% interval; pooled D)

| Profile | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| original | -12.4 [-13.1, -11.9] (-12.6) | -23.2 [-23.6, -22.8] (-23.4) | -25.7 [-26.1, -25.3] (-25.8) | -23.6 [-23.9, -23.2] (-23.7) |
| anthropic-opus-5.5-5m | -10.5 [-11.2, -9.8] (-10.7) | -19.1 [-19.6, -18.6] (-19.3) | -20.2 [-20.6, -19.8] (-20.3) | -13.5 [-14.0, -13.0] (-13.7) |
| anthropic-sonnet-5.5-5m | -10.5 [-11.2, -9.8] (-10.7) | -19.1 [-19.6, -18.6] (-19.3) | -20.2 [-20.6, -19.8] (-20.3) | -13.5 [-14.0, -13.0] (-13.7) |
| anthropic-haiku-5.5-5m | -14.3 [-14.9, -13.6] (-14.4) | -71.7 [-72.2, -71.2] (-72.0) | -34.4 [-34.9, -33.9] (-34.6) | -21.9 [-22.6, -21.3] (-22.1) |
| openai-generic-30m | -13.8 [-14.4, -13.2] (-13.9) | -24.1 [-24.5, -23.7] (-24.2) | -45.4 [-45.8, -45.0] (-45.6) | -47.5 [-47.9, -47.2] (-47.7) |
| deepseek-v4-pro | -8.7 [-9.4, -8.0] (-8.9) | -17.7 [-18.1, -17.3] (-17.8) | -19.8 [-20.1, -19.5] (-19.9) | -17.6 [-17.9, -17.3] (-17.7) |

### Cost split per answered turn (K chars in miss units): read / write / summary input / output

| Profile | Window | stock | 1.9.0 | 1.10.0 |
|---|---|---|---|---|
| original | 128K | 104 / 168 / 4 / 0.0 | 73 / 183 / 0 / 0.0 | 73 / 183 / 0 / 0.0 |
| original | 252K | 220 / 180 / 2 / 0.0 | 139 / 188 / 0 / 0.0 | 139 / 188 / 0 / 0.0 |
| original | 500K | 456 / 188 / 2 / 0.0 | 302 / 196 / 0 / 0.0 | 302 / 196 / 0 / 0.0 |
| original | 1M | 656 / 195 / 2 / 0.0 | 454 / 213 / 0 / 0.0 | 454 / 212 / 0 / 0.0 |
| anthropic-opus-5.5-5m | 128K | 52 / 176 / 4 / 4.9 | 36 / 189 / 0 / 0.2 | 36 / 188 / 0 / 0.2 |
| anthropic-opus-5.5-5m | 252K | 110 / 185 / 2 / 5.0 | 70 / 192 / 0 / 0.0 | 70 / 192 / 0 / 0.0 |
| anthropic-opus-5.5-5m | 500K | 228 / 190 / 2 / 3.3 | 151 / 203 / 0 / 0.0 | 151 / 203 / 0 / 0.0 |
| anthropic-opus-5.5-5m | 1M | 328 / 196 / 2 / 3.1 | 226 / 245 / 0 / 0.0 | 226 / 245 / 0 / 0.0 |
| anthropic-sonnet-5.5-5m | 128K | 52 / 176 / 4 / 4.9 | 36 / 189 / 0 / 0.2 | 36 / 188 / 0 / 0.2 |
| anthropic-sonnet-5.5-5m | 252K | 110 / 185 / 2 / 5.0 | 70 / 192 / 0 / 0.0 | 70 / 192 / 0 / 0.0 |
| anthropic-sonnet-5.5-5m | 500K | 228 / 190 / 2 / 3.3 | 151 / 203 / 0 / 0.0 | 151 / 203 / 0 / 0.0 |
| anthropic-sonnet-5.5-5m | 1M | 328 / 196 / 2 / 3.1 | 226 / 245 / 0 / 0.0 | 226 / 245 / 0 / 0.0 |
| anthropic-haiku-5.5-5m | 128K | 104 / 176 / 4 / 4.9 | 73 / 189 / 0 / 0.2 | 73 / 188 / 0 / 0.2 |
| anthropic-haiku-5.5-5m | 252K | 835 / 503 / 2 / 5.0 (tier 33) | 173 / 213 / 0 / 0.0 (tier 3) | 173 / 213 / 0 / 0.0 (tier 3) |
| anthropic-haiku-5.5-5m | 500K | 2137 / 724 / 2 / 3.3 (tier 111) | 1286 / 666 / 0 / 0.0 (tier 97) | 1285 / 666 / 0 / 0.0 (tier 97) |
| anthropic-haiku-5.5-5m | 1M | 3180 / 819 / 2 / 3.1 (tier 254) | 2183 / 1019 / 0 / 0.0 (tier 265) | 2181 / 1019 / 0 / 0.0 (tier 265) |
| openai-generic-30m | 128K | 104 / 168 / 4 / 4.9 | 73 / 183 / 0 / 0.2 | 73 / 183 / 0 / 0.2 |
| openai-generic-30m | 252K | 220 / 180 / 2 / 5.0 | 139 / 188 / 0 / 0.0 | 139 / 188 / 0 / 0.0 |
| openai-generic-30m | 500K | 660 / 233 / 2 / 3.3 (tier 38) | 302 / 196 / 0 / 0.0 | 302 / 196 / 0 / 0.0 |
| openai-generic-30m | 1M | 1105 / 279 / 2 / 3.1 (tier 137) | 508 / 225 / 0 / 0.0 (tier 23) | 508 / 225 / 0 / 0.0 (tier 23) |
| deepseek-v4-pro | 128K | 35 / 134 / 4 / 2.9 | 24 / 147 / 0 / 0.1 | 24 / 147 / 0 / 0.1 |
| deepseek-v4-pro | 252K | 73 / 144 / 2 / 3.0 | 46 / 151 / 0 / 0.0 | 46 / 151 / 0 / 0.0 |
| deepseek-v4-pro | 500K | 152 / 150 / 2 / 2.0 | 101 / 157 / 0 / 0.0 | 101 / 157 / 0 / 0.0 |
| deepseek-v4-pro | 1M | 219 / 156 / 2 / 1.9 | 151 / 170 / 0 / 0.0 | 151 / 170 / 0 / 0.0 |

### Scorecard per session

| Window | Arm | steps | compactions | cache breaks | prune commits | shortened rows | failed turns | facts kept % | kept+recoverable % | value present % |
|---|---|---|---|---|---|---|---|---|---|---|
| 128K | stock_defaults | 31 | 2.11 | 2.11 | 0.00 | 3.9 | 0.00 | 16.5 | 16.5 | 24.3 |
| 128K | engine_1_9_0 | 31 | 0.24 | 6.46 | 6.22 | 24.3 | 0.00 | 35.5 | 91.4 | 41.1 |
| 128K | engine_1_10_0 | 31 | 0.24 | 6.45 | 6.21 | 24.4 | 0.00 | 36.5 | 91.8 | 41.8 |
| 252K | stock_defaults | 61 | 1.93 | 1.93 | 0.00 | 8.4 | 0.00 | 16.8 | 16.8 | 25.9 |
| 252K | engine_1_9_0 | 61 | 0.00 | 4.98 | 4.98 | 48.1 | 0.00 | 36.2 | 97.3 | 42.8 |
| 252K | engine_1_10_0 | 61 | 0.00 | 4.97 | 4.97 | 48.1 | 0.00 | 37.0 | 97.4 | 43.0 |
| 500K | stock_defaults | 128 | 1.98 | 1.98 | 0.00 | 30.6 | 0.00 | 10.6 | 10.6 | 23.4 |
| 500K | engine_1_9_0 | 128 | 0.00 | 4.46 | 4.46 | 101.4 | 0.00 | 36.0 | 97.4 | 44.5 |
| 500K | engine_1_10_0 | 128 | 0.00 | 4.46 | 4.46 | 101.3 | 0.00 | 37.3 | 97.4 | 45.3 |
| 1M | stock_defaults | 258 | 3.13 | 3.13 | 0.00 | 25.0 | 0.00 | 8.6 | 8.6 | 22.9 |
| 1M | engine_1_9_0 | 258 | 0.00 | 7.61 | 7.61 | 212.0 | 0.00 | 34.4 | 98.0 | 43.9 |
| 1M | engine_1_10_0 | 258 | 0.00 | 7.61 | 7.61 | 212.0 | 0.00 | 35.8 | 98.0 | 44.6 |

### Facts kept, change in points per scenario (95% interval; one-sided 0.0125 lower bound)

| Comparison | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| engine_1_10_0 vs engine_1_9_0 | +1.0 [+0.2, +1.9]; lb +0.1 | +0.8 [+0.1, +1.5]; lb +0.0 | +1.3 [+0.7, +2.1]; lb +0.6 | +1.3 [+0.7, +2.0]; lb +0.7 |
| engine_1_10_0 vs stock_defaults | +20.0 [+16.6, +23.4]; lb +16.1 | +20.2 [+17.4, +23.2]; lb +17.0 | +26.7 [+24.5, +29.0]; lb +24.2 | +27.2 [+25.1, +29.2]; lb +24.8 |
| engine_1_9_0 vs stock_defaults | +19.0 [+15.6, +22.3]; lb +15.0 | +19.4 [+16.6, +22.4]; lb +16.1 | +25.4 [+23.2, +27.6]; lb +22.9 | +25.8 [+23.8, +27.8]; lb +23.5 |

### Facts kept % by language (stock / 1.9.0 / 1.10.0)

| language | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| en | 18 / 38 / 40 (n 436) | 17 / 37 / 38 (n 872) | 11 / 38 / 39 (n 1308) | 9 / 34 / 36 (n 1744) |
| zh | 17 / 35 / 35 (n 276) | 17 / 36 / 35 (n 552) | 12 / 35 / 35 (n 828) | 9 / 35 / 35 (n 1104) |
| mixed | 12 / 30 / 30 (n 168) | 16 / 35 / 36 (n 336) | 8 / 33 / 36 (n 504) | 8 / 35 / 36 (n 672) |

### Facts kept % by task style (stock / 1.9.0 / 1.10.0)

| task style | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| specific | 17 / 37 / 38 (n 448) | 18 / 39 / 40 (n 896) | 11 / 39 / 40 (n 1344) | 8 / 36 / 38 (n 1792) |
| vague | 16 / 34 / 35 (n 432) | 16 / 34 / 34 (n 864) | 10 / 33 / 34 (n 1296) | 9 / 32 / 34 (n 1728) |

### Facts kept % by fact kind (stock / 1.9.0 / 1.10.0)

| fact kind | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| natural | 20 / 28 / 29 (n 242) | 20 / 30 / 31 (n 484) | 12 / 27 / 28 (n 726) | 9 / 24 / 26 (n 968) |
| error | 24 / 73 / 76 (n 198) | 21 / 69 / 73 (n 396) | 13 / 70 / 74 (n 594) | 9 / 63 / 68 (n 792) |
| buried | 22 / 27 / 28 (n 220) | 20 / 31 / 30 (n 440) | 10 / 30 / 30 (n 660) | 8 / 27 / 27 (n 880) |
| real | 0 / 18 / 18 (n 220) | 7 / 18 / 19 (n 440) | 7 / 21 / 21 (n 660) | 9 / 27 / 27 (n 880) |

### Facts kept % by position (stock / 1.9.0 / 1.10.0)

| position | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| early | 1 / 30 / 31 (n 440) | 2 / 40 / 40 (n 661) | 3 / 38 / 40 (n 1361) | 3 / 37 / 38 (n 2227) |
| middle | 3 / 33 / 34 (n 238) | 8 / 25 / 26 (n 652) | 3 / 27 / 28 (n 846) | 4 / 27 / 28 (n 866) |
| late | 66 / 50 / 50 (n 202) | 51 / 47 / 48 (n 447) | 49 / 48 / 49 (n 433) | 48 / 37 / 39 (n 427) |

### Facts kept % by split (stock / 1.9.0 / 1.10.0)

| split | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| dev | 19 / 39 / 40 (n 528) | 17 / 39 / 40 (n 1056) | 10 / 39 / 41 (n 1584) | 9 / 38 / 39 (n 2112) |
| validation | 13 / 30 / 31 (n 352) | 17 / 32 / 32 (n 704) | 11 / 31 / 32 (n 1056) | 8 / 29 / 30 (n 1408) |

### Questions held % by stratum (stock / 1.9.0 / 1.10.0)

| Stratum | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| single | 22 / 48 / 50 | 20 / 48 / 50 | 13 / 46 / 49 | 9 / 42 / 44 |
| buried | 22 / 30 / 31 | 19 / 34 / 34 | 9 / 32 / 32 | 7 / 28 / 27 |
| superseded | 22 / 25 / 25 | 20 / 28 / 26 | 12 / 29 / 29 | 8 / 26 / 26 |
| aggregate | 8 / 21 / 23 | 11 / 22 / 23 | 5 / 20 / 22 | 3 / 16 / 18 |
| real | 0 / 18 / 18 | 7 / 18 / 19 | 7 / 21 / 21 | 9 / 27 / 27 |

### Weighted pooled `effective_real` change % by stratum (1.10.0 vs 1.9.0 / 1.10.0 vs stock)

| Stratum | 128K | 252K | 500K | 1M |
|---|---|---|---|---|
| lang=en | -0.2 / -11.8 | -0.1 / -30.8 | -0.1 / -28.4 | -0.1 / -23.6 |
| lang=zh | -0.2 / -11.5 | +0.1 / -29.8 | +0.0 / -27.2 | -0.1 / -21.9 |
| lang=mixed | -0.1 / -12.8 | -0.2 / -31.1 | -0.2 / -28.9 | -0.1 / -23.4 |
| style=specific | -0.1 / -11.8 | -0.1 / -30.1 | -0.0 / -28.1 | -0.0 / -22.9 |
| style=vague | -0.2 / -12.0 | +0.0 / -30.9 | -0.1 / -28.2 | -0.1 / -23.1 |
| split=dev | -0.2 / -11.3 | -0.1 / -30.4 | -0.1 / -28.1 | -0.1 / -22.8 |
| split=validation | -0.2 / -12.7 | +0.1 / -30.8 | -0.1 / -28.2 | -0.0 / -23.4 |

### S5 8(a)-style retention: facts whose row is still in the final prompt but shortened, % whose line that row holds (n)

| Arm | Stratum | 128K | 252K | 500K | 1M |
|---|---|---|---|---|---|
| engine_1_9_0 | all | 22 (686) | 22 (1454) | 23 (2224) | 22 (3030) |
| engine_1_9_0 | specific | 24 (350) | 23 (732) | 26 (1129) | 24 (1552) |
| engine_1_9_0 | vague | 21 (336) | 20 (722) | 20 (1095) | 19 (1478) |
| engine_1_9_0 | en | 24 (337) | 21 (713) | 23 (1080) | 21 (1490) |
| engine_1_9_0 | zh | 23 (205) | 22 (459) | 24 (709) | 22 (960) |
| engine_1_9_0 | mixed | 19 (144) | 22 (282) | 21 (435) | 22 (580) |
| engine_1_9_0 | kind:natural | 11 (194) | 10 (383) | 11 (601) | 10 (821) |
| engine_1_9_0 | kind:error | 65 (151) | 62 (322) | 62 (477) | 56 (664) |
| engine_1_9_0 | kind:buried | 10 (172) | 13 (350) | 17 (555) | 15 (759) |
| engine_1_9_0 | kind:real | 10 (169) | 8 (399) | 9 (591) | 12 (786) |
| engine_1_10_0 | all | 24 (690) | 23 (1456) | 24 (2222) | 23 (3029) |
| engine_1_10_0 | specific | 25 (349) | 25 (731) | 27 (1129) | 26 (1552) |
| engine_1_10_0 | vague | 22 (341) | 21 (725) | 22 (1093) | 20 (1477) |
| engine_1_10_0 | en | 26 (336) | 23 (712) | 25 (1080) | 24 (1490) |
| engine_1_10_0 | zh | 23 (210) | 22 (461) | 24 (710) | 22 (959) |
| engine_1_10_0 | mixed | 19 (144) | 23 (283) | 24 (432) | 23 (580) |
| engine_1_10_0 | kind:natural | 12 (194) | 11 (384) | 12 (600) | 11 (820) |
| engine_1_10_0 | kind:error | 69 (151) | 67 (323) | 67 (476) | 62 (665) |
| engine_1_10_0 | kind:buried | 10 (173) | 12 (350) | 17 (555) | 15 (758) |
| engine_1_10_0 | kind:real | 10 (172) | 8 (399) | 10 (591) | 12 (786) |
| stock_defaults | all | 0 (81) | 0 (220) | 0 (480) | 0 (162) |
| stock_defaults | specific | 0 (30) | 0 (112) | 0 (270) | 0 (90) |
| stock_defaults | vague | 0 (51) | 0 (108) | 0 (210) | 0 (72) |
| stock_defaults | en | 0 (42) | 0 (123) | 0 (250) | 0 (90) |
| stock_defaults | zh | 0 (25) | 0 (62) | 0 (126) | 0 (38) |
| stock_defaults | mixed | 0 (14) | 0 (35) | 0 (104) | 0 (34) |
| stock_defaults | kind:natural | 0 (30) | 0 (67) | 0 (147) | 0 (48) |
| stock_defaults | kind:error | 0 (20) | 0 (55) | 0 (111) | 0 (45) |
| stock_defaults | kind:buried | 0 (31) | 0 (66) | 0 (134) | 0 (43) |
| stock_defaults | kind:real | - | 0 (32) | 0 (88) | 0 (26) |

### Per family at 128K: weighted pooled `effective_real` change % and facts kept %

| Family | 1.10.0 vs 1.9.0 | 1.10.0 vs stock | 1.9.0 vs stock | kept stock / 1.9.0 / 1.10.0 |
|---|---|---|---|---|
| ansible | -0.7 | -13.0 | -12.4 | 18 / 39 / 39 |
| bazel | -0.0 | -15.0 | -14.9 | 9 / 41 / 41 |
| cargo | +0.1 | -9.3 | -9.4 | 20 / 43 / 46 |
| cmake | +0.0 | -12.3 | -12.3 | 14 / 39 / 39 |
| gcc | -0.5 | -14.0 | -13.6 | 11 / 34 / 34 |
| ghactions | -0.0 | -10.0 | -10.0 | 30 / 48 / 48 |
| gitlog | +0.1 | -10.8 | -11.0 | 7 / 18 / 18 |
| gotest | +0.1 | -13.0 | -13.1 | 11 / 48 / 46 |
| grep | +0.0 | -11.3 | -11.3 | 16 / 25 / 25 |
| jest | -1.7 | -11.0 | -9.5 | 18 / 39 / 50 |
| kubectl | -0.0 | -9.9 | -9.9 | 16 / 43 / 43 |
| maven | -0.0 | -13.8 | -13.8 | 16 / 43 / 43 |
| metrics | +0.0 | -11.3 | -11.3 | 20 / 7 / 7 |
| pglog | -0.0 | -14.1 | -14.1 | 14 / 18 / 18 |
| pytest | +0.0 | -8.2 | -8.2 | 25 / 41 / 41 |
| rspec | -0.7 | -11.5 | -10.8 | 18 / 32 / 34 |
| sql | +0.0 | -11.3 | -11.3 | 14 / 48 / 48 |
| trace-node | -0.6 | -12.0 | -11.5 | 14 / 41 / 48 |
| trace-python | -0.0 | -12.8 | -12.8 | 16 / 23 / 23 |
| webpack | +0.2 | -13.0 | -13.1 | 23 / 41 / 41 |

### Per family at 252K: weighted pooled `effective_real` change % and facts kept %

| Family | 1.10.0 vs 1.9.0 | 1.10.0 vs stock | 1.9.0 vs stock | kept stock / 1.9.0 / 1.10.0 |
|---|---|---|---|---|
| ansible | -0.0 | -30.4 | -30.4 | 31 / 48 / 48 |
| bazel | +0.4 | -30.8 | -31.1 | 8 / 48 / 47 |
| cargo | -0.7 | -29.7 | -29.4 | 9 / 43 / 43 |
| cmake | +0.3 | -29.6 | -29.7 | 22 / 38 / 38 |
| gcc | -0.0 | -30.3 | -30.3 | 14 / 36 / 36 |
| ghactions | -0.0 | -30.6 | -30.6 | 20 / 49 / 49 |
| gitlog | +0.5 | -30.8 | -31.1 | 22 / 22 / 19 |
| gotest | -0.0 | -31.2 | -31.2 | 15 / 53 / 53 |
| grep | +0.0 | -30.5 | -30.5 | 9 / 22 / 22 |
| jest | -1.2 | -28.4 | -27.5 | 17 / 34 / 49 |
| kubectl | -0.1 | -31.2 | -31.1 | 14 / 42 / 42 |
| maven | +0.3 | -30.9 | -31.1 | 20 / 43 / 43 |
| metrics | -0.2 | -31.3 | -31.2 | 9 / 10 / 9 |
| pglog | -0.1 | -33.2 | -33.2 | 11 / 17 / 17 |
| pytest | +0.0 | -29.0 | -29.0 | 19 / 46 / 46 |
| rspec | +0.3 | -28.6 | -28.8 | 27 / 28 / 28 |
| sql | +0.1 | -32.6 | -32.7 | 11 / 50 / 49 |
| trace-node | -0.4 | -29.4 | -29.0 | 20 / 40 / 46 |
| trace-python | -0.0 | -31.6 | -31.6 | 11 / 25 / 25 |
| webpack | -0.1 | -29.8 | -29.7 | 25 / 31 / 32 |

### Per family at 500K: weighted pooled `effective_real` change % and facts kept %

| Family | 1.10.0 vs 1.9.0 | 1.10.0 vs stock | 1.9.0 vs stock | kept stock / 1.9.0 / 1.10.0 |
|---|---|---|---|---|
| ansible | -0.1 | -28.4 | -28.3 | 18 / 47 / 48 |
| bazel | -0.0 | -27.1 | -27.1 | 10 / 46 / 46 |
| cargo | +0.0 | -28.0 | -28.0 | 8 / 43 / 43 |
| cmake | -0.1 | -28.9 | -28.8 | 10 / 41 / 42 |
| gcc | +0.1 | -28.3 | -28.4 | 11 / 33 / 33 |
| ghactions | +0.0 | -27.6 | -27.6 | 16 / 46 / 46 |
| gitlog | -0.0 | -29.4 | -29.4 | 8 / 18 / 18 |
| gotest | -0.0 | -28.6 | -28.6 | 7 / 50 / 50 |
| grep | +0.0 | -28.1 | -28.2 | 6 / 24 / 24 |
| jest | -0.7 | -26.2 | -25.7 | 17 / 39 / 52 |
| kubectl | -0.1 | -28.2 | -28.1 | 11 / 44 / 44 |
| maven | -0.1 | -29.8 | -29.7 | 8 / 40 / 41 |
| metrics | +0.1 | -28.1 | -28.2 | 7 / 15 / 15 |
| pglog | -0.0 | -29.5 | -29.5 | 11 / 15 / 15 |
| pytest | +0.0 | -26.2 | -26.2 | 13 / 46 / 46 |
| rspec | +0.2 | -28.0 | -28.2 | 8 / 26 / 27 |
| sql | -0.0 | -29.8 | -29.8 | 10 / 48 / 49 |
| trace-node | -0.3 | -27.7 | -27.5 | 14 / 38 / 44 |
| trace-python | -0.1 | -26.7 | -26.6 | 11 / 22 / 23 |
| webpack | -0.1 | -27.9 | -27.8 | 11 / 40 / 42 |

### Per family at 1M: weighted pooled `effective_real` change % and facts kept %

| Family | 1.10.0 vs 1.9.0 | 1.10.0 vs stock | 1.9.0 vs stock | kept stock / 1.9.0 / 1.10.0 |
|---|---|---|---|---|
| ansible | +0.0 | -24.0 | -24.0 | 11 / 47 / 48 |
| bazel | -0.1 | -22.9 | -22.9 | 10 / 45 / 46 |
| cargo | -0.1 | -20.0 | -19.9 | 6 / 37 / 39 |
| cmake | +0.2 | -22.9 | -23.1 | 6 / 35 / 36 |
| gcc | -0.0 | -24.2 | -24.2 | 8 / 33 / 33 |
| ghactions | +0.0 | -22.7 | -22.7 | 12 / 43 / 44 |
| gitlog | -0.0 | -23.9 | -23.9 | 7 / 20 / 20 |
| gotest | -0.1 | -22.1 | -22.1 | 4 / 44 / 44 |
| grep | +0.0 | -22.0 | -22.0 | 7 / 21 / 21 |
| jest | -0.5 | -17.6 | -17.1 | 12 / 36 / 49 |
| kubectl | -0.1 | -25.5 | -25.4 | 10 / 43 / 43 |
| maven | -0.1 | -25.9 | -25.8 | 6 / 37 / 38 |
| metrics | -0.0 | -25.5 | -25.4 | 9 / 19 / 19 |
| pglog | -0.0 | -25.4 | -25.4 | 6 / 12 / 12 |
| pytest | +0.0 | -19.6 | -19.6 | 10 / 42 / 42 |
| rspec | -0.2 | -21.7 | -21.6 | 9 / 32 / 34 |
| sql | +0.0 | -23.6 | -23.6 | 7 / 47 / 47 |
| trace-node | -0.5 | -23.7 | -23.3 | 8 / 34 / 42 |
| trace-python | -0.0 | -22.2 | -22.1 | 12 / 19 / 19 |
| webpack | -0.0 | -23.5 | -23.5 | 11 / 42 / 41 |

### 64K cache-break check, development matrix shapes (seeds 0-5; committed P1c rows, reproduced exactly)

| Shape | Arm | compactions | cache breaks | prune commits | facts kept / needed |
|---|---|---|---|---|---|
| refactor | stock_defaults | 2.00 | 2.00 | 0.00 | 13.00 / 16.00 |
| refactor | engine_1_9_0 | 0.00 | 6.67 | 6.67 | 13.67 / 16.00 |
| refactor | engine_1_10_0 | 0.00 | 6.67 | 6.67 | 13.67 / 16.00 |
| smallrows | stock_defaults | 2.00 | 2.00 | 0.00 | 10.00 / 17.00 |
| smallrows | engine_1_9_0 | 2.00 | 2.00 | 0.00 | 10.67 / 17.00 |
| smallrows | engine_1_10_0 | 2.00 | 2.00 | 0.00 | 10.67 / 17.00 |

64K weighted `effective_real` (both shapes): engine_1_10_0 vs engine_1_9_0: +0.0 [+0.0, +0.0]; engine_1_10_0 vs stock_defaults: +6.7 [+3.6, +11.3]; engine_1_9_0 vs stock_defaults: +6.7 [+3.6, +11.3].
