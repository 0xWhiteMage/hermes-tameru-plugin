# Development

## Tests: two modes

The suite runs against either a **stub** of Hermes' `agent` package (`tests/hermes_stub`, strict about
`ContextCompressor.__init__` arguments as real Hermes is) or a **real Hermes checkout**.

```bash
pip install -e .[dev]

# stub mode (any Python >= 3.11)
HERMES_PYTHON=/path/to/test/python bash /path/to/hermes-agent/scripts/run_tests.sh -j 1 tests -- -c "$PWD/pyproject.toml" -q

# real mode (Python 3.14 at the pinned SHA; Hermes' dependencies resolve there)
HERMES_PYTHON=/path/to/test/python bash /path/to/hermes-agent/scripts/run_tests.sh -j 1 tests -- -c "$PWD/pyproject.toml" --hermes-repo-root=/path/to/hermes-agent -q

# The runner clears caller environment variables. Pass --hermes-repo-root after -- to select
# real Hermes; this option is read before test collection. The older HERMES_REPO_ROOT
# environment selector remains supported by direct harness scripts.

ruff check .      # the rule set is explicit in pyproject.toml (E4, E7, E9, F)
```

On a development PC with Hermes checked out beside the plugin, the quickest real-Hermes run (Windows or POSIX, system Python 3.11 to 3.13)
puts a small `ruamel.yaml` stand-in on the path: `PYTHONPATH=<shim> HERMES_REPO_ROOT=<hermes-agent> python -m pytest -q -p no:cacheprovider`
(about 7 minutes; the full gate below takes about 12).

Where measurements run: the replay and the cost matrix are deterministic offline code, so a given commit gives the same cost and retention
numbers on any machine; only the `timing` fields differ. All the numbers in this repository were measured on the development PC (Windows 11,
local Hermes Desktop on the "This device" connection), never on a NAS or any shared host. The live checks used the Desktop app on that PC.
`.[dev]` brings `pyyaml`, `setuptools` and `hypothesis`; without them the direct `config.yaml` read
(`tests/test_config.py`), the offline packaging build (`tests/test_packaging.py`) and the fuzz harness
(`tests/test_fuzz_redteam.py`) skip, so check the skip count. In the real-mode venv install them too
(`venv314/bin/pip install pyyaml setuptools hypothesis`).

Markers: `real_hermes` (skipped without `HERMES_REPO_ROOT`), `stub_only` (skipped on real Hermes), `perf` (CI runs
`-m "not perf"`). `tests/test_seam_contract.py` checks the private Hermes names the plugin hooks; if it fails
after a Hermes bump, a seam moved.

## Checking the vendored engine

`python scripts/check_vendored.py PATH_TO_ENGINE_CLONE` (or `TAMERU_ENGINE_REPO=... python -m pytest tests/test_vendored_engine.py`) compares
every module of `hermes_tameru_plugin/tameru/` with the engine repository's `src/tameru` at the commit `VENDORED_FROM` records, ignoring line
endings. CI runs it in the `vendored` job against the engine's public repository, so the engine commit must be pushed before the plugin that
names it. The Desktop panel tests run in the `desktop-panel` job; locally: `TAMERU_UI_TEST_DEPS=<a node_modules with jsdom, react and react-dom>
node --experimental-vm-modules --test tests/desktop/activity.test.mjs` (since Hermes 0.23.1 those packages are in
`hermes-agent/apps/desktop/node_modules`). Both jobs ran green on GitHub's runners for 1.9.0.

## Looking at the holdouts

