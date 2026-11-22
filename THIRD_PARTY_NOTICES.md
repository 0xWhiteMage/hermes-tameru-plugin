# Third-party notices

Plugin-owned code is Apache-2.0-licensed (see `LICENSE`), except for the MIT-licensed adaptations
in the four Hermes stubs identified below. Historical MIT notices and the provenance
of repository-only fixtures are retained below. The engine is bundled in the sdist and wheel;
all material under `tests/` is repository-only and excluded from both distributions.

## Hermes Agent — Nous Research

- Project: <https://github.com/NousResearch/hermes-agent>
- The stubs and this attribution were introduced together in plugin commit
  `4dfbffddb1a393b0ccfd922b573f9b44a34b3666`. Plugin history records a compressor-helper refresh
  against `a0a49926f44c231f3456faffeaa4b1cc8ee4274c` in commit
  `8aa577342fc83fe3e4e0ae354880e02bdfa5dc32`; later compatibility adaptations mean the stubs
  are not a verbatim snapshot of a single Hermes revision.
- License: MIT, Copyright (c) 2025 Nous Research

| File in this repository | Hermes source revision | Relationship to Hermes Agent |
|---|---|---|
| `tests/hermes_stub/agent/compression_marker.py` | `1298c8e74baa73e1a2b90124228d017261ac6bc4` | Copied verbatim from `agent/compression_marker.py` (a notice header was prepended). |
| `tests/hermes_stub/agent/turn_context.py` | `1298c8e74baa73e1a2b90124228d017261ac6bc4` | Three helpers (`substitute_api_content`, `drop_stale_api_content`, `extract_api_content_sidecar`) copied verbatim from `agent/turn_context.py`. |
| `tests/hermes_stub/agent/context_engine.py` | `1298c8e74baa73e1a2b90124228d017261ac6bc4` | Derived: attribute names, signatures and hook defaults of `agent/context_engine.py`; docstrings abbreviated. |
| `tests/hermes_stub/agent/context_compressor.py` | `a0a49926f44c231f3456faffeaa4b1cc8ee4274c` (verbatim helpers; first derived from `1298c8e74baa73e1a2b90124228d017261ac6bc4`) | Derived: class and method signatures, the skip rules of `_demote_tool_result_at`, the structure of `_prune_old_tool_results` / `_pressure_demote_tail` / `_demote_stale_tail_tools`, and a handful of small helpers copied verbatim (`_is_summary_stub`, `_lean_recovery_stub`, `_tc_get`, `_json_dict`, `_tool_calls_by_id`, `_skill_view_call_sites`, `_collect_protected_skill_names`, `_dedupe_tool_results`, `_prune_boundary`, `_tail_soft_ceiling`, `_pending_tool_round`, the image-stripping helpers) from `agent/context_compressor.py`; other bodies are simplified re-implementations. |

These stubs are used when a real Hermes checkout is unavailable (`HERMES_REPO_ROOT` unset).
The plugin's adaptations in these four stubs are also MIT-licensed. Each file retains the
following upstream MIT notice.

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
<https://github.com/0xWhiteMage/tameru-compaction-system> (`src/tameru/`). The engine repository
is now Apache-2.0-licensed, since commit `ed8bcff6e7de66852d19ee2f1fcc53724b6bbb3e`.
The vendored **1.9.0** snapshot at `9f1f1c45e432e63119f6fa0cd232bb27b476e05b` was published
under MIT and is distributed here, by the same copyright holder (Copyright 2026 Benjamin Ang),
as part of this Apache-2.0 work. Its pin is recorded by `VENDORED_FROM` in the plugin-owned
`tameru/__init__.py`; other engine modules remain upstream copies.

