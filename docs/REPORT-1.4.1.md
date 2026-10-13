# Tameru 1.4.1 candidate: what changed and what was measured

Status: on `main` (developed on branch `tameru-1.4.1`) on top of 1.4.0 (`0a7e568`), not released. Vendored engine:
the 1.4.1 candidate `e099d10` (now on `main` of `tameru-compaction-system`; patches `0004` and
`0005` in [upstream-patches/](upstream-patches/README.md)); after the red-team round (§7) `a1e8a7f` (patch `0006`). Every number below was measured on 2026-10-12 with
Hermes `1298c8e` unless it says otherwise. Savings are in characters.

## 1. What was open after 1.4.0 and where it stands

| Item (source) | State |
|---|---|
| A live 29.5K `git log` that quoted `<persisted-output>` was read as a Hermes spill (first live sessions) | Fixed: a spill must start with the tag (or, since the red-team round, with Hermes' untrusted-data wrapper header and then the tag). Tested against Hermes' own `_build_persisted_message`, with a subdirectory hint appended after the closing tag, and with `make_tool_result_message` for wrapped tools. |
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
  file's `echo` lines: engine alone with the question plus that part saves 42.2% (corrected in the red-team round: an
  earlier wording said "that part alone", which measures 58.6%). This comes from the fixture, not from what a real session asks.
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
  `terminal` settings) now relies on the user's words for that term. A `/compress <topic>` focus equal to the tool name
  is kept (red-team fix, §7).
- **The vendored engine is not a tagged release.** `VENDORED_FROM` names an untagged commit on the engine's `main` (now `91f00e0`).
- **Evidence:** the per-result set and corpus are synthetic. The replay uses a fake summarizer, and the live session
  is a single run per arm on an older Hermes build.

## 7. Red-team round

An adversarial review of everything since 1.4.0, done on a Linux dev machine only (no live model). Every
number here was measured on 2026-10-12 against Hermes `1298c8e`.

### Findings

| # | Severity | Area | Finding | Status |
|---|---|---|---|---|
| R1 | Medium | spill detection (`3453d3a`) | Hermes wraps `web_extract`/`web_search`/`browser_*`/`mcp_*` results in `<untrusted_tool_result>` *after* the spill. "Starts with the tag" missed those spills: the brief rung (1,012-1,053 chars, mostly external preview) instead of the 1.4.0 `spilled to <path>` header, and no spill note in the lean tail. | Fixed `30ac14d` |
| R2 | Low | query (`c4e46f1`) | `/compress terminal` on `terminal` rows: focus and call name collapse into one query part, and the filter dropped it. | Fixed `5f728cf` |
| R3 | Medium | engine secrets screen (`173a948`) | Unquoted `KEY=value` pattern matched code (`password = get_password_from_vault()`, `api_key = agent._x`). `contains_secret` was true for 141/2,372 non-test Hermes `.py` files (9 at `a541e92`), and the recovery store refuses any original with a secret, so `tameru_expand` could not serve those files from the store. | Fixed engine `a1e8a7f`, vendored `5b8855d`: 15/2,372 (all literal fixture keys); linear on 200K-char inputs |
| R4 | Medium (pre-existing since 1.4.0) | `tameru_expand` grep | `_backtracking_risk` catches nested repeats only. Polynomial patterns pass: `.*.*x` on a 1,200-char line took 1.4 s, `.*.*.*x` on a 3,000-char line ran >275 s (killed). `.*x` is quadratic on very long (minified) lines. `re` cannot be interrupted. | Fixed in §8 (`7634d3a`) |
| R5 | Low (design) | pass-2 reservation | Only the extract is limited. A brief still uses the full room, and real briefs (1.8-2.7k) are larger than the 1,200 reserved, so the newest rows of a tight pass can still fall to headers or to a sliver brief (244-327 chars). Probe: 6 orders × 4 budgets, 13/24 change with the reservation, all toward more body-carrying rows; none lose one. | Fixed in §8 (`27b66d7`, `83df6b1`) |
| R6 | Info | spill detection | A tool output (or wrapped web page) that itself *starts* with `<persisted-output>` and a `Full output saved to:` line is taken as a spill and cut to a header naming that path. 1.4.0 accepted any result containing the tag, so this is narrower than before. | Not a bug |
| R7 | Info | refs | `ref` is 32 bits; a session-archive lookup over 20K rows has a ~5e-6 chance per lookup of answering with a different row. | Widened to 64 bits in §8 (`725fc9c`) |
| R8 | Low | tests | `test_the_query_leaves_out_the_bare_tool_name[read_file]` passes with the filter reverted (only `[terminal]` guards it). `test_inline_short_gaps_in_extract` asserts only when the rung is `extract` (it does fail when inlining is removed). The yaml and setuptools tests are skipped in both dev venvs (they pass, 278/278, in a venv with those packages). | Fixed in §8 (`83df6b1`, `53c23ea`) |
| R9 | Info | engine perf | Linear in every adversarial case timed (`perf_engine.txt`): saturated rg up to 3.0 MB 1.1 s, captured-log pytest 2.5 MB 0.8 s, nested rules 4.6 MB 22.5 s (4× input, 4× time), 4 MB single line 1.1 s (fail-open). | OK |

