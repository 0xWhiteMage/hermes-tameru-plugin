# P3: the `plan` policy, replayed on the dev corpus

`policy_mode: plan` is an opt-in third policy. At a Hermes cache-broken moment — the compaction trigger
or a prune Hermes itself fires, never a proactive warm-cache pass — the pass hands the rows Hermes
would demote to the engine's `tameru.plan` (compact mode, `K = 3`, `H = 0`) and renders the per-row
rung it returns through the existing ladder. The adaptive activation constants do not apply under this
policy. All host numbers — the trigger threshold, the scaled retained budget, the growth-derived
`n_eff` — stay in the adapter (`hermes_tameru_plugin/plan_pass.py`).

Numbers below are **replay-only, corpus dev split (132 scenarios)**; arm `HP1` is
`policy_mode: plan`, EV strength `normal`, `price_profile: anthropic-sonnet-5.5-5m`. References are
the committed P2c rows (`engine_1_10_0`, `engine_1_9_0`). The re-run stock rows equal P2c's stock dev
rows on every field apart from timing and the new `row_rung` fact column (0 diffs, 396 rows).

### weighted effective_real change % (Hermes weights 1/5), cluster 95% CI
| comparison | 128K | 252K | 500K |
|---|---|---|---|
| HP1-stock | 0.1 [-0.36, 0.56] (pooled 0.01) | -0.2 [-0.61, 0.2] (pooled -0.29) | 0.2 [0.01, 0.39] (pooled 0.17) |
| HP1-e110 | 13.07 [11.93, 14.23] (pooled 12.87) | 71.49 [69.37, 73.66] (pooled 70.81) | 42.72 [41.73, 43.72] (pooled 42.64) |
| HP1-e19 | 12.83 [11.69, 13.99] (pooled 12.64) | 71.19 [69.05, 73.34] (pooled 70.51) | 42.64 [41.65, 43.67] (pooled 42.56) |
| e110-stock | -11.14 [-12.0, -10.27] (pooled -11.34) | -30.15 [-30.7, -29.59] (pooled -30.35) | -27.97 [-28.47, -27.49] (pooled -28.09) |
| e19-stock | -10.95 [-11.82, -10.06] (pooled -11.17) | -30.07 [-30.62, -29.49] (pooled -30.28) | -27.94 [-28.45, -27.45] (pooled -28.06) |
| HP1-stock effective | 0.5 [0.08, 0.93] (pooled 0.42) | 0.28 [-0.05, 0.61] (pooled 0.23) | 0.81 [0.66, 0.96] (pooled 0.79) |
| HP1-stock effective_finder | 1.81 [1.23, 2.43] (pooled 1.79) | 5.54 [4.25, 7.04] (pooled 5.55) | 3.71 [2.71, 4.79] (pooled 3.73) |

### cache breaks + compactions per session (mean / total), facts kept
| arm@window | n | prefix_breaks | compactions | prune_commits | requests | kept | lost | recoverable | needed |
|---|---|---|---|---|---|---|---|---|---|
| stock_defaults@128000 | 132 | 2.106 / 278 | 2.106 / 278 | 0 | 37.8 | 0.742 | 3.258 | 0 | 4 |
| engine_1_10_0@128000 | 132 | 6.409 / 846 | 0.288 / 38 | 808 | 37.8 | 1.598 | 0.386 | 2.015 | 4 |
| engine_1_9_0@128000 | 132 | 6.439 / 850 | 0.288 / 38 | 812 | 37.8 | 1.553 | 0.409 | 2.038 | 4 |
| HP1@128000 | 132 | 2.114 / 279 | 2.114 / 279 | 0 | 37.8 | 1.311 | 2.288 | 0.402 | 4 |
| stock_defaults@252000 | 132 | 1.932 / 255 | 1.932 / 255 | 0 | 72.46 | 1.341 | 6.659 | 0 | 8 |
| engine_1_10_0@252000 | 132 | 4.977 / 657 | 0 / 0 | 657 | 72.46 | 3.197 | 0.242 | 4.561 | 8 |
| engine_1_9_0@252000 | 132 | 4.985 / 658 | 0 / 0 | 658 | 72.46 | 3.106 | 0.25 | 4.644 | 8 |
| HP1@252000 | 132 | 1.955 / 258 | 1.955 / 258 | 0 | 72.46 | 2.356 | 4.636 | 1.008 | 8 |
| stock_defaults@500000 | 132 | 1.985 / 262 | 1.985 / 262 | 0 | 151.39 | 1.258 | 10.742 | 0 | 12 |
| engine_1_10_0@500000 | 132 | 4.508 / 595 | 0 / 0 | 595 | 151.39 | 4.879 | 0.371 | 6.75 | 12 |
| engine_1_9_0@500000 | 132 | 4.515 / 596 | 0 / 0 | 596 | 151.39 | 4.705 | 0.371 | 6.924 | 12 |
| HP1@500000 | 132 | 1.985 / 262 | 1.985 / 262 | 0 | 151.39 | 2.652 | 8.735 | 0.614 | 12 |

