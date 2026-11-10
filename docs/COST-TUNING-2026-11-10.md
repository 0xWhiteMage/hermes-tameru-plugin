# Cost tuning against Hermes as shipped (2026-11-10)

Goal: adaptive (the default) and legacy should cost no more than Hermes as shipped. Measured with `tests/eval/test_cost_matrix.py`
(replay, cost per answered turn, cache-aware pricing; see `docs/DEVELOPMENT.md`). Gold-fact retention was never below stock in any cell up to a 128K window; at 200K the plugin keeps 1-2 fewer facts than stock in three scenarios (default, refactor, i18n), because it prunes before any compaction is needed (see the Phase 0 section at the end).

## What changed
1. One tool is listed (`tameru_read_original`); the old name is still answered. Saves one copy of the schema per request.
2. The tool description and parameter text are shorter (1,627 to about 1,300 characters). Trigger sentence, credential note and ref format kept.
3. A full adaptive compaction prunes results from 1,000 characters (it used 8,000), so the summarizer reads stubs. This was the main loss:
   the summarizer prompt was about 3.4 times Hermes' in smallrows. A floor of 200 lost a recall fact in the oncall gate; 2,500+ brings the loss back.
4. The balanced preference's detail is 0.50 (was 0.67).

## Results (cost vs Hermes as shipped; negative = cheaper)
| Cell | Adaptive, before | Adaptive, after (seed 0 / mean of seeds 1-2) | Legacy, after (seeds 1-2) |
|---|---|---|---|
| smallrows 32K | +30.9% | +7.5% / +9.7% | +8.9% |
| smallrows 48K | +21.8% | +3.9% / +4.2% | +4.5% |
| smallrows 64K | +15.4% | +1.4% / +2.7% | +2.6% |
| refactor 48K | +5.1% | +4.4% / -1.1% | +0.5% |
| i18n 48K | -0.8% | -1.8% / 0.0% | +3.3% |
| oncall 32K / 48K / 64K | -30% / -30% / -14% | -34% / -39% / -19% | -34% / -39% / -14% |

Cells that never reach a compaction (held-out dense, diagnostics, longform, mixedlang, rereads) cost +3.5% to +5.4%: the tool schema is sent
with every request and nothing is saved to repay it. Removing that cost means dropping the recovery tool.

## Known remaining losses
- smallrows (every result is below the prune floor): a sixth compaction (81K more invalidated characters) and larger summaries from the artifact trail.
  Lowering the trail caps to 600/700 characters gains 1.5 to 2.5 points but drops i18n 32K retention below stock, so the caps stay.
- Legacy is not separately tuned; it shares the schema savings and loses where adaptive loses.

## Quality trade-off
oncall retains fewer gold facts than before (12 of 20 at 64K, was 17; 9 at 32K, was 13), still above stock (8 and 4). The default scenario at 32K retains more (11, was 8).

## Live check (Hermes Desktop, This device, Haiku 4.5, 2026-11-10)
A chat ran seven commands producing 500 lines each; Tameru shortened old outputs (986 to 10 lines, about 65% saved). Asked for record 250 of batch 2,
Haiku said it needed `tameru_read_original`, called it with the ref and returned the exact line; Tameru then logged "Shortened outputs were re-read, so more detail is kept".
One run, one model: it shows the shortened tool text still triggers recovery, not a rate.

## Method notes
Single-seed cells move 2 to 3 points between runs of the same code; run `TAMERU_COST_SEEDS=0,1,2` before trusting a difference under 3 points.

## Tried and rejected (so nobody repeats them)
- Adaptive activation (0.60 or 0.75) and minimum reclaim share (0.35 or 0.60): no change in the worst cells.
- Full-compaction prune floor 200: lost a recall fact in the oncall gate. 2,500 and 4,000: the smallrows loss comes back (+30%).
- Skipping the pre-prune before the native summary: about as cheap as the 1,000 floor but loses the tail demotion; the floor was kept.
- Lowering the artifact-trail caps to 600/700 characters: gains 1.5 to 2.5 points, drops i18n 32K retention below stock.
- Legacy with the ledger off: 1 to 2 points cheaper, but error-template retention falls (smallrows 32K: 14 to 2). Not worth it.
- Legacy with a "the saving must repay 35% of the rewritten suffix" gate on proactive prunes (adaptive's rule): better at 32K (i18n 22.8k to 22.6k,
  refactor 32.6k to 32.3k) but worse at 48K (i18n 29.3k to 29.7k, refactor 43.4k to 43.8k). Not net positive, so not shipped. Legacy stays the plain fallback.