### Mutation check of the 1.4.1 fixes (revert the fix, run the tests)

Every fix is caught: spill "contains" (2 tests fail), no reservation (1), tool name kept (1), no `inline_short_gaps` (1),
compare.py home (1); engine: saturated grep (4), captured-log collapse (2), CLI flags (3), renderer inlining (4),
`_with_gaps` inlining (2), agent-format inlining (2), AIza pattern (1), cross-file marker (1). The red-team tests fail on
`e2716a8` (6 in the stub, 7 on real Hermes) and on `e099d10` (engine secrets test).

### Fuzzing (`tests/test_fuzz_redteam.py`, hypothesis 6.169, random seeds)

| property | runs | failures |
|---|---|---|
| engine entry points (determinism, extract never longer than its input) | 3,000 real Hermes + 3,000 stub | 0 |
| ladder, one demotion (no swallowed error, determinism, row never longer, extract keeps every error fingerprint, pairing, byte-stable second demotion) | 3,000 real Hermes + 3,000 stub | 0 |
| whole prune pass (pairing, no swallowed error, rendered rows unchanged by a second prune) | 1,500 real Hermes | 0 |

The real-Hermes single-row runs exercised the code before the R1-R3 fixes; the stub runs and the prune run, the code after.
No crash, non-determinism or invariant violation was found.

### Suites (before: `e2716a8`; after: red-team head)

| suite | before | after |
|---|---|---|
| plugin, stub | 1085 passed, 269 skipped | 1094 passed, 270 skipped |
| plugin, real Hermes | 1349 passed, 5 skipped | 1359 passed, 5 skipped |
| engine pytest | `e099d10`: 1159 passed, 3 skipped | `a1e8a7f`: 1162 passed, 3 skipped |
| `run_battery.py` | 15/15 | 15/15, savings identical |
| `threshold_sweep.py` | 77.3 / 68.4 / 61.2 / 41.5 / 24.9% | same |
| `compare.py` | 13/17 at 1,019,153; product 12/17 at 990,003 | identical |
| `per_result.py` | 14/14 answers, 22/22 templates, corpus 67.5% vs 81.0% | identical |

The added tests account for the difference: `test_redteam_141.py` (6 stub + 1 real-only) and 3 fuzz properties.

### Docs check

Every number in §2-§4 was re-measured or matched to a saved artifact: the 1.4.0 and pre-query-change per-result
tables (re-run at `0a7e568` and `1a47165`), the decomposition in §3 (travis 40.9/47.9, jp_musubi 28.6/31.5, git_log
89.4, ar_needle 72.9, git_log with `terminal` 81.0), the replay and WIP numbers, the suite counts and the live session
(23 API calls, 559,425 vs 473,058 prompt tokens, briefs 2,211→1,189 and 11,734→1,208). One wording was wrong (§3
travis "that part alone", corrected). Not re-checked on the dev machine: the `b78931e` suite count (1319/30) and the
"12/12 correct" judgement, which was made by reading the answers.

## 8. Fix-all round (same branches; plugin `d8fd2d6..725fc9c`, engine `a1e8a7f..91f00e0`)

Every open red-team item was fixed. Linux dev machine only, measured on 2026-10-12 against Hermes `1298c8e`.
For each fix, a test that fails when the fix is reverted ("mutation" below).

