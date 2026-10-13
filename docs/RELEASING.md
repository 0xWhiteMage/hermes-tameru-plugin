# Releasing

Three repositories are involved. Only the last step needs anyone outside this project.

| Repository | Role | Who merges |
|---|---|---|
| [`tameru-compaction-system`](https://github.com/0xWhiteMage/tameru-compaction-system) | The engine library (`src/tameru/`). | Us |
| `hermes-tameru-plugin` (this repo) | The Hermes integration. Vendors the engine byte-for-byte in `hermes_tameru_plugin/tameru/`. | Us |
| [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) `plugin-catalog/tameru.yaml` | The catalogue entry Hermes users install from, pinned to one commit of this repo. | Nous (human review) |

Batch changes and release them together: every release costs exactly one catalogue PR.

## 1. Engine (only when engine code changed)

1. Land the change in `tameru-compaction-system` with its tests; run the suite, `run_battery.py` and
   `threshold_sweep.py` (savings must not move unless the change intends it).
2. Bump `__version__`, `ENGINE_VERSION` and `pyproject.toml` together, and date the CHANGELOG section.
3. Merge to its `main`.

## 2. Plugin

1. Re-vendor (see [DEVELOPMENT.md](DEVELOPMENT.md#vendoring-the-engine)), update `VENDORED_FROM`, and check:
   `python scripts/sync_to_harness.py <plugin>/hermes_tameru_plugin/tameru --manifest ../plugin.yaml --check`
   from the engine repo reports no drift.
2. The plugin version must equal the vendored `ENGINE_VERSION` (`tests/test_packaging.py` enforces it):
   `pyproject.toml` and `hermes_tameru_plugin/plugin.yaml` (the sync stamps the manifest).
3. Run both test modes (stub in a venv **without** Hermes installed; real Hermes at the CI pin and at Hermes
   `main`), `ruff check .`, the replay (`tests/eval`, including `--old-harness` comparisons if the harness changed)
   and review every baseline diff.
4. Run Hermes' own validator on the subdir, from a Hermes checkout:
   `hermes plugins validate <plugin>/hermes_tameru_plugin` (or `validate_plugin_dir`). Expect 14/14 checks, scan
   "safe", and one informational rule-9 note (the private `ContextCompressor` import).
5. Update `CHANGELOG.md` and the report, merge to `main`, and note the merge commit SHA.

## 3. Catalogue (one PR to Nous per release)

Edit only `plugin-catalog/tameru.yaml` in `NousResearch/hermes-agent`:

```yaml
sha: <40-hex merge commit of this repo's main>
version: "X.Y.Z"
```

Keep `name: tameru`, `subdir: hermes_tameru_plugin` and `provides_tools: [tameru_expand]`. Leave `requires_hermes`
alone unless a seam requirement changed. Change the disclosure text only when what the plugin *does* changed (a new
tool, a new process, network or file access, a new default it turns on); keep it free of tuning numbers so a
retune does not need a disclosure edit. It must mention:

- it replaces Hermes' context engine and subclasses `ContextCompressor` through a private import;
- it turns on Hermes' proactive tool-result pruning when the user has not configured it, scaled to the model's
  context window;
- it adds the `tameru_expand` tool;
- grep patterns with repeats run in a short-lived stdlib-only `python -I -S` child with a timeout;
- no network calls and no credentials; a telemetry file only when `telemetry_log` is set; in-process isolation.

The PR must come from the plugin owner or a major contributor. Reviewers read the commit range between the old and
new `sha`, so keep release commits readable. Users then update with `hermes plugins update tameru`.

Users who want a release before the catalogue PR merges can install from this repo directly (directory copy into
`~/.hermes/plugins/tameru/`, or `pip install` plus `hermes plugins enable tameru`).
