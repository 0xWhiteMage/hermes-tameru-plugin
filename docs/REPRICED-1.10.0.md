# 1.10.0 repriced under the dated price profiles (P1c, replay only)

**Every figure here is replay-only.** No live probe has been billed against any profile, so no number below is a dollar claim, and none
should be read as one until a live probe per profile lands within ±10%.

## What was run

- **Arms**, each run once (engine decisions do not depend on the price): `stock_defaults` (Hermes as shipped), `engine_1_9_0` (this plugin
  at 836c5d3 with its vendored engine 1.9.0, run from a scratch worktree with this commit's `tests/eval` harness; the only plugin-code
  difference from today is that acceptance now goes through the engine's `decide()`) and `engine_1_10_0` (this tree, vendored engine
  1.10.0 bbb0c38). Product hashes are in the rows file's header.
- **Matrix**: the six development shapes (`default`, `oncall`, `refactor`, `smallrows`, `i18n`, `bursty`) × 64K / 128K / 200K / 252K ×
  seeds 0-5, structural seeds, Hermes' measured 39,993-character tool block, Hermes 3b42157858. 432 rows:
  [`tests/eval/results/repriced-1.10.0-rows.json`](../tests/eval/results/repriced-1.10.0-rows.json). No holdout or blind shape was touched.
- **Pricing ledger.** `matrix.run(..., price_log=True)` (`TAMERU_MATRIX_PRICE_LOG=1`) adds `row["pricing"]`: per sent request the
  characters sent, the common prefix with the previous request, the virtual send time, Hermes' rough prompt tokens, the user turn and the
  Anthropic breakpoint positions; per summary call its input and output characters; and the read-back terms of the three prices. Without
  the option every output is byte-identical to before (tested), and the rows with the ledger equal the rows without it once `pricing` is
  removed (checked on all 432 rows).
- **Prices** come from [`docs/results/prices-2026-11-19.json`](results/prices-2026-11-19.json), a copy of the engine's
  `src/tameru/prices.json` at 7a7bb83 (sha256 `070dcfa3…faa0`, pinned in `tests/eval/repricing.py` and checked against the engine file
  when it is beside the plugin; `REPLAY_PRICES` may name another path, which must carry the same hash).
- **Cost under a profile** (`tests/eval/repricing.py`), per answered turn, in characters at miss price (uncached input = 1):
  r × cached-read + w × written + summary input (cold, 1×) + o × summary output, each request scaled by the profile's long-context tier,
  plus the read-back terms of `effective` / `effective_real` / `effective_finder` at the same r and w. At r = 0.10, w = 1.25, o = 0, exact
  prefix and no expiry it reproduces every committed `base`, `effective`, `effective_real` and `effective_finder` exactly (tested on every
  row). A post-hoc TTL equals the replay's own `cache_ttl_s` run exactly (real-Hermes test).
