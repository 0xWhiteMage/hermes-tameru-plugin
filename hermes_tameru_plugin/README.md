# Tameru for Hermes

A context engine for [Hermes Agent](https://github.com/NousResearch/hermes-agent): deterministic, extractive
per-result compaction ([Tameru](https://github.com/0xWhiteMage/tameru-compaction-system)) inside Hermes' own
pruning gates. Full documentation, measurements and changelog:
[0xWhiteMage/hermes-tameru-plugin](https://github.com/0xWhiteMage/hermes-tameru-plugin).

## Enable

```yaml
# ~/.hermes/config.yaml
context:
  engine: tameru
```

A catalogue or directory install lands in `~/.hermes/plugins/tameru/`; nothing else is needed. A `pip install`
also needs `hermes plugins enable tameru`.

## What it changes

- When Hermes demotes an old tool result, the plugin keeps a query-aware extract or a structural brief (always
  with one line per distinct error) instead of a one-line summary. Hermes still decides when to prune, which rows,
  and whether a pass is worth committing.
- It turns on Hermes' proactive tool-result pruning when you have not configured it, with a trigger scaled to
  the model's context window. Your own `compression.*` values always win.
- It adds one tool, `tameru_expand`, which returns the exact original lines of a compacted result by its `ref=`.
- No LLM calls and no network access are added. It subclasses Hermes' `ContextCompressor` (a private import). Grep
  patterns with repeats run in a short-lived, stdlib-only `python -I -S` child with a timeout. A telemetry file is
  written only when `telemetry_log` is set.

## Settings

`plugins.entries.tameru.settings.<field>` in `config.yaml`, or `TAMERU_HERMES_<FIELD>` in the environment.
`enabled: false` gives Hermes' built-in behaviour. For a gateway serving many sessions in one process, consider
`store_max_chars: 8000000` (the per-session store of originals defaults to 32M characters). Run with the default
`plugins.isolation: in_process`.

MIT licensed.