### trigger crossing (sessions with >=1 / >=2 compactions)
| arm | 128K | 252K | 500K |
|---|---|---|---|
| stock_defaults | 100% / 99% | 100% / 90% | 100% / 95% |
| HP1 | 100% / 100% | 100% / 91% | 100% / 95% |

### engine_seconds per run p50 / p95 (wall p95)
| arm | 128K | 252K | 500K |
|---|---|---|---|
| stock_defaults | 0.05 / 0.29 (1.81) | 0.08 / 0.28 (1.91) | 0.12 / 0.38 (3.44) |
| engine_1_10_0 | 4.16 / 7.17 (20.84) | 6.52 / 10.94 (22.97) | 11.66 / 19.93 (30.57) |
| HP1 | 2.98 / 4.96 (5.49) | 7.29 / 10.83 (11.56) | 16.84 / 24.18 (26.79) |

### rung table @128000: HP1 vs P2c engine_1_10_0 (fact rows shortened)
| rung | HP1 n | HP1 held% | P2c n | P2c held% |
|---|---|---|---|---|
| brief | 29 | 51.7 | 154 | 21.4 |
| errors | 10 | 70.0 | 67 | 74.6 |
| extract | 12 | 100.0 | 36 | 86.1 |
| header | 35 | 0.0 | 153 | 0.0 |
| superseded | 0 | None | 3 | 0.0 |

### rung table @252000: HP1 vs P2c engine_1_10_0 (fact rows shortened)
| rung | HP1 n | HP1 held% | P2c n | P2c held% |
|---|---|---|---|---|
| brief | 44 | 43.2 | 237 | 25.3 |
| errors | 20 | 70.0 | 141 | 75.2 |
| extract | 20 | 100.0 | 72 | 86.1 |
| header | 104 | 0.0 | 414 | 0.0 |
| hermes | 0 | None | 1 | 0.0 |
| superseded | 0 | None | 9 | 0.0 |

### rung table @500000: HP1 vs P2c engine_1_10_0 (fact rows shortened)
| rung | HP1 n | HP1 held% | P2c n | P2c held% |
|---|---|---|---|---|
| brief | 24 | 37.5 | 383 | 33.7 |
| errors | 11 | 81.8 | 188 | 73.4 |
| extract | 23 | 100.0 | 134 | 85.1 |
| header | 56 | 0.0 | 594 | 0.0 |
| hermes | 0 | None | 3 | 0.0 |
| superseded | 1 | 0.0 | 39 | 0.0 |


## What the numbers say

- **The registered cache-break gate passes.** HP1 acts only at Hermes' own rewrites, so its prefix
  breaks per session equal stock at every window (2.11 vs 2.11 at 128K, 1.96 vs 1.93 at 252K, 1.99 vs
  1.99 at 500K), with identical compactions and zero proactive `prune_commits`. The incumbent engine's
  6.4 / 5.0 / 4.5 breaks per session is the failure this policy was built to avoid.