| # | Change | Commits | Tests (mutation result) |
|---|---|---|---|
| R4 | `tameru_expand` grep: a pattern with a repeat or an alternation runs in a child `python -I -S` process (stdlib only, JSON over stdin), killed after `expand_grep_timeout` s (default 2, 0.05-600), error "grep timed out after N s and was stopped ... simplify it". Repeat-free patterns stay in-process. Lines capped at `expand_grep_line_chars` (20,000; `lines_capped` reported). Nested-repeat pre-check kept. | `7634d3a`, `22be182` | `test_fixall_141.py` R4 tests (6). Forcing the in-process path: the timeout test hangs (killed at 40 s). |
| R5 | Pass-2 reservation limits the brief too, and each later row reserves an estimate of its own brief (`max(brief_chars, brief_share*size)`, x1.5 with an error fingerprint, capped at its extract cap) instead of a flat 1,200. A `brief_chars/2` floor tried in `27b66d7` was removed in `83df6b1` (below). | `27b66d7`, `83df6b1` | `test_r5_*` (2 fail without the fix); `test_retained_budget.py` updated to the intended newest-first order (2 tests pinned the old order). |
| R3 | Engine: whole-line env/shell (`KEY=value`, no spaces) and YAML (`key: value`) credential assignments are secrets again, whatever the value looks like, unless an expression/placeholder, ending in `,` `;` `)`, or (YAML) an attribute chain. | engine `91f00e0`, vendored `b029396` | engine `test_secrets_screen_141.py` (+3; positives fail without the fix), plugin `test_r3_env_file_passphrases_are_not_kept_for_expand` (fails with the `a1e8a7f` vendor). Linear: 100K/200K/400K inputs 0.05/0.10/0.20 s. |
| R8 | Brief rung inlines short gaps (engine `brief_context` honours `InliningGapMarker`); `[read_file]` case made to bite; inline-gap test asserts at the brief rung; `pyyaml`, `setuptools`, `hypothesis` in the `dev` extra (documented in `DEVELOPMENT.md`). | engine `b391ed9`, `83df6b1`, `53c23ea` | engine `test_brief_inline_gaps.py` (1 fails without); `test_inline_short_gaps_in_brief` (fails with the plain marker); `[read_file]` and `[terminal]` both fail with the filter removed; the yaml and build_py tests now pass instead of skipping. |
| R1 | Untrusted-wrapper spill check: any attributes in any order, tag case, CRLF/trailing blanks, 0-4 notice lines, extra blank lines, up to 3 nested wraps; notice must end in a blank line. Still pinned by `test_real_hermes_wrapped_spill_is_a_spill`. | `aa51a4a` | `test_r1_*` (10; 5 of the 6 variants fail on the old regex, the exact Hermes format passes both). |
| R7 | Refs are 16 hex (64 bits). An old 8-hex ref is the prefix of the new one: headers, `tameru_expand`, the store and the session-archive lookup accept both; an 8-hex ref prefixing two keys is a miss, not a wrong original. | `725fc9c` | `test_r7_*` (5; 3 fail without the change, the ambiguity and session-lookup tests are guards). `test_render.py` grammar/formula tests updated. |

### What the brief-floor episode showed

With brief inlining plus the `brief_chars/2` floor from `27b66d7`, the product replay lost gold fact R4: the T12
`git diff` row became a header, where a 346-char brief had kept the `index 3f9a1c2..8be41d7` line. Without the floor
both arms keep 13/17, so the floor (a speculative change) was dropped. Small briefs can still occur; none appeared in
the reservation probe after the change.

### Reservation probe (`reservation_probe_compare.txt`, 6 orders x 4 budgets)

The newest row keeps a body in 24/24 cases (7/24 at `d8fd2d6`); briefs under 400 chars: 0 (6 before). Cost: the
estimate over-reserves in some orders, so a tight budget is used less fully (LLLLF 3,000: 2,321 of 3,000 vs 2,936;
LFLFLF 9,000: 8,036 vs 8,920). In the replay this nets out (below).

### `tameru_expand` grep timing (5,000-line, 330 KB original, median of 20 calls, 3 runs)

| pattern | `d8fd2d6` | fix-all head |
|---|---|---|
| repeat-free (`ERROR`, `error timeout`) | 3.0-3.8 ms | 3.4-3.7 ms (in-process; one cold run 6.4-6.8 ms) |
| child path (`ERROR\|WARN`, `batch \d+ in 9\dms`, `err.*out`) | 3.2-11.4 ms | 21-26 ms |
| `.*.*.*x`, `a*a*a*a*x`, `\w*\w*\w*!` on a 3,000-char line | `.*.*.*x` >275 s (killed) | error after 1.00 s (timeout 1) / 2.00 s (default 2) |

The child costs about 18-20 ms per call (interpreter start). Differential check: 3,000 random repeat/alternation
patterns, `matched_lines` equal to Python `re` line by line in all 3,000, no errors (`fuzz/grep_differential.txt`).
Windows: `subprocess.run(timeout=...)` (TerminateProcess) with `CREATE_NO_WINDOW`; not run on Windows. A frozen
interpreter (`sys.executable` not Python) makes such greps return an error rather than run.

