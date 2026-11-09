# Cost tuning against Hermes as shipped (2026-11-10)

Goal: adaptive (the default) and legacy should cost no more than Hermes as shipped. Measured with `tests/eval/test_cost_matrix.py`
(replay, cost per answered turn, cache-aware pricing; see `docs/DEVELOPMENT.md`). Gold-fact retention was never below stock in any cell.

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
