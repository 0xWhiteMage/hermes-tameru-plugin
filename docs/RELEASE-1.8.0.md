# Tameru 1.8.0: release record

> **Historical record.** Written for an earlier version; its numbers and claims are not updated. Current measurements: [COST-TUNING-2026-11-10.md](COST-TUNING-2026-11-10.md); current state: [RELEASE-1.9.0-READINESS.md](RELEASE-1.9.0-READINESS.md).

Recorded 2026-11-09. This is the honest account of what was checked for 1.8.0, what was not, and what to say in the
catalogue pull request. [CHANGELOG.md](../CHANGELOG.md) says what changed.

## Scope

- **1.8.0 is the first release since 1.6.0.** 1.7.0 was a release candidate that was never tagged or published, in
  this repository or in the engine's. Everything it contained ships here, along with later work.
- **Engine:** `tameru-compaction-system` 1.8.0 is vendored byte-for-byte. Its code is the 1.7.0 candidate plus two
  fixes made afterwards (source associations across structured compaction; YAML dependencies and Git note
  boundaries); only the version stamps changed for 1.8.0. The extraction results in
  [REPORT-1.7.0.md](REPORT-1.7.0.md) therefore still describe the engine, but that replay was **not re-run** for the
  1.8.0 additions below.
- **New in the plugin:** a clearer Desktop panel with hover hints; Restore and Keep; a per-turn note that tells the
  model what is shortened; the provider's own usage numbers; self-tuning that learns across chats; and the security
  hardening listed in the changelog.

## What was run

| Check | Result |
|---|---|
| Engine suite (`tameru-compaction-system`, Python 3.13) | 2,008 passed, 4 skipped |
| Plugin suite, stub mode (Python 3.13) | 1,816 passed, 1,134 skipped (those need a real Hermes checkout) |
| Desktop panel tests (jsdom + React) | 77 passed |
| Vendoring check (`sync_to_harness.py --check`) | in sync, version 1.8.0 |
| Packaging tests (versions agree; wheel and sdist contents; entry point; panel build number equals backend's) | passed, inside the stub suite |
| Hermes' own plugin validator (`hermes plugins validate`) on Hermes v0.23 | passed all checks, including "declared hooks match registrations" for `pre_llm_call`; one warning, the same private-core-import disclosure as before (`get_plugin_command_handler`, `_raise_if_stale_attempt`, `ContextCompressor`, `PROACTIVE_PRUNE_REARM_MODEL_CONFIG_KEY`) |
| Real Hermes v0.23 (Python 3.11), selected files: seam contract, seam fallback, ladder, error retention, idempotency | 86 of 87 seam checks, 6/6 fallback, 43/43 ladder, 12/12 error retention, 8/8 idempotency |
| Rollback script (`scripts/rollback.ps1`) | exercised on throwaway Hermes homes: plan-only changes nothing; apply restores `v1.6.0` with backups; an unknown tag is refused; a release without a Desktop panel moves the current panel aside |

**The one failing seam check** is `test_parity_helpers_are_verbatim_copies[_is_summary_stub]`. Hermes v0.23 changed
that helper (it now strips a skill-safety rule first) after the revision CI pins, so the test stub's verbatim copy
is out of date. It affects the test stub, not the plugin, and is recorded here so a Hermes bump re-copies it.

## Live validation (Hermes Desktop on Windows, local backend)

Done with the Haiku 4.5 model on a throwaway chat, with `compression.threshold` 0.5 and `protect_last_n` 20
unchanged, and never on a remote gateway.

- **The panel** loads with the schema-3 data, the table fits at the tested width, the viewer opens, and the
  Restore, Keep and Cancel controls act on a row.
- **The per-turn note reaches the model.** After a restart the model quoted the whole `<tameru_note>` block back on
  the first turn. (An earlier build showed none: the engine had no rows until the panel asked for them; fixed by
  rebuilding rows inside the note builder.)
- **Restore reaches the provider.** Hermes' own request log, in tokens:

  | Call | Prompt | Cache reused |
  |---|---|---|
  | before the Restore | 55,465 | 100% |
  | first turn after the Restore | 76,281 (+20,816) | 18% |
  | the next turn | 76,369 | 100% |

  One cache rebuild, then full reuse. The row read Pending, then Applied.
- **A Restore that crowds the chat is refused.** In an earlier run on a chat already at 95% of its window, a
  Restore pushed it to 113% and Hermes summarised the whole thread, which cannot be undone. That led to the "Memory
  nearly full" check, at click time and on every request, covered by tests. It was not re-run live at 95%.
- **Not shown:** that the model *quotes* restored text. The neighbour-line probes returned UNKNOWN and the control
  was contaminated by outputs that sit unshortened in Hermes' protected tail. The provider numbers above show the
  content was sent; unit tests show the overlay content is exact.

## What was not run

- `ruff check .` (ruff is not installed in the test environment).
- The CI matrix: Python 3.11 to 3.14 stub runs, the real-Hermes job at the pinned commit `a0a49926`, and the
  `hermes-main` nightly. The full suite against a newer Hermes could not be used as a signal here (it needs the
  network and Python 3.14); only the selected files above were run.
- The replay harness (`tests/eval`) and its baselines, and the engine's `run_battery.py` and `threshold_sweep.py`.
  The engine's code did not change for 1.8.0, so savings should not move, but that was not measured.
- Live checks of the new commands in the CLI and TUI; live refusals from a messaging platform; a live secret
  refusal; the Desktop panel at narrow widths (CSS container rules are asserted in tests but jsdom cannot run them).
- `tests/test_real_hermes_e2e.py` (it launches Hermes' own CLI bootstrap and cannot run inside this environment).

## Known limits

- Restore and Keep act only on outputs Tameru already shortened and that are still in the request: text folded into a
  Hermes summary is gone and reports "Not in this chat's history". Web content can only be re-read.
- There is no `/tameru reset` command; see [RUNBOOKS.md](RUNBOOKS.md) for the workarounds.
- The Desktop panel and backend are compared by a build number, not a file hash.
- Shorten, the category breakdown, learning from your corrections, and subagent reference passing are not in 1.8.0.
- The display name differed between the Desktop header (`ためる`) and the manifest, engine and README (`貯める`). Fixed in 1.9.0: it is `Tameru (ためる)` everywhere.

## Catalogue pull request (one per release)

Edit only `plugin-catalog/tameru.yaml` in `NousResearch/hermes-agent`:

```yaml
sha: <40-hex merge commit of this repo's main after the release commit>
version: "1.8.0"
```

Keep `name`, `subdir` and `provides_tools`. The disclosure must keep the existing points and now also say:

- it adds a short context note to the user message each turn, when something is shortened (counts and file base
  names only, framed as data), through the `pre_llm_call` hook; `manifest: off` disables it;
- it can replace a shortened tool result with its full original in the request sent to the model, on the user's
  explicit Restore or Keep, through `select_context`; saved history is never edited; web content and anything that
  looked like a credential are refused; `edits: off` disables it;
- it writes one small profile-wide file, `$HERMES_HOME/tameru/learned.json` (five numbers), and per-chat session
  metadata keys `tameru_tuner_v1` and `tameru_edits_v1`; `autotune: off` stops the learning;
- the optional telemetry log rotates at 5 MB.

Reviewers read the commit range between the old and new `sha`.
