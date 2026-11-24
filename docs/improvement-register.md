# Hermes plugin improvement register

One line per probed-and-fixed gap. Runs append R-NNNN entries; do not re-file listed items.

- R-0001 | seam arg types | digest non-str, tool_calls_by_id/build_index non-dict rows, call_id_to_tool/cache non-dict → AttributeError; now TypeError at entry. [PR #17]
