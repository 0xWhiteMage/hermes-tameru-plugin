# hermes-tameru-plugin

[Tameru](https://github.com/0xWhiteMage/tameru-compaction-system) deterministic
context compaction for [Hermes Agent](https://github.com/NousResearch/hermes-agent) —
installed as a standalone plugin, no Hermes source tree changes.

Tameru is an extractive compactor: it keeps the exact bytes that matter and
drops provably redundant context. Its pruning pass makes **no model calls**,
generates no summaries, is byte-identical across runs, and fails open on
anything ambiguous.

Two scoping notes for this integration:

- The engine wraps Hermes' built-in summariser, which remains an LLM call —
  Tameru adds none. Its timing gate suppresses only the Tameru prune step;
  the parent's subsequent compaction is unaffected.
- Dropped tool payloads are **not** persisted or recoverable here: the
  adapter runs with CCR and citations off, since a live tool-prune has no
  retrieval path. (CCR reversibility exists in the standalone API.)

## Install

### Option A — user plugin directory (no pip)

```bash
git clone https://github.com/0xWhiteMage/hermes-tameru-plugin
cp -r hermes-tameru-plugin/hermes_tameru_plugin ~/.hermes/plugins/tameru
```

### Option B — pip (entry-point discovery)

```bash
pip install git+https://github.com/0xWhiteMage/hermes-tameru-plugin
```

The package exposes the `hermes_agent.plugins` entry point; Hermes discovers
it automatically.

## Enable

In `~/.hermes/config.yaml`:

```yaml
context:
  engine: tameru
```

Restart Hermes. The engine wraps the built-in summariser: Tameru prunes tool
payloads deterministically first, the stock compressor still handles
conversational compaction, and a post-check returns the pruned transcript
untouched if summarisation would lose query facts or bulky tool content.

## Uninstall

Remove `~/.hermes/plugins/tameru/` (or `pip uninstall hermes-tameru-plugin`)
and set `context.engine: compressor`.

## How it stays in sync

`hermes_tameru_plugin/tameru/` is vendored from
`tameru-compaction-system/src/tameru/` and updated with:

```bash
python scripts/sync_to_harness.py <this_repo>/hermes_tameru_plugin/tameru \
    --manifest <this_repo>/hermes_tameru_plugin/plugin.yaml
```

`plugin.yaml` and both `__init__.py` files are owned by this repo; everything
else under `tameru/` is upstream-owned.

## License

MIT — same as upstream Tameru.