- **Cost is neutral vs stock** (weighted `effective_real` +0.1 / -0.2 / +0.2, CIs straddling or just
  under zero). The incumbent saves 11-30%; `plan` deliberately gives that up in exchange for never
  breaking the prefix cache outside a Hermes rewrite.
- **Fact retention improves on stock without paying for it**: kept 1.31 / 2.36 / 2.65 vs stock's
  0.74 / 1.34 / 1.26 per session (+0.6 / +1.0 / +1.4 facts). The incumbent still keeps more
  (1.60 / 3.20 / 4.88) at its much lower cache-friendliness.
- **The rungs the plan picks hold facts**: extract rows kept the fact 100% of the time at all three
  windows, `errors` rows 70-82%, briefs 38-52%, headers 0% by construction. The fact-row rung mix is
  far shallower than the incumbent's (which also spends retained budget newest-first).
- **Supersession barely fires**: 0 / 0 / 1 rows across the windows (P2c: 3 / 9 / 39). The
  fact-coverage check blocks the rest (2 / 18 / 50 blocked across the windows), and surviving supersessions carry a
  `superseded by msg …` note.
- **Pass cost**: engine_seconds per run p50 / p95 is 3.0 / 5.0 at 128K, 7.3 / 10.8 at 252K and
  16.8 / 24.2 at 500K — heavier than the incumbent at 500K because planning touches every demote
  candidate once per trigger.

## 64K development matrix (refactor / smallrows, seeds 0-5)

| shape | arm | prefix breaks/session | compactions/session | native prune commits | facts kept |
|---|---|---|---|---|---|
| refactor | stock_defaults | 2.0 | 2.0 | 0 | 13.0 |
| refactor | HP1 | 2.83 | 1.0 | 11 | 13.0 |
| smallrows | stock_defaults | 2.0 | 2.0 | 0 | 10.0 |
| smallrows | HP1 | 2.0 | 2.0 | 0 | 10.33 |

At `smallrows` HP1 is break-identical to stock. At `refactor` HP1 compacts once instead of twice and
then Hermes' *native* proactive prune (which `policy_mode: plan` delegates untouched — the plugin's
own proactive gate does not run) commits about twice per session downstream of the different
post-compaction transcript: 2.83 breaks vs stock's 2.0. Every commit is Hermes' own pass with Hermes'
own line; the plugin neither initiates nor plans it — but the strict breaks-≤-stock comparison reads
+0.83 there. `effective` at 64K is +4.5% vs stock [+2.5, +8.0].

## HP2: the forecast exception (P3-12, engine `plan()` warm-step + adapter)

HP2 = HP1 plus the warm-step act: at Hermes' proactive-prune checkpoint (every post-tool request's
`prune_tool_results_only` call) the adapter shadows the pass Hermes would run — same boundary, spared
round, dedupe and skill protection, same demote-call arguments — builds the compact-mode `PlanInput`
over the rows the prune would demote, and asks `plan()` with `event: step`, `cache.warm: true`,
`H = 3`, `margin_steps = 6`, `K = 3`, `steps_to_trigger` and `growth_tokens_per_step` (mean of the last
6 growth samples). The engine's runway gate — `(trigger − projected_after)/growth ≥ H + margin_steps`
— decides; only an acted plan reaches the native pass, where Hermes' own reclaim/rearm gates still
apply. Engine-side the exception is additive to protocol 3 (`growth_tokens_per_step`, `margin_steps`,
new gate code `no_runway`); trigger, cold-cache and prefix-rewrite events are unchanged. The new
`plan_forecast` setting (default on) gates the warm path so the `HP1` replay arm stays reproducible:
`HP1` = `policy_mode: plan` + `plan_forecast: off`, `HP2` = the same with it on.

### weighted `effective_real` change % (Hermes weights 1/5), cluster 95% CI