## Reference arm: plain observation masking
`engines.plain_masking` replaces every tool result older than the protected tail with a fixed placeholder (no summary, no recovery ref, no ledger).
Cost against Hermes as shipped (seed 0): default 32K +46%, oncall 32K +63%, refactor 32K +23%, i18n 32K +28%; it is cheaper only in smallrows (-2.5% at 32K, -6.8% at 64K),
where it also keeps fewer error templates at 64K (8 against Tameru's 23). `tests/eval/test_masking_reference.py` gates that Tameru keeps at least as many gold facts
as masking in every development scenario at 32K.

## Six-seed release gate (seeds 0-5, 11 scenarios, windows 32K-128K, six arms)

Run with `TAMERU_COST_SEEDS=0,1,2,3,4,5` over `stock_defaults`, `plain_masking`, `tameru_1_4_product` (adaptive), `tameru_legacy`, `tameru_1_9_host_off` and `tameru_1_9_host_on` (1,584 cells). It measures the replay only, not model accuracy.

- **Gold facts kept** (the six compacting scenarios, all windows and seeds): stock 1,088, plain masking 1,189, adaptive 1,637, legacy 1,594, host off 1,637, host on 1,626. Adaptive and legacy are never below stock in any cell; no arm fails a turn.
- **Cost:** adaptive beats stock in the compacting scenarios by 1% (refactor 48K: +0.2%) up to 62% (bursty 32K). It loses 3.2-5.1% in the five never-compacting scenarios (dense, diagnostics, longform, mixedlang, rereads: the tool schema) and 0.5-9.7% on smallrows. Legacy matches adaptive there and is worse in i18n 48K (+3.3%), refactor 48K (+0.9%) and bursty 64K.
- **Plain masking** costs 5-63% more than stock in most compacting cells and keeps fewer gold facts than adaptive in every one.
- **Host tuning (the plan's gate "host on is at least host off") failed at 128K, found and fixed:** host on kept 11 fewer gold facts than off in 12 of 66 cell-seeds and cost 2-6% more in default and refactor. An A/B showed the protected-tail half was inert and the threshold half was the cause: Tameru's own prune trigger and budgets were sized from Hermes' tuned threshold, so raising Hermes' summary threshold also delayed Tameru's pruning. Tameru's policy now uses the threshold the user configured (`_policy_threshold_tokens`, remembered while untuned); host tuning only moves when Hermes summarises. Re-run at 64K and 128K, seeds 0-5, all scenarios: host on equals host off in every cell (no cost difference over 0.05%, gold delta 0). Regression test: `test_tameru_prunes_from_the_configured_threshold_not_the_tuned_one`.
- **Probe types:** `tests/eval/scenarios_probes.py` adds a 24-turn `directives` scenario (not in the registry, so the saved baselines are untouched; `test_probe_scenario.py` registers it for one run) with three of the plan's four probe types: a standing user instruction (I1, I2), a rejected theory (R1, R2) and a superseded setting (S1, S2). At 32K and 48K, seeds 0-1, stock Hermes (three or four compactions) loses I2 and S2; adaptive and legacy keep all six in every cell. The fourth type is `test_every_ref_in_the_final_request_resolves` in the same file: after compaction in the 32K replay, every `ref=` left in the final request is read back through `tameru_read_original`.

## Phase 0 measurement (2026-11-10, commit after `7cce52c`): the product as shipped, five windows, six seeds

1,980 replays: 11 scenarios x 32K/48K/64K/128K/200K x seeds 0-5 x six arms. Means over scenarios and seeds, cost against the named reference.

| Comparison | 32K | 48K | 64K | 128K | 200K |
|---|---|---|---|---|---|
| Product vs stock | -7.4% | -2.7% | -0.8% | -5.7% | -4.0% |
| Host tuning on vs off | -0.6% | 0.0% | 0.0% | 0.0% | 0.0% |
| As shipped (host step and per-turn note run) vs product | +0.9% | +1.0% | +0.4% | +0.2% | 0.0% |
| Autotune off vs on | -0.7% | -1.4% | -0.2% | 0.0% | 0.0% |

- **Host tuning does nothing except at 32K**, where it helps in three scenarios only (default -4.5%, oncall -1.3%, refactor -0.4%) and loses no fact. Keeping it off by default costs that 0.6%.
- **The per-turn note costs 0.2-1.0% on average and up to 6.8% in one cell** (it is stored in the user message, so every later request carries it), and at 32K/48K the as-shipped arm keeps 6 and 12 fewer gold facts than the product arm across all cells. The note was unpriced before.
- **Autotune off is cheaper at 32K/48K/64K and keeps more facts** (+11, +5, +4 across cells), so self-tuning is not shown to help.
- Raw rows: `phase0-matrix.json` (kept outside the repository; regenerate with the command in `tests/eval/test_cost_matrix.py`).

## Effective cost: pricing the facts an arm lost and the read-backs it offers (`tests/eval/protocol3.py`)

The saved baselines count a needed fact as kept only if its text is in the final request, and charge nothing for a fact an arm
lost or for the tool call that gets one back. `protocol3.measure` leaves them alone and measures one run a second way. A needed
fact is **kept** (in the request), **recoverable** (not in the request, but a `tameru_read_original` call on a ref that is in the request
returns it, which the measurement really performs) or **lost**. A recoverable fact costs a read-back and a lost fact planted by a tool
costs a re-run of that call, both priced as 1.25 x the returned characters plus 300 characters of call overhead, once, uncached. That is
a model of the extra turn, not a measurement: it ignores output tokens, latency and whether the model would actually make the call.
A lost fact the user or assistant said cannot be re-run and is counted separately, not priced.

330 runs: 11 scenarios x 32K/48K/64K/128K/200K x seeds 0-1 x stock, product, legacy.

| Needed facts, all cells | Kept | Recoverable | Lost | of which cannot be re-run |
|---|---:|---:|---:|---:|
| Stock Hermes | 1,345 | 0 | 355 | 91 |
| Product (adaptive) | 1,511 | 48 | 141 | 30 |
| Legacy | 1,516 | 26 | 158 | 29 |

| Mean cost against stock | 32K | 48K | 64K | 128K | 200K |
|---|---:|---:|---:|---:|---:|
| Product, as measured so far | -7.5% | -2.7% | -0.8% | -5.8% | -4.0% |
| Product, effective | -8.4% | -5.0% | -2.4% | -7.3% | -4.3% |
| Legacy, as measured so far | -8.2% | -2.8% | 0.0% | -4.1% | +0.1% |
| Legacy, effective | -9.1% | -4.3% | -0.9% | -5.8% | -0.6% |

- **The 200K fact losses are not real costs.** Where the product keeps fewer facts than stock at 200K: `i18n` and `refactor` are two recoverable
  facts each (effective cost -15.9% and -8.9%, the same as the measured -16.0% and -9.0%); `default` loses one tool-planted fact (effective -14.5%,
  measured -17.9%). The recovery tool is worth what it costs there.
- **Pricing the losses widens the product's lead at every window.** Stock loses 2.5 times as many needed facts.
- **What stays above stock, even priced:** the five scenarios that never compact (+3.2% to +5.1% at every window: the tool schema) and `smallrows`
  (effective +10.5% at 32K, +4.0% at 48K, +1.4% at 64K; it loses 12 facts against stock's 13 at 32K and 48K, so the loss is real cost, not a measuring gap).
  Legacy no longer beats the product anywhere once priced, except 32K by 0.7 points.
- **Not yet in this table:** the per-turn note, idle gaps and a cache lifetime (the replay now accepts `cache_ttl_s` and `idle_seconds`, off by default),
  a realistic stock tool block, and a second summarizer. Those are the rest of the second protocol.
