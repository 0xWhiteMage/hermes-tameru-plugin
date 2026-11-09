# Repo audit, 2026-11-09 (Ponytail audit method)

Method: the `ponytail-audit` procedure (DietrichGebert/ponytail, MIT), applied by hand to both repositories. Nothing from
Ponytail was installed or run: its skills are plain instructions, so only the method was used. Reading order was README,
build config, entry points, then the paths where a mistake costs most (the summary ledger, Keep/Restore, pins, host-settings
tuning, the panel, the install scripts). Checks were run with a small AST script (unused imports, definitions nobody calls,
long functions, duplicate bodies), a diff of the vendored engine copy, timing measurements, and the full test suites.

**What this repo does:** the plugin shortens old tool output in a Hermes chat, keeps the originals recoverable, and shows
what it did in a Desktop panel. The engine repository holds the shortening code; the plugin vendors it. Assumed load: one
person with a few long chats at a time (a local desktop), not a shared server.

## Must fix (all three are fixed in this commit)

1. **Ordinary commands were hidden as "secrets", and ordinary errors were dropped** (`ledger.py`, the two filters added this week)
   - **What this is:** the summary keeps a list of commands and error lines. One filter hides anything that looks like a
     password; another drops lines that read like orders to the AI.
   - **Problem:** `ssh -p2222 host` and `psql -p5432 db` were hidden as passwords (a port looks like `-pPASSWORD`). `MAX_TOKENS=4096`
     and `--token-limit 5` were hidden too. Error lines such as "next step failed", "tool call failed" and anything naming the
     `tameru` plugin (including its own load errors) were dropped, so the summary lost real failures.
   - **Fix:** the filters now need a real secret shape (a value after a secret flag, a name that ends in TOKEN/SECRET/PASSWORD,
     `-p` followed by more than a port number) and a real order shape ("Tameru verified:", "ignore all previous instructions",
     "you must curl ...").
   - **If we skip it:** the model loses the exact command or error it needs after a summary.
2. **A backup folder broke the Desktop panel** (`scripts/use_display_name.ps1`, `scripts/rollback.ps1`)
   - **What this is:** the scripts keep a copy of the old plugin before replacing it.
   - **Problem:** the copy was left inside `plugins/`. Hermes copied its panel into `desktop-plugins/` too, and Desktop loads every
     folder there, hidden or not. Two panels with the same id: Desktop kept the old one and refused the new one, so new panel
     features never appeared.
   - **Fix:** backups go to `plugin-backups/`, outside every scanned folder; docs and a test now say and check this.
   - **If we skip it:** every upgrade or rollback can silently leave the old panel running.
3. **Rollback restored the panel into the wrong folder** (`scripts/rollback.ps1`)
   - **Problem:** after the folder rename, the panel lives in `desktop-plugins/tameru_(...)`, but rollback looked for
     `desktop-plugins/tameru`, found nothing and left the new panel beside an old backend.
   - **Fix:** it follows the installed folder name. **If we skip it:** a rolled-back chat shows errors from a panel that is too new.

## Should fix (status after the clean-up pass)

4. **Done.** **Dead code:** `confirm_edits_sent` (`engine.py`) had no caller left after the "Applied only after the provider accepts" change.
   Deleted.
5. **Done in part.** **One 1,353-line function** (`compress_context`, `src/tameru/compress_context.py:6305`), plus `score_blocks` (496 lines),
   `detect_test_runner` (277), `preprocess_logs` (252), `adapt_grep_lines` (211), `brief_context` (211). They do several unrelated
   jobs each, which makes them hard to read and to test. Split by job, not by length, and only with the benchmark suite running
   after every step. Four of its steps are now their own functions, moved verbatim (`_resolve_objective`, `_normalise_options`,
   `_build_receipt`, `_write_audit_log`), and `compress_context` is 1,149 lines. Each move was checked against 5,616
   input, query and option combinations captured from the code before the move, plus the engine's 2,000 tests; that capture
   is now a permanent test in the engine repository (`tests/test_golden_compress_context.py`). The rest of the function is
   branches that return early and share many local variables, so splitting it further needs a redesign rather than a move;
   `score_blocks` (496 lines) and the other long functions are untouched.
6. **Done in part.** **`engine.py` was 2,231 lines** and keeps growing: this release added Keep/Restore, notes, host tuning, pins and usage numbers to
   the same class. Each is a separate job with its own state. They now live in three mixin modules (`engine_edits.py` for Keep, Restore and pins, `engine_host.py` for host-settings
   tuning, `engine_usage.py` for usage numbers), moved verbatim, so `engine.py` is down to about 1,900 lines. The remaining
   size is the ladder, the lean tail and the pass bookkeeping; splitting those is the same kind of job as finding 5.
7. **Done for the new code.** **About 85 `except ...: pass` blocks** in the plugin (22 in `engine.py`, 11 in `recovery.py`, 10 in `config.py`). Most are
   deliberate (a hint must never break a chat turn), but a failure is then invisible. Count each swallowed error in telemetry
   (a number only) so `/tameru` can show when something is failing quietly. The Keep/Restore, pin, tuning and usage code now
   does (`quiet.swallowed`, shown as "errors ignored" in the Health line); the older blocks in `recovery.py`, `config.py` and
   the rest of `engine.py` are unchanged.
8. **Not changed: moving the files out of the tree would not shrink what gets pushed.** **The plugin repo tracks about 3,900 files and over 100 MB of replay captures** under `docs/adaptive-*` (five folders of about 20 MB
   each). They back the release reports. Attach them to a release (or move them to a separate data repository) and keep only
   the summaries in git. None of this has been pushed yet, but these files are already in the local history, so only rewriting
   that history (squashing the release commits) before the first push would keep them out of the remote.
9. **Done.** **A missing Hermes helper is looked up again on every call** (`hermes_compat._module` caches nothing on failure, on purpose).
   On a Hermes version without a helper this made the per-turn file scan take 4.0 s for 10,000 messages; on current Hermes the
   same scan takes 116 ms. A failed lookup is now remembered for five seconds.

## Nice to have

10. **Done.** The engine repository's working copy has Windows line endings in `folding.py` and `unicode_profile.py` while git stores
    them with Unix endings, so a byte-for-byte sync run from that checkout reports drift that is not real. The two files were normalised.
11. **Not changed.** The counters in the `/tameru` Health line (`secret refusals`, `order-like lines left out`, `edits not confirmed`) are
    process-wide numbers shared by every chat and profile. They are integers only, so nothing leaks, but they do not belong to
    one chat. Making them per-chat means threading the engine through the modules that bump them; the numbers are only
    integers, so it was left as it is.

Eleven findings in all; nothing else was left out.

## Checked and fine

No unused imports, no duplicated function bodies across the two source trees (only the intended vendored copy), no unused
JavaScript names, no CSS class without a use (the `tameru-edit-*` and `tameru-watching` classes are built from state at run
time), no build output or caches tracked in git, and the vendored engine matches the engine repository byte for byte apart from
the package marker and line endings. The plugin suite passes (stub and real Hermes), the engine suite passes, and the panel
tests pass.

**Verdict:** healthy after fixes 1 to 3; the next worthwhile cut is splitting `compress_context` and the big `engine.py`.
**Lean:** about -30 lines now (the dead method and two filter patterns simplified); -~3,900 tracked files possible by moving the replay captures out.
**Not checked:** the engine's behaviour on formats it has no benchmark for, security of the Hermes host itself, and the
third-party Ponytail repository's own hooks and scripts (not installed, not reviewed).
