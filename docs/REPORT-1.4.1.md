# Tameru 1.4.1 candidate: what changed and what was measured

Status: local branch `locally/finish-1.4` on top of 1.4.0 (`0a7e568`), not released, not pushed. Vendored engine:
the local 1.4.1 candidate `e099d10` (branch `locally/1.4.0-patches` of `tameru-compaction-system`; patches `0004` and
`0005` in [upstream-patches/](upstream-patches/README.md)). Every number below was measured on 2026-10-12 with
Hermes `1298c8e` unless it says otherwise. Savings are in characters.

## 1. What was open after 1.4.0 and where it stands

| Item (source) | State |
|---|---|
| A live 29.5K `git log` that quoted `<persisted-output>` was read as a Hermes spill (first live sessions) | Fixed: a spill must start with the tag. Tested against Hermes' own `_build_persisted_message`, with a subdirectory hint appended after the closing tag. |
| Two answers lost per result: saturated `rg -n` hit, pytest assertion inside a long captured log (REPORT-1.4.0 §8) | Fixed (engine `e099d10`); 14/14 answers |
| Replay regression from the first 1.4.1 cut (`tameru_1_4` gold 13/17 to 10/17) | Fixed: pass-2 reservation; 13/17 |
| `compare.py` wrote into the developer's `HERMES_HOME` | Fixed: always a throwaway home unless `TAMERU_EVAL_KEEP_HERMES_HOME=1` or `TAMERU_EVAL_HERMES_HOME=<dir>` |
| `tameru_expand` "not granted" in the live session | Not a plugin defect, see §5. Documented in the README. |
| Savings gap to the engine alone (QA "Still open") | Narrowed from 16.3 to 13.5 points; the rest is Hermes' marker, which stays (§3) |
| Not run in a live Hermes session (REPORT-1.4.0 §8) | One 12-turn session per arm (§5) |
| Short results keep only Hermes' one-liner; risk label too coarse; hex masking; diff adapter for plain-word queries; final request 1.46x stock; engine CPU | Unchanged, still open |

## 2. Lost answers and the replay regression

### Per-result QA set (`tests/eval/per_result.py`)

The harness re-creates the 13 agent outputs of REPORT-1.4.0 §5 (14 planted answers, 22 error templates). Each one is
demoted once through the real ladder.

| | answers | error templates | mean saved | lost |
|---|---|---|---|---|
| 1.4.0 (`0a7e568`) | 12/14 | 22/22 | 87.2% | `rg -n` definition line; pytest `E   AssertionError` line |
| 1.4.1 candidate | **14/14** | 22/22 | 86.2% | none |

- `rg -n`: brief (90.3% saved, answer lost) became extract (84.9% saved, answer kept).
- pytest failure: brief (82.5%) became extract (74.6%).

Two engine changes were needed for `rg`, besides the saturation mode:

- The query is the call's own command (`rg -n StockReservationConflict`). `-n` was its only "strong" selector, so
  the saturation mode never ran. CLI flags are no longer selectors.
- With Hermes' marker, 25 fully omitted files cost 25 × ~100 chars. One marker now covers a run of such files.

### Replay (`tests/eval/compare.py`, 30 turns, `tameru_1_4` arms)

| | equal gates: gold | equal gates: cost | product: gold | product: cost |
|---|---|---|---|---|
| stock | 10/17 | 1,273,007 | | |
| 1.4.0 | 13/17 | 1,019,153 | 12/17 | 986,072 |
| first 1.4.1 cut (`3453d3a`) | **10/17** | 1,011,639 | 12/17 | 990,129 |
| 1.4.1 candidate | 13/17 | 1,019,153 | 12/17 | 990,003 (+0.4%) |

Error fingerprints are 7/7 in every plugin arm. The final request is 123,512 chars, against 84,080 for stock.

Cause: with the captured log collapsed, an old pytest failure row takes the extract rung (~3.3k) instead of a
1.2k brief. Hermes visits pass 2 oldest first and the retained-extract budget is shared, so that one extract used
the room the newer rows needed, and they fell to headers.

Fix: an extract in pass 2 may only use the room left after reserving `brief_chars` for each later row the ladder
could still render. The equal-gates arm is byte-for-byte back to its 1.4.0 numbers. The product arm costs 0.4% more
than 1.4.0 at the same gold, and its baseline is re-recorded.

## 3. Savings against the engine alone

Engine corpus through the plugin (`per_result.py`, second table). Each document is a Hermes terminal result asked
about by its own question. Gold is kept in every case and no distractor survives, before and after.

| case | 1.4.0 | + short-gap inlining | + no bare tool name (now) | engine alone |
|---|---|---|---|---|
| travis_yaml | 13.5% | 23.4% | 23.4% | 64.1% |
| jp_musubi | 26.5% | 29.4% | 29.4% | 46.1% |
| git_log | 69.1% | 69.1% | 87.6% | 93.9% |
| mean (12 cases) | 64.7% | 65.9% | **67.5%** | 81.0% |

Decomposition (engine alone, one input changed at a time):

- **Hermes' ~100-char marker alone:** travis 64.1% to 40.9% (47.9% with inlining), jp_musubi 46.1% to 28.6%
  (31.5%), git_log 93.9% to 89.4%, ar_needle 89.0% to 72.9%. Hermes owns the marker, and the plugin keeps it so
  Hermes and other tools recognize an elision.
