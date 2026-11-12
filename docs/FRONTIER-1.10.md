# 1.10.0 idea A: frontier shortening. Result: does not pass; dropped from the release

**Idea.** Shorten a large tool result one request after the model has seen it, at any context fill, instead of waiting for the activation point. Only a few cached tokens sit after it then, so the rewrite is cheap, and every later request saves the difference. Behind `frontier` (default off; `frontier_min_chars`, default 12,000).

**How it runs.** A frontier plan applies below the activation point only: the tail is the pending round by count, the retained-extract budget and room cap are not used (older rows stay as they are), Hermes' re-arm mark is opened for the pass and restored after it, the acceptance share counts the cached part only, and a frontier commit does not reset the normal pass's timing or feed the self-tuner. Hermes' own message-count gate still applies.

**Result** (`tests/eval/results/frontier-matrix.json`, 126 rows, the pre-registered gate of `matrix.PROMOTION`, against the shipped product; honest rows in `honest-matrix.json`):

| Mean change of cost, cluster 95% interval | 64K | 128K | 200K |
|---|---|---|---|
| old price | -0.4% [-1.3, +0.5] | -7.7% [-11.1, -4.0] | -18.2% [-24.0, -10.4] |
| realistic read-back price | -0.3% [-1.3, +0.6] | -7.7% [-11.1, -4.0] | -18.5% [-24.3, -10.6] |

| Per session | 64K | 128K | 200K |
|---|---|---|---|
| prune commits | 4.9 → 5.4 | 3.9 → 9.8 | 1.4 → 10.5 |
| cache breaks | 5.8 → 6.3 | 4.1 → 9.9 | 1.4 → 10.5 |
| rewritten characters | 500K → 502K | 505K → 515K | 342K → 420K |
| needed detail missing in the next 3 requests (`dependent` shape) | 3.8 | 13.0 | 7.0 |

**Why it fails.** The kill rule (net gain on `refactor` at 64K under about 3%) applies: it is -0.9%. The gate also fails on the dependent-use ceiling (0.5 per session) at every window, on cache breaks at every window, on rewritten characters at 200K, and on facts kept outright in `default` at 128K (-1.5 per session, margin 1.0).

**What it does show.** At 128K and 200K, where the shipped product waits until the context fills and so does little, shortening early is much cheaper in the replay. The saving is bought by removing a detail the next requests still wanted: in the `dependent` shape those misses are read-backs the cost does not charge (the detail facts there are not scored). A rough price, not a measurement: a read-back is an extra request of about 17K characters, so 13 misses per session is about 9% of the shape's cost at 128K, which cancels the 7.7% gain; at 200K, 7 misses is about 5% against 18.5%, which would not.

**Decision.** Not promoted, and the code is removed from the product (it is in commit `5ce21ae` with its replay arm). One variant would be worth a separate, pre-registered test: frontier only above a window size, only for results the model is unlikely to need again (for example only after a successful, unchanged read), judged on the blind shapes, which this idea has not spent.
