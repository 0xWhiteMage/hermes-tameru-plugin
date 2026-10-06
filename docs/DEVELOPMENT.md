# Development

## Tests: two modes

The suite runs against either a **stub** of Hermes' `agent` package (`tests/hermes_stub`, strict about
`ContextCompressor.__init__` arguments as real Hermes is) or a **real Hermes checkout**.

```bash
pip install -e .[dev]

# stub mode (any Python >= 3.11)
python3 -m pytest -q -p no:cacheprovider

# real mode (Python 3.14 at the pinned SHA; Hermes' dependencies resolve there)
HERMES_REPO_ROOT=/path/to/hermes-agent /path/to/venv314/bin/python -m pytest -q -p no:cacheprovider

ruff check .
```

Markers: `real_hermes` (skipped without `HERMES_REPO_ROOT`), `stub_only` (skipped on real Hermes), `perf` (CI runs
`-m "not perf"`). `tests/test_seam_contract.py` checks the private Hermes names the plugin hooks; if it fails
after a Hermes bump, a seam moved.

## Real Hermes setup

```bash
git clone https://github.com/NousResearch/hermes-agent
git -C hermes-agent checkout 1298c8e74baa73e1a2b90124228d017261ac6bc4   # HERMES_PINNED_SHA in ci.yml
python3.14 -m venv venv314
venv314/bin/pip install -e ./hermes-agent pytest
export HERMES_REPO_ROOT=$PWD/hermes-agent
```

Bump the pin deliberately, together with `tests/test_seam_contract.py` if a seam changed, and re-record the
`stock` baseline (below).

## Replay harness and baselines

`tests/eval` is a deterministic, offline 30-turn session that measures an engine (cost after cache pricing,
compactions, prefix invalidation, gold-fact and error-fingerprint retention, re-fetch, pairing, byte stability).
Metrics and caveats are defined in [tests/eval/README.md](../tests/eval/README.md).

```bash
HERMES_REPO_ROOT=... python -m pytest -q tests/eval         # tests and acceptance checks
HERMES_REPO_ROOT=... python tests/eval/compare.py           # side-by-side table
HERMES_REPO_ROOT=... python tests/eval/compare.py --json
```

Re-record the saved baselines (`tests/eval/baselines/*.json`) after an intended change (Hermes bump, scenario or
metric change, or a re-sync of the vendored engine, whose output the `tameru_1_4*` baselines pin):

```bash
TAMERU_WRITE_BASELINES=1 HERMES_REPO_ROOT=... python -m pytest -q tests/eval
git diff tests/eval/baselines    # read the diff before committing it
```

`stock.json` is compared exactly; `tameru_1_3.json` only while the vendored engine reports 1.3.x (skipped now).

## Vendoring the engine

`hermes_tameru_plugin/tameru/` is a copy of upstream `src/tameru/`; never edit it here. Upstream owns every file
in it except `__init__.py`, which this repo owns and which records `VENDORED_FROM` (the upstream commit). To update:

```bash
cd /path/to/tameru-compaction-system
python scripts/sync_to_harness.py <plugin_repo>/hermes_tameru_plugin/tameru --manifest ../plugin.yaml --check   # drift list, exit 1 if any
python scripts/sync_to_harness.py <plugin_repo>/hermes_tameru_plugin/tameru --manifest ../plugin.yaml           # apply
```

Then update `VENDORED_FROM`, run both test modes, re-record the baselines and review their diff. The sync stamps
`version:` and `Tameru X.Y.Z` in `plugin.yaml` from upstream's `__version__`; the rest of the manifest is
hand-written (no `kind:` key). Engine fixes go upstream first; see
[upstream-patches/README.md](upstream-patches/README.md) for how the 1.4.0 commits are exported.

## CI (`.github/workflows/ci.yml`)

| Job | What |
|---|---|
| `unit` | Stub-mode suite (`-m "not perf"`) on Python 3.11, 3.12, 3.13, 3.14 |
| `real-hermes` | Full suite against Hermes at the pinned SHA, Python 3.14, `HERMES_REPO_ROOT` set (required) |
| `hermes-main-nightly` | Same against Hermes `main`, nightly and on dispatch; allowed to fail (early warning that a private seam or the `stock` baseline moved) |
| `lint` | `ruff check .` and `compileall` of the plugin |
| `build` | Builds the wheel and sdist; asserts every module, `plugin.yaml` and the `hermes_agent.plugins` entry point are in the wheel and that tests/docs are not shipped |

## Conventions

- Standard library only; deterministic; type hints and short docstrings.
- Plugin modules live directly in `hermes_tameru_plugin/` and must import cleanly when Hermes' directory loader
  executes each sibling alone: relative imports of siblings and `.tameru.*` only, nothing imported from the package
  `__init__`, no Hermes access or side effects at import time (`tests/test_import_safety.py`). Only `engine.py`
  imports `agent.context_compressor` at module level, guarded.
- Per-agent state in instance attributes; per-pass state in `ContextVar`s.
