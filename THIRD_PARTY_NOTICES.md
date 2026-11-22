# Third-party notices

This repository is Apache-2.0-licensed (see `LICENSE`), except for the MIT-licensed Hermes-derived
fixtures identified below. The engine is bundled in the sdist and wheel; all material under `tests/`
is repository-only and excluded from both distributions.

## Hermes Agent — Nous Research

- Project: <https://github.com/NousResearch/hermes-agent>
- Original source revision (also recorded in the four stub headers):
  `1298c8e74baa73e1a2b90124228d017261ac6bc4`.
- The stubs and this attribution were introduced together in plugin commit
  `4dfbffddb1a393b0ccfd922b573f9b44a34b3666`. The compressor helpers were subsequently refreshed
  against `a0a49926f44c231f3456faffeaa4b1cc8ee4274c` in plugin commit
  `8aa577342fc83fe3e4e0ae354880e02bdfa5dc32`; later compatibility adaptations mean the stubs
  are not a verbatim snapshot of a single Hermes revision.
- License: MIT, Copyright (c) 2025 Nous Research

| File in this repository | Relationship to Hermes Agent |
|---|---|
| `tests/hermes_stub/agent/compression_marker.py` | Copied verbatim from `agent/compression_marker.py` (a notice header was prepended). |
| `tests/hermes_stub/agent/turn_context.py` | Three helpers (`substitute_api_content`, `drop_stale_api_content`, `extract_api_content_sidecar`) copied verbatim from `agent/turn_context.py`. |
| `tests/hermes_stub/agent/context_engine.py` | Derived: attribute names, signatures and hook defaults of `agent/context_engine.py`; docstrings abbreviated. |
| `tests/hermes_stub/agent/context_compressor.py` | Derived: class and method signatures, the skip rules of `_demote_tool_result_at`, the structure of `_prune_old_tool_results` / `_pressure_demote_tail` / `_demote_stale_tail_tools`, and a handful of small helpers copied verbatim (`_is_summary_stub`, `_lean_recovery_stub`, `_tc_get`, `_json_dict`, `_tool_calls_by_id`, `_skill_view_call_sites`, `_collect_protected_skill_names`, `_dedupe_tool_results`, `_prune_boundary`, `_tail_soft_ceiling`, `_pending_tool_round`, the image-stripping helpers) from `agent/context_compressor.py`; other bodies are simplified re-implementations. |

These stubs are used when a real Hermes checkout is unavailable (`HERMES_REPO_ROOT` unset).
The relationship descriptions refer to the original extraction; subsequent compatibility edits
may change individual helpers. Each file retains the following MIT notice.

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
<https://github.com/0xWhiteMage/tameru-compaction-system> (`src/tameru/`), Apache-2.0, same copyright
holder as this repository: Copyright 2026 Benjamin Ang. The bundled engine is version **1.9.0**,
synced from commit `9f1f1c45e432e63119f6fa0cd232bb27b476e05b`, as recorded by `VENDORED_FROM`
in the plugin-owned `tameru/__init__.py`. Other engine modules remain upstream copies.

## Repository-only Tameru data

The following material comes from the same Tameru repository, Apache-2.0, Copyright 2026
Benjamin Ang (the same copyright holder). It is not included in the sdist or wheel.

- `tests/fixtures/engine_benchmark_cases.json` contains selected case fields (`name`, `q`, `ctx`,
  `gold`, `forbid`) from `CASES` in `benchmarks/run_battery.py` at commit
  `867eb31e1a8af1d32d835caf196aca4c42568040`. Generated text and referenced fixture contents
  are materialized in the JSON; runner settings and performance-only cases are omitted.
  Referenced upstream inputs are `fixtures/shape-git-log.txt`, `fixtures/shape-npm-log.txt`,
  `fixtures/shape-jp-musubi.txt`, and `fixtures/shape-yaml-travis.txt` at that revision.
- `tests/desktop/ui-icons.json` copies `docs/ui-icons.json` at commit
  `29124e3856f611e4c1c05b990a84203782a20c8f`.
- `tests/desktop/ui-vocabulary.json` copies `docs/ui-vocabulary.json` at commit
  `b00efbb140010dcb23a76008e70a3306a092705e`.
  The shipped Desktop panel also uses these shared Tameru drawings and vocabulary.

The eval scenarios, generated payloads and saved matrix/baseline reports are local synthetic
evaluation material, not a bundled external dataset.

## Host-provided and optional/development dependencies

The plugin declares no Python runtime dependencies. Hermes Agent (MIT, Nous Research),
`@hermes/plugin-sdk` (provided by Hermes Desktop), and React (MIT) are supplied by the host;
their implementations are not redistributed in this plugin's sdist or wheel.

The vendored optional semantic adapter can use `sentence-transformers` (Apache-2.0) and separately
obtained model weights. Neither that dependency nor model weights are redistributed here;
users enabling it must consult the selected models' terms.

Development/build tools are installed separately, not redistributed: pytest, Ruff, PyYAML,
setuptools and the `build` frontend (MIT), and Hypothesis (**MPL-2.0**, development-only).
Desktop test dependencies jsdom, React and React DOM are MIT-licensed and are also not
redistributed. Installing optional/dev dependencies does not make them part of this package.