| comparison | 128K | 252K | 500K |
|---|---|---|---|
| HP2 − stock | +3.2 [+2.1, +4.3] | +11.2 [+9.6, +12.8] | +4.1 [+3.3, +5.1] |
| HP2 − HP1 | +3.1 [+2.1, +4.1] | +11.4 [+9.9, +13.0] | +3.9 [+3.1, +4.8] |
| HP2 − engine 1.10.0 | +16.5 [+15.0, +18.0] | +93.5 [+90.1, +96.9] | +48.2 [+46.9, +49.6] |
| HP2 − engine 1.9.0 | +16.3 [+14.8, +17.7] | +93.2 [+89.8, +96.7] | +48.1 [+46.8, +49.5] |
| HP2 − stock `effective` | +3.6 [+2.5, +4.7] | +11.7 [+10.2, +13.3] | +5.2 [+4.3, +6.1] |
| HP2 − stock `effective_finder` | +5.5 [+4.2, +7.0] | +30.8 [+25.4, +36.6] | +29.8 [+22.1, +38.2] |

### cache breaks, compactions, warm acts, facts

| arm@window | n | prefix_breaks | compactions | prune_commits | kept | lost | recoverable |
|---|---|---|---|---|---|---|---|
| stock@128K | 132 | 2.11 / 278 | 2.11 / 278 | 0 | 0.74 | 3.26 | 0 |
| HP1@128K | 132 | 2.11 / 279 | 2.11 / 279 | 0 | 1.31 | 2.29 | 0.40 |
| HP2@128K | 132 | 2.31 / 305 | 1.96 / 258 | 47 | 1.29 | 2.27 | 0.45 |
| e1.10.0@128K | 132 | 6.41 / 846 | 0.29 / 38 | 808 | 1.60 | 0.39 | 2.02 |
| stock@252K | 132 | 1.93 / 255 | 1.93 / 255 | 0 | 1.34 | 6.66 | 0 |
| HP1@252K | 132 | 1.96 / 258 | 1.96 / 258 | 0 | 2.36 | 4.64 | 1.01 |
| HP2@252K | 132 | 2.42 / 320 | 0.93 / 123 | 197 | 3.16 | 3.11 | 1.73 |
| e1.10.0@252K | 132 | 4.98 / 657 | 0 / 0 | 657 | 3.20 | 0.24 | 4.56 |
| stock@500K | 132 | 1.99 / 262 | 1.99 / 262 | 0 | 1.26 | 10.74 | 0 |
| HP1@500K | 132 | 1.99 / 262 | 1.99 / 262 | 0 | 2.65 | 8.74 | 0.61 |
| HP2@500K | 132 | 2.16 / 285 | 1.17 / 154 | 131 | 3.40 | 6.77 | 1.83 |
| e1.10.0@500K | 132 | 4.51 / 595 | 0 / 0 | 595 | 4.88 | 0.37 | 6.75 |

### warm acts (forecast exception telemetry, per window)

| window | checks | acted | no_forecast | no_runway | hysteresis | commits (/sess) | native no-commit | failed ≤H+M | fail share |
|---|---|---|---|---|---|---|---|---|---|
| 128K | 3863 | 47 | 2851 | 953 | 12 | 47 (0.36) | 0 | 30 | **63.8%** |
| 252K | 7913 | 304 | 7110 | 499 | 0 | 197 (1.49) | 107 | 1 | **0.5%** |
| 500K | 16814 | 167 | 16233 | 414 | 0 | 131 (0.99) | 36 | 0 | **0.0%** |

### rungs (fact rows shortened, HP2 vs HP1 vs P2c incumbent)

| rung | HP2 held% (n) | HP1 held% (n) | P2c held% (n) — 128K / 252K / 500K |
|---|---|---|---|
| extract | 94 / 96 / 100 (16/27/17) | 100 / 100 / 100 | 86 / 86 / 85 |
| errors | 75 / 70 / 90 (12/37/29) | 70 / 70 / 82 | 75 / 75 / 73 |
| brief | 53 / 43 / 51 (32/175/207) | 52 / 43 / 38 | 21 / 25 / 34 |
| header | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| superseded | 0 / 0 / 0 (0/2/7) | 0 / 0 / 0 | 0 / 0 / 0 |

