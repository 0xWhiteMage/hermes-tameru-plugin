# 1.10.0: round 1 re-scored under the honest price

Step 0 of round 2 changed how the replay is scored, then re-ran every round-1 arm (`tests/eval/results/honest-matrix.json`: 1,008 rows, six development shapes plus the new `dependent` shape, Hermes v0.23.1, replay only).

- `effective`: the old price (a read-back or re-run is a small charge).
- `effective_real`: a read-back or re-run is an extra request: the whole prompt is read from the cache again, plus the returned text and the call.
- `effective_finder`: as `effective_real`, but for a model that does not know the answer. It greps the question's words in the refs whose header names the right tool; when that fails it re-runs the command.

| Product against stock | 64K | 128K | 200K |
|---|---|---|---|
| `effective` | -2.9% | -15.1% | -15.4% |
| `effective_real` | -4.2% | -16.8% | -15.5% |
| `effective_finder` | +3.9% | -9.2% | -7.7% |

Against the shipped product, no arm passes the round-2 gate. At 64K (`effective_real`): `payback` -0.1% [-1.4, +1.0], `payback_early` +0.3%, `share_cached` 0.0%, `lazy` +0.3% [+0.1, +0.6], `accrual` +2.0% (+6.8% at 128K), self-tuning off -1.2% [-2.5, -0.2].

The weak finder found none of the roughly 0.8 recoverable facts per session. Read: at small windows "recoverable" overstates what a model that does not already know the answer can get back. The finder was not tuned after this result. The `dependent` shape scores 0 missing details for every arm because nothing shortens an output within three requests yet.