- **Provider rules (primary):** DeepSeek reads only whole 64-token prefix units below an edit (an append-only request keeps its whole
  prefix); Anthropic hits only at a breakpoint an earlier request wrote (Hermes' system-and-3 layout); OpenAI at the exact prefix. Haiku's
  ×5 above 100K prompt tokens applies to every price of that request. The generic OpenAI profiles have no output price: o = 5 (the round-2
  plan's stated assumption).
- **Expiry:** primary = no expiry. Sensitivities: the profile's TTL, 300 s and 3,600 s, each with the `mixed` think time (10 min before
  every third user turn, 45 s otherwise) added to the replay clock for pricing only; and the profile TTL with `slow` (10 min before every
  turn). The replay's own clock (20 s per request, no think time) never lets a 5-minute TTL lapse, so a TTL alone changes nothing.
- **Weights** (round-2 plan, section 4): Hermes = `anthropic-opus-5.5-5m`, `anthropic-sonnet-5.5-5m`, `anthropic-haiku-5.5-5m`,
  `openai-generic-30m`, `deepseek-v4-pro`, 1/5 each. Every other profile is a sensitivity.
- **Statistics:** `tests/eval/stats.py`: mean of per-cell relative changes, paired on scenario, window and seed, 95% cluster bootstrap with
  scenarios as clusters (6 clusters, 36 cells per window), seed 0; 10,000 resamples for the registered profiles and the original price,
  2,000 for sensitivities. The pooled D of the plan (Σ cost change / Σ reference cost) is printed beside each mean. The weighted interval
  resamples the weighted per-cell change. With 6 clusters these intervals are wide and approximate.
- **Integrity:** every arm against itself returns exactly zero at every window, under every profile, TTL variant and price field (2,196
  checks in `repriced_report.py`, and a test over the committed rows). No setting was tuned and no arm was added, so the settings-tried
  ledger is unchanged.

Regenerate: `python tests/eval/repriced_report.py tests/eval/results/repriced-1.10.0-rows.json --json docs/results/repriced-1.10.0.json`
(no Hermes needed). Every sign change found is listed in [`docs/results/repriced-1.10.0.json`](results/repriced-1.10.0.json)
(`sign_changes`).

## Results

- **1.10.0 against 1.9.0: no change at any window or profile.** The two engines make almost the same decisions on these shapes; every
  difference is within ±0.1% (except +0.5% [-0.1, +1.9] for Haiku at 252K, from the 100K long-context tier) and no interval excludes zero.
  Facts, compactions and cache breaks per session are the same to two decimals.
- **Against stock (both engines), registered weights, no expiry, `effective_real`:** 64K -5.6% [-13.0, +2.2] (original price -4.2%),
  128K -15.7% [-20.9, -8.4] (-16.9%), 200K -19.5% [-26.6, -10.2] (-15.6%), 252K -16.5% [-29.4, -4.4] (-13.2%). The weighted figure is
  pulled down at 200K and 252K by Haiku's long-context tier: stock's prompts cross 100K tokens far more often (12 and 17 requests per
  session against 1 and 4), and those requests cost 5×. Opus, Sonnet and DeepSeek show a smaller saving than the original price at
  128K and above (their cache reads cost less than 0.10, so a shorter prompt saves less); OpenAI 30-minute (same r and w as the original)
  is about the same or slightly larger. At 64K every registered profile shows a larger saving (stock's compaction summaries are now priced
  as output).
- **`refactor` at 64K stays the one clear loss, and grows:** +7.6% at the original price, +9.4% weighted (Opus/Sonnet +12.3%, DeepSeek
  pro +10.2%, Haiku +7.3%, OpenAI +4.7%). `smallrows` +3.3% → +4.1% at 64K and +0.8% → +1.4% at 128K.
- **Think time favours Tameru, as before:** with each profile's own TTL and `mixed` pauses the weighted saving is -17.3% at 128K and -23.2% at
  200K; at 64K it shrinks to -4.2% (stock's prefix is rewritten by its own compactions anyway).
- **Trigger crossing (stock, sessions with ≥1 / ≥2 compactions):** 64K 100% / 100%, 128K 100% / 33%, 200K 33% / 0%, 252K 31% / 0%.
  Only 64K and 128K meet the round-2 rule (≥70% / ≥30%); 200K and 252K do not, so their numbers are report-only and say little about a
  long session. 252K is included because 31% of sessions do reach the trigger there; longer scenarios are P2.

## Sign changes against the original 0.10 / 1.25 price

A sign change is counted when the cluster interval's verdict (cheaper / undecided / dearer) moves, or when the point estimates have
opposite signs and one is at least 0.5 points from zero.

- **Registered weighted result, no expiry: none.** Every comparison keeps its sign and its verdict at every window, for all three prices.
- **Single profiles (no expiry, `effective_real`, vs stock, the same for both engines):** DeepSeek pro (-8.0% [-17.3, +0.8]) and flash at
  252K, and Opus/Sonnet 1-hour at 252K, lose the "cheaper, interval excludes zero" verdict of the original price (-13.2% [-24.1, -2.9]);
  OpenAI 30-minute at 64K gains it (-6.8% [-13.3, -0.2], original -4.2% [-10.0, +1.9]). DeepSeek pro and Opus/Sonnet 5-minute lose the
  `effective_finder` verdict at 200K (DeepSeek pro also at 128K).
- **Sensitivities (weighted):** only `effective_finder` changes: at 64K its point estimate turns from +3.9% to -0.7% (300 s + mixed) and
  -1.6% (profile TTL + slow), intervals including zero either way; at 252K it becomes significantly cheaper (-15% to -20%).
- **1.10.0 vs 1.9.0:** no sign change under any profile or sensitivity.

## What is not verified

- DeepSeek's prefix-unit size (64 tokens assumed) and its hit rate as a function of the idle gap.
- Where Hermes' Anthropic breakpoints land on a real request (the replay places them by Hermes' system-and-3 rule on its own wire; the
  20-block lookback limit is not modelled, so hits are an upper bound), and whether a read refreshes a 1-hour entry (assumed it does).
- OpenAI: generic profiles, output price assumed (o = 5), the 272K tier not reached, prefix granularity not modelled (exact).
- Tokens are characters / 4 (Hermes' rough estimate), so the Haiku 100K tier is crossed by estimate, not by a tokenizer.
- Summary output length comes from the replay's deterministic stand-in summarizer, not a model; main-model output is not priced (it is
  the same script for every arm apart from failed turns, and no arm failed a turn here).
- The think-time profiles are assumptions; the engines ran without them (the clock can move Hermes' backoffs, so a run with think time
  could decide differently).
- 1.9.0 is the plugin at 836c5d3, not a 1.9.0 release build.

## Tables

### `effective_real`, engine_1_10_0 vs engine_1_9_0 (mean change %, cluster 95% interval; pooled D in brackets after)

| Profile | 64K | 128K | 200K | 252K |
|---|---|---|---|---|
| original | -0.0 [-0.0, +0.0] (-0.0) | -0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.0 [-0.1, +0.1] (+0.0) |
| anthropic-opus-5.5-5m | -0.0 [-0.0, +0.0] (-0.0) | +0.0 [-0.0, +0.0] (+0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.0 [-0.1, +0.1] (+0.0) |
| anthropic-sonnet-5.5-5m | -0.0 [-0.0, +0.0] (-0.0) | +0.0 [-0.0, +0.0] (+0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.0 [-0.1, +0.1] (+0.0) |
| anthropic-haiku-5.5-5m | -0.0 [-0.0, +0.0] (-0.0) | +0.0 [-0.0, +0.0] (+0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.5 [-0.1, +1.9] (+0.4) |
| openai-generic-30m | -0.0 [-0.0, +0.0] (-0.0) | -0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.0 [-0.1, +0.1] (+0.0) |
| deepseek-v4-pro | -0.0 [-0.0, +0.0] (-0.0) | -0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.0 [-0.1, +0.1] (+0.0) |
| deepseek-flash | -0.0 [-0.1, +0.0] (-0.0) | -0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.4, +0.0] (-0.1) | -0.0 [-0.1, +0.1] (+0.0) |
| anthropic-opus-5.5-1h | -0.0 [-0.0, +0.0] (-0.0) | +0.0 [-0.0, +0.0] (+0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.0 [-0.1, +0.1] (+0.0) |
| anthropic-sonnet-5.5-1h | -0.0 [-0.0, +0.0] (-0.0) | +0.0 [-0.0, +0.0] (+0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.0 [-0.1, +0.1] (+0.0) |
| anthropic-haiku-5.5-1h | -0.0 [-0.0, +0.0] (-0.0) | +0.0 [-0.0, +0.0] (+0.0) | -0.1 [-0.3, +0.0] (-0.1) | +0.5 [-0.1, +1.9] (+0.4) |
| openai-generic-12h | -0.0 [-0.0, +0.0] (-0.0) | -0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.3, +0.0] (-0.1) | +0.0 [-0.1, +0.1] (+0.0) |
| **weighted (Hermes, 1/5 each), none** | -0.0 [-0.0, +0.0] (-0.0) | +0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.1 [-0.1, +0.4] (+0.1) |
| **weighted (Hermes, 1/5 each), profile+mixed** | -0.0 [-0.0, +0.0] (-0.0) | -0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.6, +0.0] (-0.2) | +0.1 [-0.1, +0.3] (+0.1) |
| **weighted (Hermes, 1/5 each), 300s+mixed** | +0.0 [-0.0, +0.0] (-0.0) | -0.0 [-0.0, +0.0] (-0.0) | -0.2 [-0.7, +0.0] (-0.2) | +0.1 [-0.1, +0.3] (+0.1) |
| **weighted (Hermes, 1/5 each), 3600s+mixed** | -0.0 [-0.0, +0.0] (-0.0) | +0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.1 [-0.1, +0.4] (+0.1) |
| **weighted (Hermes, 1/5 each), profile+slow** | -0.0 [-0.0, +0.0] (-0.0) | -0.0 [-0.0, +0.0] (-0.0) | -0.1 [-0.4, +0.0] (-0.1) | +0.1 [-0.1, +0.5] (+0.1) |

### `effective_real`, engine_1_10_0 vs stock_defaults (mean change %, cluster 95% interval; pooled D in brackets after)

| Profile | 64K | 128K | 200K | 252K |
|---|---|---|---|---|
| original | -4.2 [-10.0, +1.9] (-4.7) | -16.9 [-21.9, -9.4] (-17.1) | -15.6 [-21.1, -8.5] (-16.2) | -13.2 [-24.1, -2.9] (-16.5) |
| anthropic-opus-5.5-5m | -4.9 [-13.1, +3.7] (-6.7) | -14.8 [-20.1, -7.7] (-15.6) | -11.9 [-17.3, -6.1] (-13.3) | -9.6 [-19.7, -0.0] (-13.7) |
| anthropic-sonnet-5.5-5m | -4.9 [-13.1, +3.7] (-6.7) | -14.8 [-20.1, -7.7] (-15.6) | -11.9 [-17.3, -6.1] (-13.3) | -9.6 [-19.7, -0.0] (-13.7) |
| anthropic-haiku-5.5-5m | -6.2 [-13.4, +1.1] (-7.1) | -18.5 [-24.0, -10.3] (-18.8) | -48.1 [-65.5, -24.4] (-54.9) | -42.0 [-66.1, -16.0] (-57.7) |
| openai-generic-30m | -6.8 [-13.3, -0.2] (-7.5) | -18.0 [-23.2, -10.1] (-18.3) | -15.8 [-21.4, -8.6] (-16.4) | -13.3 [-24.3, -2.9] (-16.7) |
| deepseek-v4-pro | -5.0 [-12.3, +2.8] (-6.7) | -12.7 [-17.2, -6.3] (-13.6) | -9.9 [-14.7, -4.9] (-11.3) | -8.0 [-17.3, +0.8] (-11.9) |
| deepseek-flash | -5.3 [-13.2, +3.2] (-7.5) | -10.5 [-15.2, -4.5] (-11.8) | -6.3 [-11.5, -1.8] (-8.3) | -4.8 [-13.6, +3.8] (-9.0) |
| anthropic-opus-5.5-1h | -2.1 [-10.6, +7.1] (-4.3) | -11.4 [-16.5, -5.5] (-12.6) | -8.4 [-14.0, -3.4] (-10.4) | -6.6 [-16.0, +2.4] (-10.9) |
| anthropic-sonnet-5.5-1h | -2.1 [-10.6, +7.1] (-4.3) | -11.4 [-16.5, -5.5] (-12.6) | -8.4 [-14.0, -3.4] (-10.4) | -6.6 [-16.0, +2.4] (-10.9) |
| anthropic-haiku-5.5-1h | -3.7 [-11.1, +4.1] (-5.0) | -15.2 [-20.3, -8.3] (-15.9) | -45.8 [-62.3, -22.4] (-53.0) | -40.3 [-63.2, -14.9] (-56.3) |
| openai-generic-12h | -3.3 [-9.8, +3.9] (-4.6) | -12.9 [-17.1, -7.0] (-13.6) | -11.1 [-15.7, -5.8] (-12.1) | -9.1 [-18.5, -0.0] (-12.7) |
| **weighted (Hermes, 1/5 each), none** | -5.6 [-13.0, +2.2] (-6.9) | -15.7 [-20.9, -8.4] (-16.4) | -19.5 [-26.6, -10.2] (-21.9) | -16.5 [-29.4, -4.4] (-22.7) |
| **weighted (Hermes, 1/5 each), profile+mixed** | -4.2 [-10.6, +2.5] (-4.9) | -17.3 [-22.5, -9.4] (-17.6) | -23.2 [-30.9, -12.6] (-24.9) | -19.7 [-33.1, -6.3] (-25.2) |
| **weighted (Hermes, 1/5 each), 300s+mixed** | -3.8 [-9.8, +2.3] (-4.2) | -19.2 [-25.0, -10.5] (-19.3) | -26.1 [-34.4, -14.3] (-27.4) | -22.0 [-35.9, -7.7] (-27.1) |
| **weighted (Hermes, 1/5 each), 3600s+mixed** | -5.5 [-13.0, +2.2] (-6.9) | -15.7 [-20.9, -8.4] (-16.4) | -19.4 [-26.6, -10.1] (-21.8) | -16.4 [-29.3, -4.3] (-22.6) |
| **weighted (Hermes, 1/5 each), profile+slow** | -5.2 [-11.0, +0.6] (-5.7) | -18.6 [-24.4, -10.1] (-18.8) | -24.4 [-32.6, -12.9] (-26.0) | -21.0 [-34.5, -7.5] (-26.2) |

### `effective_real`, engine_1_9_0 vs stock_defaults (mean change %, cluster 95% interval; pooled D in brackets after)

| Profile | 64K | 128K | 200K | 252K |
|---|---|---|---|---|
| original | -4.2 [-10.0, +1.9] (-4.7) | -16.9 [-21.9, -9.4] (-17.1) | -15.5 [-21.0, -8.5] (-16.1) | -13.2 [-24.1, -2.9] (-16.5) |
| anthropic-opus-5.5-5m | -4.9 [-13.1, +3.7] (-6.7) | -14.8 [-20.1, -7.7] (-15.6) | -11.8 [-17.2, -6.0] (-13.2) | -9.6 [-19.7, -0.0] (-13.7) |
| anthropic-sonnet-5.5-5m | -4.9 [-13.1, +3.7] (-6.7) | -14.8 [-20.1, -7.7] (-15.6) | -11.8 [-17.2, -6.0] (-13.2) | -9.6 [-19.7, -0.0] (-13.7) |
| anthropic-haiku-5.5-5m | -6.2 [-13.4, +1.1] (-7.1) | -18.5 [-24.0, -10.3] (-18.8) | -48.1 [-65.4, -24.3] (-54.9) | -42.1 [-66.3, -16.1] (-57.9) |
| openai-generic-30m | -6.8 [-13.3, -0.2] (-7.5) | -18.0 [-23.2, -10.1] (-18.3) | -15.7 [-21.3, -8.6] (-16.3) | -13.4 [-24.4, -2.9] (-16.7) |
| deepseek-v4-pro | -5.0 [-12.3, +2.8] (-6.7) | -12.7 [-17.2, -6.3] (-13.6) | -9.8 [-14.6, -4.9] (-11.2) | -8.0 [-17.3, +0.8] (-11.9) |
| deepseek-flash | -5.3 [-13.2, +3.2] (-7.5) | -10.5 [-15.2, -4.5] (-11.8) | -6.2 [-11.4, -1.7] (-8.2) | -4.8 [-13.6, +3.8] (-9.0) |
| anthropic-opus-5.5-1h | -2.1 [-10.5, +7.1] (-4.3) | -11.4 [-16.5, -5.5] (-12.6) | -8.3 [-13.8, -3.3] (-10.3) | -6.6 [-16.0, +2.4] (-10.9) |
| anthropic-sonnet-5.5-1h | -2.1 [-10.5, +7.1] (-4.3) | -11.4 [-16.5, -5.5] (-12.6) | -8.3 [-13.8, -3.3] (-10.3) | -6.6 [-16.0, +2.4] (-10.9) |
| anthropic-haiku-5.5-1h | -3.7 [-11.1, +4.1] (-5.0) | -15.2 [-20.2, -8.3] (-15.9) | -45.8 [-62.2, -22.4] (-53.0) | -40.4 [-63.5, -14.9] (-56.5) |
| openai-generic-12h | -3.3 [-9.8, +3.9] (-4.6) | -12.9 [-17.1, -7.0] (-13.6) | -10.9 [-15.6, -5.7] (-12.0) | -9.1 [-18.5, -0.0] (-12.7) |
| **weighted (Hermes, 1/5 each), none** | -5.5 [-13.0, +2.2] (-6.9) | -15.8 [-20.9, -8.4] (-16.4) | -19.4 [-26.6, -10.1] (-21.8) | -16.6 [-29.5, -4.4] (-22.8) |
| **weighted (Hermes, 1/5 each), profile+mixed** | -4.2 [-10.6, +2.5] (-4.9) | -17.3 [-22.5, -9.4] (-17.6) | -23.1 [-30.8, -12.6] (-24.8) | -19.7 [-33.1, -6.3] (-25.2) |
| **weighted (Hermes, 1/5 each), 300s+mixed** | -3.8 [-9.8, +2.3] (-4.2) | -19.2 [-25.0, -10.5] (-19.3) | -25.9 [-34.3, -14.2] (-27.3) | -22.0 [-35.9, -7.7] (-27.1) |
| **weighted (Hermes, 1/5 each), 3600s+mixed** | -5.5 [-13.0, +2.2] (-6.9) | -15.8 [-20.9, -8.4] (-16.4) | -19.3 [-26.5, -10.0] (-21.7) | -16.4 [-29.4, -4.3] (-22.6) |
| **weighted (Hermes, 1/5 each), profile+slow** | -5.2 [-11.0, +0.6] (-5.7) | -18.6 [-24.4, -10.1] (-18.8) | -24.4 [-32.6, -12.8] (-25.9) | -21.1 [-34.6, -7.5] (-26.3) |

### All three prices, weighted, no expiry

| Comparison | Field | 64K | 128K | 200K | 252K |
|---|---|---|---|---|---|
| engine_1_10_0 vs engine_1_9_0 | effective_real | -0.0 [-0.0, +0.0] | +0.0 [-0.0, +0.0] | -0.1 [-0.4, +0.0] | +0.1 [-0.1, +0.4] |
| (original price) | effective_real | -0.0 [-0.0, +0.0] | -0.0 [-0.0, +0.0] | -0.1 [-0.4, +0.0] | +0.0 [-0.1, +0.1] |
| engine_1_10_0 vs engine_1_9_0 | effective | -0.0 [-0.0, +0.0] | +0.0 [-0.0, +0.0] | -0.1 [-0.4, +0.0] | +0.1 [-0.1, +0.5] |
| (original price) | effective | -0.0 [-0.0, +0.0] | -0.0 [-0.0, +0.0] | -0.1 [-0.4, +0.0] | +0.0 [-0.1, +0.1] |
| engine_1_10_0 vs engine_1_9_0 | effective_finder | +0.0 [-0.0, +0.1] | +0.0 [-0.0, +0.0] | -0.1 [-0.2, +0.0] | +0.1 [-0.1, +0.4] |
| (original price) | effective_finder | +0.0 [-0.0, +0.1] | -0.0 [-0.0, +0.0] | -0.1 [-0.2, +0.0] | +0.0 [-0.1, +0.1] |
| engine_1_10_0 vs stock_defaults | effective_real | -5.6 [-13.0, +2.2] | -15.7 [-20.9, -8.4] | -19.5 [-26.6, -10.2] | -16.5 [-29.4, -4.4] |
| (original price) | effective_real | -4.2 [-10.0, +1.9] | -16.9 [-21.9, -9.4] | -15.6 [-21.1, -8.5] | -13.2 [-24.1, -2.9] |
| engine_1_10_0 vs stock_defaults | effective | -4.7 [-12.0, +2.9] | -14.3 [-19.2, -7.3] | -19.5 [-26.2, -10.3] | -16.5 [-28.7, -4.8] |
| (original price) | effective | -2.9 [-8.5, +2.9] | -15.1 [-19.9, -7.9] | -15.5 [-20.7, -8.6] | -13.1 [-23.1, -3.7] |
| engine_1_10_0 vs stock_defaults | effective_finder | +1.2 [-10.4, +13.6] | -9.0 [-16.8, -1.6] | -13.0 [-21.9, -4.7] | -9.6 [-24.1, +4.3] |
| (original price) | effective_finder | +3.9 [-6.8, +15.8] | -9.2 [-17.1, -1.4] | -7.8 [-16.2, -0.3] | -4.7 [-18.3, +9.0] |
| engine_1_9_0 vs stock_defaults | effective_real | -5.5 [-13.0, +2.2] | -15.8 [-20.9, -8.4] | -19.4 [-26.6, -10.1] | -16.6 [-29.5, -4.4] |
| (original price) | effective_real | -4.2 [-10.0, +1.9] | -16.9 [-21.9, -9.4] | -15.5 [-21.0, -8.5] | -13.2 [-24.1, -2.9] |
| engine_1_9_0 vs stock_defaults | effective | -4.7 [-12.0, +2.9] | -14.3 [-19.2, -7.3] | -19.4 [-26.2, -10.2] | -16.5 [-28.7, -4.8] |
| (original price) | effective | -2.9 [-8.5, +2.9] | -15.1 [-19.9, -7.9] | -15.4 [-20.6, -8.5] | -13.1 [-23.1, -3.7] |
| engine_1_9_0 vs stock_defaults | effective_finder | +1.1 [-10.4, +13.6] | -9.0 [-16.8, -1.6] | -12.9 [-21.8, -4.7] | -9.6 [-24.2, +4.3] |
| (original price) | effective_finder | +3.9 [-6.8, +15.8] | -9.2 [-17.1, -1.4] | -7.7 [-16.2, -0.2] | -4.7 [-18.3, +9.0] |

### Exact prefix instead of the provider rule (`effective_real`, 1.10.0 vs 1.9.0)

| Profile | 64K | 128K | 200K | 252K |
|---|---|---|---|---|
| anthropic-opus-5.5-5m (exact) | -0.0 [-0.0, +0.0] | -0.0 [-0.0, +0.0] | -0.1 [-0.3, +0.0] | +0.0 [-0.1, +0.1] |
| anthropic-sonnet-5.5-5m (exact) | -0.0 [-0.0, +0.0] | -0.0 [-0.0, +0.0] | -0.1 [-0.3, +0.0] | +0.0 [-0.1, +0.1] |
| anthropic-haiku-5.5-5m (exact) | -0.0 [-0.0, +0.0] | -0.0 [-0.0, +0.0] | -0.1 [-0.3, +0.0] | +0.5 [-0.1, +1.9] |
| openai-generic-30m (exact) | -0.0 [-0.0, +0.0] | -0.0 [-0.0, +0.0] | -0.1 [-0.3, +0.0] | +0.0 [-0.1, +0.1] |
| deepseek-v4-pro (exact) | -0.0 [-0.0, +0.0] | -0.0 [-0.0, +0.0] | -0.1 [-0.3, +0.0] | +0.0 [-0.1, +0.1] |

### Cost split per answered turn (chars in miss units, no expiry): read / write / summary input / output

| Profile | Window | stock | 1.9.0 | 1.10.0 |
|---|---|---|---|---|
| original | 64K | 28K / 23K / 1K / 0.0K | 26K / 25K / 1K / 0.0K | 26K / 25K / 1K / 0.0K |
| original | 128K | 42K / 23K / 1K / 0.0K | 32K / 25K / 0K / 0.0K | 32K / 25K / 0K / 0.0K |
| original | 200K | 55K / 22K / 0K / 0.0K | 42K / 23K / 0K / 0.0K | 42K / 23K / 0K / 0.0K |
| original | 252K | 62K / 22K / 0K / 0.0K | 48K / 23K / 0K / 0.0K | 48K / 23K / 0K / 0.0K |
| anthropic-opus-5.5-5m | 64K | 14K / 24K / 1K / 2.4K | 13K / 27K / 1K / 0.7K | 13K / 27K / 1K / 0.7K |
| anthropic-opus-5.5-5m | 128K | 21K / 25K / 1K / 1.3K | 16K / 26K / 0K / 0.2K | 16K / 26K / 0K / 0.2K |
| anthropic-opus-5.5-5m | 200K | 27K / 22K / 0K / 0.2K | 21K / 23K / 0K / 0.0K | 21K / 23K / 0K / 0.0K |
| anthropic-opus-5.5-5m | 252K | 31K / 22K / 0K / 0.2K | 24K / 23K / 0K / 0.0K | 24K / 23K / 0K / 0.0K |
| anthropic-sonnet-5.5-5m | 64K | 14K / 24K / 1K / 2.4K | 13K / 27K / 1K / 0.7K | 13K / 27K / 1K / 0.7K |
| anthropic-sonnet-5.5-5m | 128K | 21K / 25K / 1K / 1.3K | 16K / 26K / 0K / 0.2K | 16K / 26K / 0K / 0.2K |
| anthropic-sonnet-5.5-5m | 200K | 27K / 22K / 0K / 0.2K | 21K / 23K / 0K / 0.0K | 21K / 23K / 0K / 0.0K |
| anthropic-sonnet-5.5-5m | 252K | 31K / 22K / 0K / 0.2K | 24K / 23K / 0K / 0.0K | 24K / 23K / 0K / 0.0K |
| anthropic-haiku-5.5-5m | 64K | 27K / 24K / 1K / 2.4K | 26K / 27K / 1K / 0.7K | 26K / 27K / 1K / 0.7K |
| anthropic-haiku-5.5-5m | 128K | 42K / 25K / 1K / 1.3K | 32K / 26K / 0K / 0.2K | 32K / 26K / 0K / 0.2K |
| anthropic-haiku-5.5-5m | 200K | 119K / 39K / 0K / 0.2K (tier 12) | 48K / 23K / 0K / 0.0K (tier 1) | 48K / 23K / 0K / 0.0K (tier 1) |
| anthropic-haiku-5.5-5m | 252K | 171K / 47K / 0K / 0.2K (tier 17) | 66K / 26K / 0K / 0.0K (tier 4) | 66K / 26K / 0K / 0.0K (tier 4) |
| openai-generic-30m | 64K | 28K / 23K / 1K / 2.4K | 26K / 25K / 1K / 0.7K | 26K / 25K / 1K / 0.7K |
| openai-generic-30m | 128K | 42K / 23K / 1K / 1.3K | 32K / 25K / 0K / 0.2K | 32K / 25K / 0K / 0.2K |
| openai-generic-30m | 200K | 55K / 22K / 0K / 0.2K | 42K / 23K / 0K / 0.0K | 42K / 23K / 0K / 0.0K |
| openai-generic-30m | 252K | 62K / 22K / 0K / 0.2K | 48K / 23K / 0K / 0.0K | 48K / 23K / 0K / 0.0K |
| deepseek-v4-pro | 64K | 9K / 18K / 1K / 1.4K | 9K / 20K / 1K / 0.4K | 9K / 20K / 1K / 0.4K |
| deepseek-v4-pro | 128K | 14K / 18K / 1K / 0.8K | 11K / 20K / 0K / 0.1K | 11K / 20K / 0K / 0.1K |
| deepseek-v4-pro | 200K | 18K / 17K / 0K / 0.1K | 14K / 19K / 0K / 0.0K | 14K / 19K / 0K / 0.0K |
| deepseek-v4-pro | 252K | 21K / 18K / 0K / 0.1K | 16K / 19K / 0K / 0.0K | 16K / 19K / 0K / 0.0K |

### Scorecard per session (price-independent)

| Window | Arm | compactions | cache breaks | prune commits | facts kept | held | lost | needed | failed turns | sessions with ≥1 / ≥2 compactions |
|---|---|---|---|---|---|---|---|---|---|---|
| 64K | stock_defaults | 2.86 | 2.86 | 0.00 | 7.47 | 7.47 | 5.69 | 13.17 | 0.00 | 100% / 100% |
| 64K | engine_1_9_0 | 0.92 | 5.81 | 4.89 | 10.00 | 10.78 | 2.39 | 13.17 | 0.00 | 64% / 28% |
| 64K | engine_1_10_0 | 0.92 | 5.81 | 4.89 | 10.00 | 10.78 | 2.39 | 13.17 | 0.00 | 64% / 28% |
| 128K | stock_defaults | 1.33 | 1.33 | 0.00 | 7.83 | 7.83 | 5.33 | 13.17 | 0.00 | 100% / 33% |
| 128K | engine_1_9_0 | 0.17 | 4.08 | 3.92 | 10.61 | 11.36 | 1.81 | 13.17 | 0.00 | 17% / 0% |
| 128K | engine_1_10_0 | 0.17 | 4.08 | 3.92 | 10.61 | 11.36 | 1.81 | 13.17 | 0.00 | 17% / 0% |
| 200K | stock_defaults | 0.33 | 0.33 | 0.00 | 11.50 | 11.50 | 1.67 | 13.17 | 0.00 | 33% / 0% |
| 200K | engine_1_9_0 | 0.00 | 1.44 | 1.44 | 11.81 | 12.67 | 0.50 | 13.17 | 0.00 | 0% / 0% |
| 200K | engine_1_10_0 | 0.00 | 1.44 | 1.44 | 11.81 | 12.67 | 0.50 | 13.17 | 0.00 | 0% / 0% |
| 252K | stock_defaults | 0.31 | 0.31 | 0.00 | 11.47 | 11.47 | 1.69 | 13.17 | 0.00 | 31% / 0% |
| 252K | engine_1_9_0 | 0.00 | 1.06 | 1.06 | 11.92 | 12.67 | 0.50 | 13.17 | 0.00 | 0% / 0% |
| 252K | engine_1_10_0 | 0.00 | 1.06 | 1.06 | 11.92 | 12.67 | 0.50 | 13.17 | 0.00 | 0% / 0% |

### Per shape, pooled `effective_real` change % (original price / registered weighted, no expiry)

| Window | Shape | 1.10.0 vs 1.9.0 | 1.10.0 vs stock | 1.9.0 vs stock |
|---|---|---|---|---|
| 64K | bursty | +0.0 / +0.0 | -14.4 / -19.1 | -14.4 / -19.1 |
| 64K | default | -0.0 / -0.0 | -6.2 / -9.6 | -6.2 / -9.6 |
| 64K | i18n | +0.0 / -0.0 | -7.9 / -7.3 | -7.9 / -7.3 |
| 64K | oncall | +0.0 / +0.0 | -7.8 / -10.9 | -7.8 / -10.9 |
| 64K | refactor | +0.0 / +0.0 | +7.6 / +9.4 | +7.6 / +9.4 |
| 64K | smallrows | +0.0 / +0.0 | +3.3 / +4.1 | +3.3 / +4.1 |
| 128K | bursty | +0.0 / +0.0 | -24.2 / -23.0 | -24.2 / -23.0 |
| 128K | default | -0.0 / -0.0 | -20.7 / -21.1 | -20.6 / -21.0 |
| 128K | i18n | +0.0 / +0.0 | -17.3 / -15.9 | -17.3 / -15.9 |
| 128K | oncall | -0.0 / -0.0 | -18.8 / -19.1 | -18.8 / -19.1 |
| 128K | refactor | +0.0 / +0.0 | -21.3 / -17.1 | -21.3 / -17.1 |
| 128K | smallrows | +0.0 / +0.0 | +0.8 / +1.4 | +0.8 / +1.4 |
| 200K | bursty | +0.0 / +0.0 | -25.2 / -31.8 | -25.2 / -31.8 |
| 200K | default | -0.1 / -0.1 | -18.8 / -23.8 | -18.7 / -23.7 |
| 200K | i18n | -0.0 / -0.0 | -18.2 / -22.8 | -18.2 / -22.8 |
| 200K | oncall | -0.4 / -0.4 | -15.8 / -23.6 | -15.5 / -23.3 |
| 200K | refactor | -0.1 / -0.1 | -17.0 / -18.9 | -16.9 / -18.8 |
| 200K | smallrows | +0.0 / +0.0 | +0.5 / +1.9 | +0.5 / +1.9 |
| 252K | bursty | +0.0 / +0.0 | -30.4 / -36.2 | -30.4 / -36.2 |
| 252K | default | -0.1 / -0.1 | -13.6 / -18.8 | -13.5 / -18.7 |
| 252K | i18n | +0.0 / +0.0 | -5.7 / -9.3 | -5.7 / -9.3 |
| 252K | oncall | +0.1 / +0.6 | -31.7 / -37.5 | -31.8 / -37.8 |
| 252K | refactor | +0.0 / +0.0 | +1.2 / -0.2 | +1.2 / -0.2 |
| 252K | smallrows | +0.0 / +0.0 | +0.5 / +1.9 | +0.5 / +1.9 |