### Suites (before: red-team head `d8fd2d6`; after: `725fc9c`, engine `91f00e0`)

| suite | before | after |
|---|---|---|
| plugin, stub | 1094 passed, 270 skipped | 1125 passed, 268 skipped |
| plugin, real Hermes | 1359 passed, 5 skipped | 1390 passed, 3 skipped |
| engine pytest | `a1e8a7f`: 1162 passed, 3 skipped | `91f00e0`: 1168 passed, 3 skipped |
| `run_battery.py` | 15/15 | 15/15, savings identical |
| `threshold_sweep.py` | 77.3 / 68.4 / 61.2 / 41.5 / 24.9% | identical |
| `compare.py` `tameru_1_4` | 13/17, 5,481,666 chars, 1,019,153 cache-priced | 13/17, 5,484,812, 1,019,675 |
| `compare.py` product | 12/17, 5,296,155, 990,003 | 13/17, 5,290,759, 989,114 |
| `per_result.py` | 14/14 answers, 22/22 templates, 86.2%; corpus 67.5% vs 81.0% | 14/14, 22/22, 86.1%; corpus 67.2% vs 81.0% |
| secrets probe (2,372 Hermes `.py`) | 15 | 15 |

Replay steps: R5 alone took product to 13/17 and both arms' input down (5,471,639 / 5,287,152); the 16-hex refs add
8 chars per header (+3,479 / +4,035 chars over the session; savings -0.1 to -0.7 points in the per-result tables). Baselines were
re-recorded for each intended change (`tameru_1_4*.json`; `stock.json` untouched).

### Fuzzing (final code, random seeds)

Code at `725fc9c` / engine `91f00e0`, `tests/test_fuzz_redteam.py` with `FUZZ_RANDOM=1`:

| property | runs | failures |
|---|---|---|
| engine entry points | 3,000 stub + 3,000 real Hermes | 0 |
| ladder, one demotion | 3,000 stub + 3,000 real Hermes | 0 |
| whole prune pass (pairing, no swallowed error, byte stability) | 3,000 stub + 1,000 real Hermes | 0 |
| `tameru_expand` grep vs Python `re` (random repeat/alternation patterns) | 3,000 | 0 |

16,000 property examples plus 3,000 grep patterns; no crash, non-determinism or invariant violation
(`fuzz/stub.txt`, `fuzz/real.txt`, `fuzz/real_prune.txt`, `fuzz/grep_differential.txt`).

### Not fixed

- Unquoted snake_case or dotted credentials inside code-shaped lines (`password = settings_default_password`, a
  keyword argument) stay unflagged: lexically they are code. Only whole-line env/YAML assignments are caught.
- The grep timeout is untested on Windows.

## 9. Hermes as shipped, large windows, and error lines past the budget (2026-10-13)

A red-team pass on the evaluation itself. All numbers: the 30-turn replay, seed 0, real Hermes at `main` `63d0b94`
(1,427 real-Hermes tests pass there and at the CI pin `a0a4992`); costs are cache-priced (read 0.1x, write 1.25x),
in chars as the harness reports and in o200k tokens from a re-pricing of every request.

### The "stock" baseline was not Hermes as shipped

The replay's `stock` engine runs Hermes' proactive prune at 48K tokens. Hermes ships it off
(`proactive_prune_tokens: 0`, `hermes_cli/config_defaults.py`). A user who switches from out-of-the-box Hermes to
the plugin is compared with that, so the replay now has a `stock_defaults` engine and baseline
(`tests/eval/baselines/stock_defaults.json`) and an acceptance test against it.

| 64K window | Hermes as shipped | Hermes, prune at 48K | plugin, equal gates | plugin as shipped |
|---|---:|---:|---:|---:|
| cost, chars | 1,048,851 | 1,273,007 | 1,019,675 (−2.8%) | 989,114 (**−5.7%**) |
| cost, o200k tokens | 370,086 | 442,162 | 354,251 (−4.3%) | 343,939 (**−7.1%**) |
| compactions | 2 | 1 | 1 | 1 |
| gold facts (recall/artifact/continuation/decision) | 6/17 (0/2/2/2) | 10/17 (0/3/3/4) | 13/17 (2/4/3/4) | 13/17 (2/4/3/4) |
| error templates | 0/7 | 0/7 | 7/7 | 7/7 |
| re-fetch: calls (facts missing) | 4 (8) | 3 (4) | 2 (2) | 2 (2) |

