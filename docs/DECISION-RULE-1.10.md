# 1.10.0: does a computed "pays back" rule beat the fixed 35% share?

**Short answer: no.** Four computed rules were built behind `decision_rule` and measured against the shipped rule (`share`) on the development replay. None passed the
promotion gate that was fixed before any of them was looked at, so `share` stays the default. The rules were removed from the product after the round-2 review (the code is in commit `5ce21ae`); only the measurement stays.

## The question

The adaptive policy accepts a prune only if it removes at least 35% as many tokens as it makes the provider rewrite into the prompt cache (`policy.py`). That share is a
hand-set stand-in for "this prune pays for its cache rewrite". Section 3 of the 1.10.0 plan replaced it with arithmetic from quantities the plugin measures
(`hermes_tameru_plugin/payback.py`, derivation in its docstring):

- the cached part of the rewrite (`rewritten`: the rows before the new assistant call; the new rows are written at full price whether or not anything is pruned),
- the cache prices `W` and `R` (settings `cache_write_multiplier`, `cache_read_multiplier`; 1.25 and 0.10),
- how many requests are left before Hermes would compact (headroom divided by the median recent growth),
- what Hermes' own compactions cost per token they keep out of the prompt (`kappa`, from what the last compaction left in the prompt).

| `decision_rule` | What it asks of a prune |
|---|---|
| `share` (default, shipped) | 35% of the whole rewritten suffix |
| `share_cached` | 35% of the cached part only (the unsent tail was never a rewrite) |
| `payback_early` | `g * (W + R*n) >= (W - R) * rewritten`: pays back over the `n` requests left |
| `payback` | the same, plus a deadline credit `kappa * g` when the threshold is one or two requests away |
| `accrual` | rent-or-buy: carry `R * g` for every check the text stays in the prompt, prune when the carried cost covers the rewrite |

## The gate (fixed in advance)

Against the shipped product, per window (64K, 128K, 200K), on paired rows (same shape, window and seed): the interval on the mean change of effective cost must exclude zero on the
cheaper side; needed facts held (kept plus recoverable) and error templates kept must not be lower with an interval below zero; and no shape may be more than 2% dearer with an interval that
excludes zero (`matrix.PROMOTION`, `python tests/eval/matrix.py promote`). Intervals are cluster bootstraps over the six development shapes (`stats.py`); seeds are the six structural seeds.

## Result (`tests/eval/results/rules-matrix.json`: 648 rows, Hermes v0.23.1, 39,993-character tool block, replay only)

Mean change of effective cost against the shipped rule (negative is cheaper), cluster 95% interval:

| Rule | 64K | 128K | 200K |
|---|---|---|---|
| `share_cached` | +0.0% [+0.0, +0.0] | +0.1% [+0.0, +0.2] | +0.0% [+0.0, +0.0] |
| `payback_early` | +0.1% [-0.9, +1.0] | +0.4% [-0.4, +1.4] | -0.0% [-0.3, +0.1] |
| `payback` | -0.4% [-1.7, +0.7] | +0.3% [-0.4, +1.3] | -0.0% [-0.3, +0.1] |
| `accrual` | +1.7% [+0.2, +3.5] | +6.7% [+3.5, +9.8] | +3.7% [+1.2, +6.4] |

- `share_cached` is a no-op: correcting the rewritten part changes no decision on these sessions.
- `payback_early` and `payback` change nothing in cost that the intervals can separate from zero, and `payback` holds fewer needed facts at 64K (-7.3%, interval below zero): it fails the gate on its guard.
- `accrual` is dearer at every window (the textbook rule waits too long) and loses facts at 64K.

Where the arithmetic does bite is the known loss, `refactor` at 64K (cost against stock Hermes; "held" is needed facts held of 16):

| | shipped `share` | `payback_early` | `payback` |
|---|---|---|---|
| cost against stock | +8.4% | +7.7% | +5.6% |
| prunes per session | 6.7 | 3.0 | 3.3 |
| host compactions | 0.0 | 0.8 | 0.7 |
| needed facts held | 16.0 | 13.5 | 14.2 |

The computed rules prune about half as often, and the saved cache rewrites are paid back by Hermes compacting instead, which costs facts. `default` at 64K shows the same trade
(`payback`: 0.9 points cheaper, 2.2 of 11 needed facts lost). **The shipped rule's higher cost on `refactor` at 64K is the price of holding all 16 facts**; a rule that is cheaper
there is cheaper because it keeps less. Whether that trade is wanted is a product choice, not something the arithmetic settles.

## What this does and does not show

- It is a replay with a fake summariser, cost in characters, six development shapes, and a prompt-cache model that is simpler than any provider's. It says the shipped share is
  within noise of the best of these rules at 64K to 200K windows. It does not say a rule that models more of the cache (breakpoints, minimum cacheable length, real idle gaps) could not do better.
- The holdouts were not looked at for this question: no rule passed the development gate, so there was nothing to confirm.
- The live check against real billing (`tests/eval/live_calibration.py`) has not been run; it needs an API key and a spending cap.