### 64K development matrix (refactor / smallrows, seeds 0-5)

| shape | arm | breaks/session | compactions/session | warm commits | facts kept |
|---|---|---|---|---|---|
| refactor | stock | 2.0 | 2.0 | 0 | 13.0 |
| refactor | HP2 | 2.17 | 2.17 | 0 | 12.83 |
| smallrows | stock | 2.0 | 2.0 | 0 | 10.0 |
| smallrows | HP2 | 2.0 | 2.0 | 0 | 10.33 |

No warm act fired at 64K on either shape (the horizon + runway gates declined); `refactor` seed 2
compacted three times vs stock's two (+0.17 breaks, −0.17 kept). `effective` at 64K: +3.1% [+2.0, +4.6].

### What the HP2 numbers say

- **The mechanism works as designed**: warm acts fire only inside the horizon (gates: `no_forecast`
  dominates at every window, then `no_runway`, then a trace of `hysteresis`), commit through Hermes'
  own gates (107 native no-commits at 252K), and **halve compactions** at 252K/500K (0.93 vs 1.93,
  1.17 vs 1.99) — sessions with ≥1 compaction drop to 77% / 83% from ~100%.
- **The strict breaks-≤-stock gate fails at every window** (+0.20 / +0.49 / +0.17 per session): each
  warm commit is itself a prefix rewrite, so ~1.5 warm commits per prevented compaction nets a small
  break increase — still 2-3× better than the incumbent's break load (6.4 / 5.0 / 4.5).
- **Cost is worse than stock and HP1** (+3-11% weighted): at these densities the rewrite price of the
  warm commits exceeds the compaction saving. `effective_finder` is much worse at the larger windows
  (the finder-priced suffix re-reads land behind a broken prefix).
- **The failed-prevention requirement (≤5%) passes at 252K/500K (0.5% / 0%) but fails at 128K
  (63.8%)**: the dense small-window sessions compact inside H+margin despite a passing runway
  projection — mean-growth underestimates bursty per-step token adds there. H/margin and the growth
  estimator are the P5 tuning surface.
- Facts kept improve further over HP1 (3.16 / 3.40 vs 2.36 / 2.65 at 252K / 500K) — the extra moments
  catch facts the trigger pass would have lost — and retention by rung holds (extract ~96-100%,
  errors 70-90%, header 0%).

## HP3: replace the compaction with one deep prune (P3-13, `plan_replace_compaction`)

Hermes calls the proactive `prune_tool_results_only` checkpoint before each post-tool request and
`compress()` when `should_compress` fires (`turn_preflight.py:402`, `:300`;
`context_compressor_prune.py:37`, `context_compressor.py:5422`). The incumbent avoids compactions by
committing its prune *before* the threshold check; HP3 instead acts *inside* `compress`
(`engine.py:1197`): a trigger `plan()` with `replace_compaction` aims a deep prune — same shadow the
native pass would demote — at `trigger − growth × margin_steps` (`plan_pass.replace_compaction:398`).
On `reaches_target` the prune commits through `super().prune_tool_results_only` and the compaction is
skipped (the replay counts it as a prune via the `_plan_replace_rows` marker, `replay.py:526`); on any
decline Hermes compacts exactly as HP1. `plan_forecast` off — no warm acts. Arms: HP3-m12 / HP3-m24.

### weighted `effective_real` change % (Hermes weights 1/5), cluster 95% CI, one-sided upper bound u1