The "about 22% cheaper than stock" figure quoted for 1.4.0 holds against Hermes with the prune opted in (−22.2%
in tokens here); against Hermes as shipped it is **−5.7% (chars) / −7.1% (tokens)**, with twice the gold facts,
every error template and one compaction fewer.

### A fixed 48K trigger pruned large windows long before they filled

The plugin enables the proactive prune at 48K tokens by default. On a 200K window this session never reaches
Hermes' compaction threshold, so out-of-the-box Hermes keeps all 17 facts; the fixed trigger pruned it twice. The
trigger is now `max(48,000, default_proactive_prune_ratio x compaction threshold)` with ratio 0.70 (your choice
among the options measured), re-derived whenever Hermes changes the model. Hermes `main` compacts at 75% of the
window (the old pin: 85%), so the trigger is 48K up to 64K windows, 67,200 at 128K, 105,000 at 200K, 134,400 at
256K. Explicit user values and explicit constructor arguments are never rescaled.

### Rows past the budget lost their error lines

The same window sweep showed the plugin keeping **0 or 1 of 7 error templates** on 128K and 200K windows: rows
past the 6,000-char retained-extract budget (or in the pressure pass) dropped to a bare header, and with no
compaction there was no summary ledger to carry their errors. Such rows now land on a new **`errors` rung**: the
header plus one exemplar line per error template (at most `errors_row_chars`, 1,600), counted against a budget of
their own (`retained_error_budget_chars`, 8,000). A result with no error lines still gets a bare header. The rung
was added to the header grammar (`fold|extract|brief|errors|header|superseded`); every row an earlier version wrote
still parses.

| window | Hermes as shipped: cost, gold, errors | plugin before (fixed 48K, no `errors` rung) | **plugin now** |
|---|---|---|---|
| 32K | 1,098,548, 8/17, 0/7 | 795,161, 9/17, 7/7 | 795,161 (−27.6%), 9/17, 7/7 |
| 64K | 1,048,851, 6/17, 0/7 | 989,114, 13/17, 7/7 | 989,114 (−5.7%), 13/17, 7/7 |
| 128K | 1,357,458, 9/17, 0/7 (1 compaction) | 1,280,941, 13/17, **1/7** | 1,365,337 (+0.6%), 13/17, 7/7, 0 compactions |
| 200K | 1,870,570, 17/17, 7/7 | 1,280,941, 13/17, **1/7** | 1,622,456 (−13.3%), 11/17, 7/7 |
| 256K | 1,870,570, 17/17, 7/7 | 1,280,941, 13/17, **1/7** | identical to Hermes (17/17, 7/7) |

(Costs in chars. "Before" is the same code with `default_proactive_prune_ratio=0` and `errors_row_chars=0`, i.e. 1.4.1's behaviour, on the same Hermes. 32K and 64K are unchanged: there the trigger stays 48K and compaction summaries already carried the errors.) What remains is the proactive prune's
inherent trade-off: once a session passes the trigger, facts go for cost (200K: 11/17 against 17/17, 13% cheaper),
but no longer any error template. Tests: `tests/test_prune_scaling.py`, the `errors` rung tests in
`tests/test_ladder.py`, `tests/test_idempotency.py` and `tests/test_tuning.py`, and in `tests/eval`
`test_acceptance_the_shipped_plugin_beats_hermes_defaults`,
`test_a_session_under_the_scaled_trigger_is_left_exactly_as_stock_leaves_it` and
`test_large_windows_keep_every_error_template`.

### Also found

- Hermes' own one-line summary of a `terminal` result counts newlines in the JSON-encoded payload, so every terminal
  row reads "1 lines output" (stock Hermes too); the Tameru header next to it carries the true count. Not changed
  here (it is Hermes' line); worth an upstream Hermes issue.
- The replay's seeds only vary filler values; its structure (and the defaults tuned on it) is one session shape.
  More scenario shapes remain the most useful next evaluation step.

## Reproduce

```bash
python3 -m pytest -q -p no:cacheprovider                                        # stub
HERMES_REPO_ROOT=<hermes@1298c8e> python3.14 -m pytest -q -p no:cacheprovider   # real Hermes
HERMES_REPO_ROOT=<hermes@1298c8e> python3.14 tests/eval/compare.py              # §2 replay table
HERMES_REPO_ROOT=<hermes@1298c8e> python3.14 tests/eval/per_result.py           # §2 and §3 tables
```
