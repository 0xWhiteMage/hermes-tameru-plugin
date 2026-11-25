# Hermes plugin improvement register

One line per probed-and-fixed gap. Runs append R-NNNN entries; do not re-file listed items.

- R-0001 | seam arg types | digest non-str, tool_calls_by_id/build_index non-dict rows, call_id_to_tool/cache non-dict → AttributeError; now TypeError at entry. [PR #17]
- R-0002 | seam arg types | merge_pins non-str summary / non-PinState state → AttributeError; now TypeError | PR #22
- R-0003 | seam arg types | message_budget_tokens non-list/str rows → AttributeError deep in estimator; now TypeError | PR #23
- R-0004 | payload seam | parse_payload non-str content, meta_line non-dict meta / non-int max_chars → misread/cryptic TypeError; now TypeError | PR #24
- R-0005 | index/display seam | build_index validation ran after first use (cryptic crash on None); display_text iterated non-str; now entry TypeErrors | PR #25
- R-0006 | query seam | build_query max_chars None/str/float/bool/negative → cryptic TypeError deep in budget math; now entry TypeError (0 stays valid) | PR #26
- R-0007 | ledger seam | build_ledger turns/max_chars/previous + merge_into_summary summary/ledger wrong types → cryptic crashes; now entry TypeErrors | PR #27
