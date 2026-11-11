# Real-Hermes check of 1.9.0 (2026-11-09)

> **Historical record.** Written for an earlier version; its numbers and claims are not updated. Current measurements: [COST-TUNING-2026-11-10.md](COST-TUNING-2026-11-10.md); current state: [RELEASE-1.9.0-READINESS.md](RELEASE-1.9.0-READINESS.md).

The plugin's own tests run against a stub of Hermes by default; the checks marked `real_hermes` (and the whole of
`tests/eval`) run only against a real checkout (`HERMES_REPO_ROOT`). Until today none of the 1.7 to 1.9 work had been
run against real Hermes, and a test run against a real install could not be done safely. This note records what the
first full real-Hermes run found.

Environment: Hermes v0.23.0 (the user's Windows install), Python 3.11, `HERMES_DISABLE_LAZY_INSTALLS=1`.

## What was blocking the run, and is fixed

Importing real Hermes' bootstrap from a test made it "finish a source update": install dependencies in the Hermes data
folder and relaunch the process. That broke 25 tests with `RelaunchExit` and, on the first attempts, did start an install
in the user's Hermes data root. `tests/conftest.py` now sets `HERMES_DISABLE_LAZY_INSTALLS=1` for every run; the same
tests then pass in seconds and nothing outside the temp folders is touched.

## Defects in the test setup that this exposed (fixed)

| What | Why it failed | Fix |
|---|---|---|
| "two replays of one engine are identical" failed for the plugin | the first run's cross-chat learning (`learned.json`) leaked into the second run | each replay now runs with an empty learned-prior folder |
| three strict expected-loss entries | the checks now pass on this Hermes (XPASS strict) | entries deleted, as the file's own rule says |
| policy-migration test expected `legacy` | adaptive has been the default everywhere since 1.7 | expectations updated, explicit `legacy` case added |
| expand/recovery/activity tests | written for the legacy fixed prune gates; adaptive (the default) triggers differently | they opt in to `policy_mode: legacy` |
| tests that count or fail `archive_and_compact` | current Hermes first tries an in-place rewrite of pruned rows and falls back to `archive_and_compact` only when that returns None | `use_archive_commit` forces the fallback path in those tests |
| environment-scrub test | the suite now sets the install guard on purpose | the one extra variable is allowed |

## Result

After the fixes above, the whole suite passes on real Hermes v0.23: 3,021 passed (the 20 end-to-end tests included),
0 failed, 4 skipped, and 59 strict expected-loss entries that document known losses with measured numbers. The stub
suite passes (1,923 passed, 1,161 skipped), the Desktop panel tests pass (83), and the engine repository's own suite
passes (2,008 passed, 4 skipped).

Further changes made to get there:

- The saved evaluation baselines were re-recorded on this Hermes (they are exact snapshots, and even the stock-Hermes ones
  differed). Anyone using a different Hermes version needs to re-record them there with `TAMERU_WRITE_BASELINES=1`.
- Expected-loss entries that now pass were deleted; the real losses were added with their numbers (below).
- `test_adaptive`: the lifecycle test now waits one clock tick (Windows' clock ticks about every 15 ms, so a reset and the
  observation before it could share a timestamp); the stage-retention test uses an extract budget that can hold a
  592-character marker line (400 never could).
- The stub's copy of `_is_summary_stub` was re-copied from Hermes v0.23, which now strips its skill safety rule first.

## Where the small-window cost comes from

Cost per answered turn at 32K (lower is better), same Hermes, seed 0; compactions in brackets:

| Scenario | Hermes as shipped | plugin, adaptive (the default) | plugin, `policy_mode: legacy` |
|---|---|---|---|
| refactor | 34,218 (5) | 35,343 (1), +3.3% | 34,453 (2), +0.7% |
| i18n | 24,579 (4) | 25,758 (2), +4.8% | 23,255 (2), -5.4% |
| default | 31,277 (5) | 30,468 (2), -2.6% | 27,672 (1), -11.5% |

Legacy is the cheaper policy on all three at 32K; adaptive trades some small-window cost for its features (per-chat
tuning, Keep and Restore, pins) and wins only on `default`. The acceptance failures follow from the adaptive default,
not from the 1.9.0 work. Whether to change the default for small windows is a product decision; nothing was changed.

## Host-settings tuning and the replay

`tests/eval/test_host_tuning_replay.py` runs the plugin with the per-turn host-settings step on and off. On the replay's
64K window Hermes' own floor makes `compression.threshold` irrelevant (the trigger is 85% of the window either way), so
the arms produce identical cost and compactions and the test guards side effects only. Hermes multiplies
`threshold_percent` by the effective input window (the context less the reply reserve), which is the size the tuner's
percentages are shares of (guarded by a real-Hermes test). Evidence that the tuning helps needs a window of 128K or more
and a long live chat.