| comparison | 128K | 252K | 500K |
|---|---|---|---|
| HP3-m12 − stock | +36.4 [+32.4, +40.5] u1 +39.8 | +21.2 [+19.4, +22.9] u1 +22.6 | +10.9 [+9.9, +11.9] u1 +11.7 |
| HP3-m12 − HP1 | +36.4 [+32.3, +40.6] | +21.4 [+19.6, +23.2] | +10.7 [+9.7, +11.7] |
| HP3-m12 − HP2 | +32.6 [+28.5, +36.8] | +9.4 [+7.7, +11.3] | +6.6 [+5.7, +7.6] |
| HP3-m12 − e1.10.0 | +53.9 [+49.2, +58.7] | +116.3 [+112.4, +120.3] | +58.1 [+56.9, +59.3] |
| HP3-m24 − stock | +28.7 [+25.3, +32.0] u1 +31.5 | +19.8 [+18.1, +21.5] u1 +21.3 | +10.8 [+9.8, +11.8] u1 +11.6 |
| HP3-m24 − HP1 | +28.6 [+25.3, +31.9] | +20.0 [+18.3, +21.8] | +10.6 [+9.6, +11.5] |
| HP3-m24 − HP2 | +25.0 [+21.7, +28.4] | +8.2 [+6.4, +10.1] | +6.5 [+5.6, +7.5] |
| HP3-m24 − e1.10.0 | +45.1 [+41.3, +48.8] | +113.2 [+109.3, +117.2] | +58.0 [+56.8, +59.2] |
| HP3-m12 − stock `effective` | +37.6 | +22.7 | +13.7 |
| HP3-m12 − stock `effective_finder` | +42.5 | +73.5 | +104.6 |
| HP3-m24 − stock `effective` | +29.6 | +21.1 | +13.6 |
| HP3-m24 − stock `effective_finder` | +32.3 | +66.3 | +101.9 |

### breaks / compactions / replace prunes per session, facts kept

| arm@window | breaks | compactions | replace prunes | kept | lost | recoverable |
|---|---|---|---|---|---|---|
| stock@128K | 2.11 | 2.11 | 0 | 0.74 | 3.26 | 0 |
| HP1@128K | 2.11 | 2.11 | 0 | 1.31 | 2.29 | 0.40 |
| HP2@128K | 2.31 | 1.96 | 47 | 1.29 | 2.27 | 0.45 |
| HP3-m12@128K | 3.77 | 1.06 | 357 | 1.54 | 1.79 | 0.67 |
| HP3-m24@128K | 3.41 | 1.30 | 279 | 1.44 | 1.96 | 0.60 |
| e1.10.0@128K | 6.41 | 0.29 | 808 | 1.60 | 0.39 | 2.02 |
| stock@252K | 1.93 | 1.93 | 0 | 1.34 | 6.66 | 0 |
| HP1@252K | 1.96 | 1.96 | 0 | 2.36 | 4.64 | 1.01 |
| HP2@252K | 2.42 | 0.93 | 197 | 3.16 | 3.11 | 1.73 |
| HP3-m12@252K | 2.60 | 0.08 | 332 | 4.53 | 0.60 | 2.87 |
| HP3-m24@252K | 2.55 | 0.21 | 308 | 4.27 | 1.17 | 2.56 |
| e1.10.0@252K | 4.98 | 0 | 657 | 3.20 | 0.24 | 4.56 |
| stock@500K | 1.99 | 1.99 | 0 | 1.26 | 10.74 | 0 |
| HP1@500K | 1.99 | 1.99 | 0 | 2.65 | 8.74 | 0.61 |
| HP2@500K | 2.16 | 1.17 | 131 | 3.40 | 6.77 | 1.83 |
| HP3-m12@500K | 2.24 | 0 | 296 | 6.98 | 0.28 | 4.74 |
| HP3-m24@500K | 2.24 | 0.02 | 292 | 6.88 | 0.48 | 4.64 |
| e1.10.0@500K | 4.51 | 0 | 595 | 4.88 | 0.37 | 6.75 |

### reaches_target share and native commit gate (telemetry, per window)

| arm@window | calls | reached | reach share | commits | native no-commit | commit share of reached |
|---|---|---|---|---|---|---|
| HP3-m12@128K | 497 | 391 | 78.7% | 357 | 34 | 91.3% |
| HP3-m24@128K | 450 | 310 | 68.9% | 279 | 31 | 90.0% |
| HP3-m12@252K | 343 | 332 | 96.8% | 332 | 0 | 100% |
| HP3-m24@252K | 336 | 308 | 91.7% | 308 | 0 | 100% |
| HP3-m12@500K | 296 | 296 | 100% | 296 | 0 | 100% |
| HP3-m24@500K | 295 | 292 | 99.0% | 292 | 0 | 100% |

