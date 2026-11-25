# Hermes plugin improvement register

One line per probed-and-fixed gap. Runs append R-NNNN entries; do not re-file listed items.

- R-0001 | seam arg types | digest non-str, tool_calls_by_id/build_index non-dict rows, call_id_to_tool/cache non-dict → AttributeError; now TypeError at entry. [PR #17]
- R-0002 | seam arg types | merge_pins non-str summary / non-PinState state → AttributeError; now TypeError | PR #22
- R-0003 | seam arg types | message_budget_tokens non-list/str rows → AttributeError deep in estimator; now TypeError | PR #23
- R-0004 | payload seam | parse_payload non-str content, meta_line non-dict meta / non-int max_chars → misread/cryptic TypeError; now TypeError | PR #24
