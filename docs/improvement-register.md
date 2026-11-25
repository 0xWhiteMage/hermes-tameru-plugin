# Hermes plugin improvement register

One line per probed-and-fixed gap. Runs append R-NNNN entries; do not re-file listed items.

- R-0001 | seam arg types | digest non-str, tool_calls_by_id/build_index non-dict rows, call_id_to_tool/cache non-dict → AttributeError; now TypeError at entry. [PR #17]
- R-0002 | seam arg types | merge_pins non-str summary / non-PinState state → AttributeError; now TypeError | PR #22
- R-0003 | seam arg types | message_budget_tokens non-list/str rows → AttributeError deep in estimator; now TypeError | PR #23
- R-0004 | payload seam | parse_payload non-str content, meta_line non-dict meta / non-int max_chars → misread/cryptic TypeError; now TypeError | PR #24
- R-0005 | index/display seam | build_index validation ran after first use (cryptic crash on None); display_text iterated non-str; now entry TypeErrors | PR #25
- R-0006 | query seam | build_query max_chars None/str/float/bool/negative → cryptic TypeError deep in budget math; now entry TypeError (0 stays valid) | PR #26
- R-0007 | ledger seam | build_ledger turns/max_chars/previous + merge_into_summary summary/ledger wrong types → cryptic crashes; now entry TypeErrors | PR #27
- R-0008 | probe-open-REGRESSION-suspect | vendor refresh fc176334→1afc770 ready on branch whitemage/vendor-refresh (checker passes, 514 insertions) BUT tests/test_ladder.py::test_an_oversized_extract_is_retried_until_it_fits fails: rung is now 'brief' not 'extract' for the oversized-source case. Investigate: did engine fixed-mode extraction grow more conservative (good) or lose query-line retention (regression)? If former, update the test's expectation after verifying release_holds lines survive; if latter, file engine gap. Do not merge until resolved.
- R-0009 | probe-open-REGRESSION-suspect | engine delta fc176334→1afc770: ladder case 'oversized extract retry' — query 'what does reserve_stock do when the ledger write fails?' over hp.orders_service_source(patched=True) — old engine produced extract ≤6000 cap containing BOTH 'release_holds(inventory, holds)' and the except line; new engine falls to brief (1224 chars) LOSING release_holds (except line survives). Either fixed-mode extraction lost query-line retention (regression — investigate engine PRs #51/#58/#45/fixed-mode retry loop) or cap accounting changed legitimately. Repro: hermes tests/test_ladder.py::test_an_oversized_extract_is_retried_until_it_fits on branch whitemage/vendor-refresh.