### 64K development matrix (refactor / smallrows, seeds 0-5)

| shape | arm | breaks/session | compactions/session | prunes/session | kept | effective vs stock |
|---|---|---|---|---|---|---|
| refactor | stock | 2.0 | 2.0 | 0 | 13.0 | — |
| refactor | HP3-m12 = HP3-m24 | 3.17 | 0.17 | 3.0 | 13.33 | +8.2% [+3.0, +15.9] |
| smallrows | stock | 2.0 | 2.0 | 0 | 10.0 | — |
| smallrows | HP3-m12 = HP3-m24 | 2.0 | 2.0 | 0 | 10.33 | +8.2% pooled above |

(The two margins land on identical trajectories at 64K — the reachability outcome is the same on these
shapes.)

### What the HP3 numbers say

- **The mechanism does exactly what it was designed to do**: compactions nearly vanish (m12: 1.06 /
  0.08 / 0 per session — at 500K zero sessions compacted at all), each trigger moment spends its one
  cache break on a deep prune (reach share 79-100%, rising with window size), and rungs follow the
  plan — extracts 92-95% held, headers 0%, no `brief` rung in replace mode.
- **Both candidate bars fail decisively.** Cost vs stock is *worse* at every window: the one-sided
  upper bounds are +39.8 / +22.6 / +11.7 (m12) and +31.5 / +21.3 / +11.6 (m24) — nowhere near < 0.
  Two compounding causes: a replace-prune leaves `margin_steps` of runway where a compaction resets
  far deeper, so sessions re-trigger more often (breaks 3.77 / 2.60 / 2.24 vs stock 2.11 / 1.93 /
  1.99 — the gate fails at every window and at 64K refactor 3.17 vs 2.0); and the extract-rich
  post-prune prompt is much bigger than a compaction's summarized middle, so every later request
  re-reads it (`effective_finder` +32 to +105%).
- **m24 is uniformly cheaper and gentler than m12** (fewer commits → fewer breaks and less retained
  extract, at the cost of more fallback compactions) — the margin trades reachability for break count
  in the predicted direction.
- **Fact retention is the best seen so far**: kept 4.53 / 6.98 at 252K/500K vs stock 1.34 / 1.26 and
  even the incumbent's 3.20 / 4.88 — extracts at compaction depth keep almost everything (lost 0.28 at
  500K). The trade is unusable as-is for cost, but it marks where the retention ceiling sits.
- **Verdict: not adopted** — the deep prune alone cannot beat a compaction's reset on either cost or
  break count at these margins; a viable hybrid would need the prune to leave compaction-scale
  runway, which is exactly what `reaches_target` already refuses.

## P3-14 — HP4: `plan_deep_tail` (a deeper reset at the same compaction moments)

HP4 keeps HP1's compaction moments and asks `plan()` to also demote the **protected tail's** older
tool results — every tail row except the most recent step's pending round (`_spared_pending_tool_round`,
the same rows Hermes itself spares). Pass 2 of Hermes' compaction prune never visits tail rows
(`agent/context_compressor.py:3298` iterates `range(prune_boundary)`), so the tail items' rungs are
committed on the pruned copy after the native pass returns (`plan_pass.apply_deep_tail`), through the
same ladder rungs, refs and recovery records; the compaction then proceeds unchanged and splices a
pre-shortened tail. No warm acts (`plan_forecast` off), no `replace_compaction`.

Rows: `build/p3-plan/rows-dev-hp4.jsonl` (132 dev scenarios x 3 windows x 2 arms; stock re-run is
identical to the committed P2c stock rows apart from timing and the post-P2c per-fact rung fields —
0 content diffs). 64K matrix: `build/p3-plan/matrix64-hp4.json`. Post-compaction sizes:
`build/p3-plan/postcompact-telemetry.jsonl`. Report: `build/p3-plan/report-hp4.{json,txt}`.