The snapshot's original MIT notice, also covering the historical benchmark code, is retained in
[Retained historical Tameru MIT notice](#retained-historical-tameru-mit-notice).

## Repository-only Tameru data

The following material comes from the same Tameru repository. Tameru-owned material is currently
Apache-2.0, Copyright 2026 Benjamin Ang (the same copyright holder). The benchmark source revision
below was MIT-licensed; see [Retained historical Tameru MIT notice](#retained-historical-tameru-mit-notice).
These test files are not included in the sdist or wheel.

- `tests/fixtures/engine_benchmark_cases.json` contains selected case fields (`name`, `q`, `ctx`,
  `gold`, `forbid`) from `CASES` in `benchmarks/run_battery.py` at commit
  `867eb31e1a8af1d32d835caf196aca4c42568040`. Generated text and referenced fixture contents
  are materialized in the JSON; runner settings and two cases are omitted:
  `large_doc_perf` (performance) and `empty_query_failopen` (fail-open behaviour).
  Referenced upstream inputs are `fixtures/shape-git-log.txt`, `fixtures/shape-npm-log.txt`,
  `fixtures/shape-jp-musubi.txt`, and `fixtures/shape-yaml-travis.txt` at that revision.
  These include captured output and the following third-party excerpts; the engine's repository
  license does not establish their terms, and no blanket Apache-2.0 ownership claim is made:
  - `jp_musubi`: 8,023 characters (10,931 UTF-8 bytes) adapted from
    [`kohya-ss/musubi-tuner` documentation, `docs/ideogram4.md`](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/docs/ideogram4.md),
    with markup/escape transformations. At that inspected revision the repository has no LICENSE
    file, and its README's Apache-2.0 grant covers code only. The documentation's licence is
    unknown; that grant does not grant a licence for the documentation.
  - `travis_yaml`: 2,691 bytes matching [`php/php-src` `.travis.yml`](https://github.com/php/php-src/blob/004cb827501d1ddaf98daacb185a53e0816f78a7/.travis.yml),
    covered by PHP License 3.01 at that inspected revision. See
    [Retained PHP License 3.01 notice](#retained-php-license-301-notice), including its clause-6
    acknowledgment: "This product includes PHP software, freely available from <http://www.php.net/software/>".
- `tests/desktop/ui-icons.json` copies `docs/ui-icons.json` at commit
  `29124e3856f611e4c1c05b990a84203782a20c8f`.
- `tests/desktop/ui-vocabulary.json` copies `docs/ui-vocabulary.json` at commit
  `b00efbb140010dcb23a76008e70a3306a092705e`.
  The shipped Desktop panel also uses these shared Tameru drawings and vocabulary.

The eval documentation describes the scenarios, generated payloads and saved matrix/baseline
reports as local synthetic evaluation material, not a bundled external dataset.

## Retained historical Tameru MIT notice

This notice applies to the pinned engine source and the historical benchmark source identified
above, both of which carried this license at their cited revisions.

```
MIT License

Copyright (c) 2026 Benjamin Ang

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

## Retained PHP License 3.01 notice

The following is retained from `php/php-src`'s `LICENSE` at the inspected revision linked above.

```
--------------------------------------------------------------------
                  The PHP License, version 3.01
Copyright (c) 1999 - 2021 The PHP Group. All rights reserved.
--------------------------------------------------------------------

Redistribution and use in source and binary forms, with or without
modification, is permitted provided that the following conditions
are met:

  1. Redistributions of source code must retain the above copyright
     notice, this list of conditions and the following disclaimer.

  2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in
     the documentation and/or other materials provided with the
     distribution.

  3. The name "PHP" must not be used to endorse or promote products
     derived from this software without prior written permission. For
     written permission, please contact group@php.net.

  4. Products derived from this software may not be called "PHP", nor
     may "PHP" appear in their name, without prior written permission
     from group@php.net.  You may indicate that your software works in
     conjunction with PHP by saying "Foo for PHP" instead of calling
     it "PHP Foo" or "phpfoo"

  5. The PHP Group may publish revised and/or new versions of the
     license from time to time. Each version will be given a
     distinguishing version number.
     Once covered code has been published under a particular version
     of the license, you may always continue to use it under the terms
     of that version. You may also choose to use such covered code
     under the terms of any subsequent version of the license
     published by the PHP Group. No one other than the PHP Group has
     the right to modify the terms applicable to covered code created
     under this License.

  6. Redistributions of any form whatsoever must retain the following
     acknowledgment:
     "This product includes PHP software, freely available from
     <http://www.php.net/software/>".

THIS SOFTWARE IS PROVIDED BY THE PHP DEVELOPMENT TEAM ``AS IS'' AND
ANY EXPRESSED OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE PHP
DEVELOPMENT TEAM OR ITS CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.

--------------------------------------------------------------------

This software consists of voluntary contributions made by many
individuals on behalf of the PHP Group.

The PHP Group can be contacted via Email at group@php.net.

For more information on the PHP Group and the PHP project,
please see <http://www.php.net>.

PHP includes the Zend Engine, freely available at
<http://www.zend.com>.
```

## Host-provided and optional/development dependencies

The plugin declares no Python runtime dependencies. Hermes Agent (MIT, Nous Research),
`@hermes/plugin-sdk` (a Hermes Desktop source alias covered by its repository's MIT license),
and React (MIT) are supplied by the host;
their implementations are not redistributed in this plugin's sdist or wheel.

The vendored optional semantic adapter can use separately installed `sentence-transformers`
and separately obtained model weights. Neither that dependency nor model weights are redistributed
here; users enabling it must consult the installed dependency's license and selected models' terms.

Development/build tools are installed separately, not redistributed: pytest, Ruff, PyYAML,
setuptools and the `build` frontend (MIT), and Hypothesis (**MPL-2.0**, development-only).
Desktop test dependencies jsdom, React and React DOM are MIT-licensed and are also not
redistributed. Installing optional/dev dependencies does not make them part of this package.
