# Third-party notices

This repository is MIT-licensed (see `LICENSE`). The files listed below incorporate or are derived
from third-party MIT-licensed code. They are **test fixtures only**: they live under `tests/`, are
not part of the wheel, and are used only when a real Hermes Agent checkout is not available
(`HERMES_REPO_ROOT` unset).

## Hermes Agent — Nous Research

- Project: <https://github.com/NousResearch/hermes-agent>
- Revision copied from / mirrored: `1298c8e74baa73e1a2b90124228d017261ac6bc4`
- License: MIT, Copyright (c) 2025 Nous Research

| File in this repository | Relationship to Hermes Agent |
|---|---|
| `tests/hermes_stub/agent/compression_marker.py` | Copied verbatim from `agent/compression_marker.py` (a notice header was prepended). |
| `tests/hermes_stub/agent/turn_context.py` | Three helpers (`substitute_api_content`, `drop_stale_api_content`, `extract_api_content_sidecar`) copied verbatim from `agent/turn_context.py`. |
| `tests/hermes_stub/agent/context_engine.py` | Derived: attribute names, signatures and hook defaults of `agent/context_engine.py`; docstrings abbreviated. |
| `tests/hermes_stub/agent/context_compressor.py` | Derived: class and method signatures, the skip rules of `_demote_tool_result_at`, the structure of `_prune_old_tool_results` / `_pressure_demote_tail` / `_demote_stale_tail_tools`, and a handful of small helpers copied verbatim (`_is_summary_stub`, `_lean_recovery_stub`, `_tc_get`, `_json_dict`, `_tool_calls_by_id`, `_skill_view_call_sites`, `_collect_protected_skill_names`, `_dedupe_tool_results`, `_prune_boundary`, `_tail_soft_ceiling`, `_pending_tool_round`, the image-stripping helpers) from `agent/context_compressor.py`; other bodies are simplified re-implementations. |

Each of these files carries the notice below at the top, as the MIT License requires.

```
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Tameru compaction engine

`hermes_tameru_plugin/tameru/` is vendored from
<https://github.com/0xWhiteMage/tameru-compaction-system> (`src/tameru/`), MIT, same copyright
holder as this repository.
