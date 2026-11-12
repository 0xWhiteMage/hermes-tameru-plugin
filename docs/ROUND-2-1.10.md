# 1.10.0 round 2: the remaining ideas, judged by the pre-registered gate

Gate: `matrix.PROMOTION` (committed before any of these ran). Rows: `tests/eval/results/{frontier,evidence,floor}-matrix.json` against the shipped product in `honest-matrix.json`. Idea A (frontier shortening) is in [FRONTIER-1.10.md](FRONTIER-1.10.md).

| Idea | Verdict | Why |
|---|---|---|
| A. Frontier shortening | Does not pass | -0.9% on `refactor` at 64K (kill rule: under 3%); needed detail missing in the next 3 requests 3.8 to 13 per session (ceiling 0.5); cache breaks up 12% to 700% |
| B. Evidence references in the Artifact Trail (`trail_evidence`) | Does not pass | the pass rule needs "recoverable by the weak finder higher": it is 0 before and after |
| D. `lazy_tool` by default | Not implemented | the honest rows already show it dearer than the shipped product: +0.3% [+0.1, +0.6] at 64K, +0.3% [0.0, +0.5] at 128K (realistic price); fixing its two defects cannot make it cheaper |
| Lower minimum result size (2,000 to 1,000 characters) | Does not pass | `smallrows`, its target, is +8.3% dearer at 64K (interval excludes zero) and loses 4.5 needed facts at 200K |

## B in detail (64K is the only window with host compactions, so the only one that moves)

| | product | with references |
|---|---|---|
| cost, old / realistic price | | -0.4% / -0.4% [-1.0, +0.1] |
| facts kept outright | 10.00 | 9.94 |
| recoverable (exact search) | 0.78 | 1.28 |
| lost | 2.39 | 1.94 |
| cost with the weak finder | | +8.2% [+1.1, +16.1] |

References put 0.5 more facts per session within reach of a model that knows where to look, at no cost on the old or realistic price. They do nothing for the weak finder (it greps only the question's words), and that finder then pays for tries that fail, which is what the +8.2% is. Whether a real model behaves like the exact search or like the weak finder is the open question; it was not measured live. On the evidence in hand the expected value is small and could be negative, so it does not ship.

## Decision

None of the round-2 ideas is added to the release. The shipped rule and settings stay as they are; the experimental code for A and B is removed from the product (the rows and this write-up stay; the code and the replay arms that produced them are in commit `5ce21ae`).