The holdout shapes (`longform`, `mixedlang`, `diagnostics`, `rereads`, `dense`, `bursty`) are only worth anything if they are not tuned against. `tests/eval/holdout_register.py`
keeps a committed list of looks: `python tests/eval/holdout_register.py record PHASE SCENARIOS` writes one entry per product hash (a SHA-256 over the plugin's own source) and refuses a
second look at the same hash unless `--again REASON` says why. Record a look when a result from a holdout is used to decide something; running them to describe a finished build is not a look.
Five of the six holdouts never compact at any window, so they check idle cost, not retention; only `bursty` compacts.

## Real Hermes setup

```bash
git clone https://github.com/NousResearch/hermes-agent
git -C hermes-agent checkout "$(cat HERMES_PIN)"   # the commit CI and the saved baselines use
python3.14 -m venv venv314
venv314/bin/pip install uv
# Hermes pins Pillow exactly while its own browser-harness dependency pins another version; pip cannot resolve that, uv with an override can.
echo "pillow==13.0.0" > overrides.txt
venv314/bin/uv pip install --override overrides.txt -e ./hermes-agent -e '.[dev]'
export HERMES_REPO_ROOT=$PWD/hermes-agent
```

Bump the pin deliberately, together with `tests/test_seam_contract.py` if a seam changed, and re-record the
baselines (below; `tests/test_hermes_pin.py` checks that every saved baseline names the pinned commit). The last bump (v0.23.0 to v0.23.1)
changed only timing keys in the baselines.

## Replay harness and baselines

`tests/eval` is a deterministic, offline 30-turn session that measures an engine (cost after cache pricing,
compactions, prefix invalidation, gold-fact and error-fingerprint retention, re-fetch, pairing, byte stability).
Metrics and caveats are defined in [tests/eval/README.md](../tests/eval/README.md).

```bash
HERMES_REPO_ROOT=... python -m pytest -q tests/eval         # tests and acceptance checks
HERMES_REPO_ROOT=... python tests/eval/compare.py           # side-by-side table
HERMES_REPO_ROOT=... python tests/eval/compare.py --json
HERMES_REPO_ROOT=... python tests/eval/per_result.py        # one demotion per agent output; plugin vs engine alone
```

`compare.py` and `per_result.py` run against a throwaway `HERMES_HOME`; set `TAMERU_EVAL_HERMES_HOME=<dir>` to choose
one (compare.py) or `TAMERU_EVAL_KEEP_HERMES_HOME=1` to keep the one in the environment.

Re-record the saved baselines (`tests/eval/baselines/*.json`) after an intended change (Hermes bump, scenario or
metric change, or a re-sync of the vendored engine, whose output the `tameru_1_4*` baselines pin):

```bash
TAMERU_WRITE_BASELINES=1 HERMES_REPO_ROOT=... python -m pytest -q tests/eval
git diff tests/eval/baselines    # read the diff before committing it
```

`stock.json` is compared exactly; `tameru_1_3.json` only while the vendored engine reports 1.3.x (skipped now). Timing keys
(`engine_seconds`, per-operation seconds) change on every run and are not compared.

## The gate report

`python tests/eval/gate.py` runs the stub suite, the real-Hermes suite (when `HERMES_REPO_ROOT` is set) and the Desktop panel tests (set
`TAMERU_UI_TEST_DEPS`), then writes `docs/gate-reports/gate-<commit>.json`: the commit, whether the tree was clean, the Hermes pin, a hash of the
saved stock baselines (a canary: if it changes, the harness moved, not the product) and each result. "Done" means this file exists for HEAD and says
`"clean": true` and `"passed": true`; the changelog and docs quote numbers only from it. It needs a clean tree and takes about 12 minutes.

## Cost tables

The README's headline tables come from `protocol3` (`tests/eval/protocol3.py`): effective cost (a fact an arm dropped is priced as a re-run of the
tool, a fact it holds behind the recovery tool as that call), structural seeds, optional cache lifetime and idle gaps, and a selectable tool-block
size. Reproduce a table with `TAMERU_P3_MATRIX=out.json TAMERU_P3_ARMS=stock_defaults,tameru_1_4_product,tameru_lazy TAMERU_P3_WINDOWS=64000,128000,200000
TAMERU_P3_SEEDS=0,1,2,3,4,5 TAMERU_P3_STRUCTURAL=1 TAMERU_P3_TOOL_CHARS=39993 python -m pytest tests/eval/test_protocol3.py -k effective_cost_matrix`.
`TAMERU_ORACLE_COLD=1` is a measurement-only switch (offer the proactive prune at turn starts that find the cache expired); it is off in every
saved baseline.

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
| `unit` | Stub-mode suite (`-m "not perf"`) on Python 3.11, 3.12, 3.13, 3.14 (full history is fetched: the rollback-script tests check out a release tag) |
| `real-hermes` | Full suite against Hermes at the pinned SHA, Python 3.14, installed through `uv` with a Pillow override, `HERMES_REPO_ROOT` set (required) |
| `hermes-main-nightly` | Same against Hermes `main`, nightly and on dispatch; allowed to fail (early warning that a private seam or the `stock` baseline moved) |
| `lint` | `ruff check .` and `compileall` of the plugin |
| `vendored` | The vendored engine equals the engine repository at the commit `VENDORED_FROM` records |
| `desktop-panel` | The Desktop panel's jsdom tests |
| `build` | Builds the wheel and sdist; asserts every module, `plugin.yaml` and the `hermes_agent.plugins` entry point are in the wheel and that tests/docs are not shipped |

## Conventions

- Standard library only; deterministic; type hints and short docstrings.
- Plugin modules live directly in `hermes_tameru_plugin/` and must import cleanly when Hermes' directory loader
  executes each sibling alone: relative imports of siblings and `.tameru.*` only, nothing imported from the package
  `__init__`, no Hermes access or side effects at import time (`tests/test_import_safety.py`). Only `engine.py`
  imports `agent.context_compressor` at module level, guarded.
- Per-agent state in instance attributes; per-pass state in `ContextVar`s.