- **The bare tool name in the query** (`terminal`) took git_log from 93.9% to 81.0%. It is now dropped.
- **travis_yaml's other query part** is the test harness' neighbouring call (`echo filler 0`), which matches the
  file's `echo` lines: 42.2% with that part alone. This comes from the fixture, not from what a real session asks.
- **Row headers** (Hermes' one-line summary plus the tameru tag) cost roughly 150-250 chars per row; in the live run a brief kept 972 of its 1,189 chars as content.

Byte stability: rendering is still a pure function of the row and the pass, and the replay reports 0 rows
rewritten between commits.

## 4. Test suites (before: 1.4.0 `0a7e568`, WIP: `3453d3a`, after: branch head)

| suite | 1.4.0 | WIP | 1.4.1 candidate |
|---|---|---|---|
| plugin, stub | 1076 passed, 268 skipped | 1081 passed, 268 skipped | 1085 passed, 269 skipped |
| plugin, real Hermes `1298c8e` | 1339 passed, 5 skipped | 1342 passed, **2 failed** (replay baselines) | 1349 passed, 5 skipped |
| engine pytest | `a541e92`: 1124 passed, 1 failed (cache test on Python 3.14), 3 skipped | `173a948`: 1151 passed, 3 skipped | `e099d10`: 1159 passed, 3 skipped |
| engine `run_battery.py` | 15/15 | 15/15 | 15/15, savings identical |
| engine `threshold_sweep.py` | 77.3 / 68.4 / 61.2 / 41.5 / 24.9% | same | same |

Against the Hermes build used live (`b78931e`, 1,181 commits older than `1298c8e`), the real-Hermes suite gives 1319
passed and 30 failed. All 30 failures are parity tests for Hermes helpers that build does not have
(`_summarize_refused_tool_result`, the failure-suffix helper), where the plugin uses its own fallback. The seams the
plugin hooks are present.

## 5. Live Hermes session

Setup:

- Hermes 2026.9.24 (`b78931e`), the build installed on the Windows test machine, run through its own managed
  Python environment.
- Model: "Qwen3.8 27B Kearuga" (local, OpenAI-compatible), `context_length` 65,536,
  `proactive_prune_tokens` 12,000.
- A throwaway `HERMES_HOME` per arm. The plugin is installed as a user plugin in the tameru arm.
- Toolsets `terminal,file,context_engine`. The first live sessions used `-t terminal,file`, which hides
  `tameru_expand`.
- The agent works in a clone of this repo at `0a7e568`. Each arm runs 12 turns: the 8 of the first live run plus 4
  larger ones (`git log -p`, an `rg` over the package, a CHANGELOG read, and a recovery question that invites
  `tameru_expand`). The last turn asks for 5 facts from earlier steps without tools.

| | stock | tameru |
|---|---|---|
| turns answered correctly | 12/12 | 12/12 |
| final recall (5 facts) | 5/5 | 5/5 |
| API calls | 23 | 23 |
| prompt tokens (input + cache read) | 559,425 | 473,058 |
| rows demoted | 2 (Hermes one-liners, 65 and 126 chars) | 2 (briefs: 2,211 to 1,189 and 11,734 to 1,208 chars) |
| `tameru_expand` offered / used | n/a | offered / not used: the model found the step-4 output intact |

What this shows, and what it does not:

- The plugin loads and renders on a live Hermes. The two demoted rows kept 7 of 15 and 9 of 238 lines where stock
  kept one line, and the agent answered every question.
- The persisted-output false positive did not recur. The 29.5K `git log` that triggered it in the first live
  session was not demoted in this run, though. That fix is shown by the unit tests, not by this session.
- The 15% fewer prompt tokens are **not** attributable to the plugin. The two agents took different paths: stock
  read all of REPORT-1.4.0.md (19 KB) where the tameru agent searched and read 80 lines, and the `rg` outputs
  differ in size. One session per arm, one model, no repeats.
- "not granted": Hermes builds with the capability audit log
  `capability_check ... capability=tools.override decision=deny evidence=not granted` at every plugin load
  (`plugins_loader._track_tool_override_policy` probes the policy with an empty tool name). It is not about
  `tameru_expand`, which is a context-engine tool. Its absence in the first live session came from `-t terminal,file`.
  Build `b78931e` has no such audit and logged nothing.

## 6. Risks and limits

- **Reservation is a heuristic:** it reserves `brief_chars` (1,200) per later row, but real briefs grow until they
  hold every error template (1.8k in the unit test). It can over- or under-reserve. Tests pin the replay and a
  five-row case.
- **The query change** affects every extract (the tool name is no longer a query part). The corpus, the replay and
  the QA set show no loss. A tool whose name is also the subject of the question (for example a result about
  `terminal` settings) now relies on the user's words for that term.
- **The vendored engine is not upstream.** `VENDORED_FROM` names a local commit (`e099d10`).
- **Evidence:** the per-result set and corpus are synthetic. The replay uses a fake summarizer, and the live session
  is a single run per arm on an older Hermes build.

## Reproduce

```bash
python3 -m pytest -q -p no:cacheprovider                                        # stub
HERMES_REPO_ROOT=<hermes@1298c8e> python3.14 -m pytest -q -p no:cacheprovider   # real Hermes
HERMES_REPO_ROOT=<hermes@1298c8e> python3.14 tests/eval/compare.py              # §2 replay table
HERMES_REPO_ROOT=<hermes@1298c8e> python3.14 tests/eval/per_result.py           # §2 and §3 tables
```