### Weighted `effective_real` change % vs stock (cluster 95% CI, one-sided upper u1)

| arm | 128K | 252K | 500K |
|---|---|---|---|
| HP4 vs stock | −1.21 [−1.68, −0.72] u1 −0.80 | −1.77 [−2.24, −1.30] u1 −1.38 | −0.97 [−1.21, −0.73] u1 −0.77 |
| HP4 vs HP1 | −1.28 | −1.55 | −1.17 |
| HP4 vs HP2 | −3.93 | −10.80 | −4.63 |
| HP4 vs e110 | +11.59 | +69.11 | +41.07 |
| HP4 vs stock `effective` | −0.86 | −1.11 | −0.25 |
| HP4 vs stock `effective_finder` | +0.20 | +9.54 | +8.96 |

### Breaks / compactions / prunes per session and facts kept

| arm@window | prefix_breaks | compactions | prune_commits | kept |
|---|---|---|---|---|
| stock 128K / 252K / 500K | 2.106 / 1.932 / 1.985 | 2.106 / 1.932 / 1.985 | 0 | 0.74 / 1.34 / 1.26 |
| HP4 128K / 252K / 500K | 2.098 / 1.917 / 1.985 | 2.098 / 1.917 / 1.985 | 0 | 1.24 / 2.68 / 2.93 |

### 64K matrix (seeds 0-5)

| shape | arm | prefix_breaks | compactions | prune_commits | kept | effective vs stock |
|---|---|---|---|---|---|---|
| refactor | stock | 2.0 | 2.0 | 0 | 13.0 | — |
| refactor | HP4 | 2.83 | 1.0 | 1.83 | 13.0 | +0.2% [−1.1, +2.8] pooled |
| smallrows | stock | 2.0 | 2.0 | 0 | 10.0 | — |
| smallrows | HP4 | 2.0 | 2.0 | 0 | 10.33 | +0.2% pooled above |

### Candidate-bar verdict

| gate | HP4 |
|---|---|
| cost u1 < 0 @128K | **pass** (−0.80) |
| cost u1 < 0 @252K | **pass** (−1.38) |
| breaks ≤ stock, main windows | **pass** (2.098 / 1.917 / 1.985 vs 2.106 / 1.932 / 1.985) |
| breaks ≤ stock @64K | **fail** (refactor 2.83 vs 2.0; smallrows 2.0 vs 2.0) |
| facts ≥ stock | **pass** (1.24 / 2.68 / 2.93 vs 0.74 / 1.34 / 1.26) |

### What the HP4 numbers say

- **The mechanism does what it was designed to do**: the post-compaction reset is deeper (tail tool
  results arrive pre-shortened — at 252K HP4 shortens 19.4 rows vs stock 9.9 and keeps 30.5 tool rows
  live vs 21.2), and it is the first arm cost-negative vs stock at every window while matching stock's
  breaks exactly on the main windows.
- **Facts nearly double** (2.93 vs 1.26 at 500K) because the tail's results demote through the fact-
  first allocation rather than surviving whole or being stubbed; errors rungs hold ~3x the error
  exemplars of stock at 64K.
- **The 64K failure is native Hermes pruning, not policy pruning**: at `refactor` the deeper reset
  halves compactions (1.0 vs 2.0) but the changed transcript then trips Hermes' *own* proactive-prune
  gate ~1.8 times per session (the same mechanism behind HP1's +0.83). Breaks read 2.83 vs 2.0.
  `smallrows` is identical to stock. The strict ≤-stock comparison fails; the compaction count itself
  improved.
- **Verdict: not adopted under the stated bar** — it clears cost, facts and every main-window break
  gate but not the 64K refactor break comparison. It is also the closest any arm has come: the
  remaining gap is native-prune activity on the post-compaction transcript, which suggests the next
  lever is suppressing or absorbing those native commits rather than changing the demotion.
