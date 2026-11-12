# Two repositories, one product

Tameru (ためる) is split in two on purpose. The engine works on any agent harness. The plugin makes it a first-class
part of Hermes. Keeping them apart is what lets the engine stay small and portable while the Hermes experience keeps
growing.

| | **Engine** [`tameru-compaction-system`](https://github.com/0xWhiteMage/tameru-compaction-system) | **Hermes plugin** `hermes-tameru-plugin` (this repository) |
|---|---|---|
| Purpose | Deterministic, query-aware compaction of tool outputs, logs and documents | Everything that makes Tameru work well *inside Hermes* |
| Runs on | Any harness: Python API, CLI, or the generic transcript adapter for OpenAI-style `role`/`content` messages | Hermes Agent only: Desktop, CLI, TUI and messaging |
| Contains | Extraction and reducers, structured-format handling, folding, supersession, Unicode handling, credential screening for recovery storage, the benchmarks | The Hermes context-engine subclass, the adaptive policy and self-tuning, Restore and Keep, the per-turn note, the Desktop panel, the `/tameru` commands, per-chat state, security gates for chat surfaces, packaging for the Hermes catalogue |
| Depends on | The Python standard library | Hermes' plugin interfaces and a few private seams (listed in the Hermes validator output); the engine, vendored |
| Knows about | Text in, smaller text out | Chats, sessions, tools, the prompt cache, Hermes settings and surfaces |
| Version | Semantic; `ENGINE_VERSION` | Semantic, separate from the engine's (1.9.1 vendors engine 1.9.0; `/tameru stats` shows both) |

**The one rule between them.** `hermes_tameru_plugin/tameru/` is a byte-for-byte copy of the engine's `src/tameru/`
and is never edited here. An engine fix is made in the engine repository first, released, and re-vendored
(see [RELEASING.md](RELEASING.md) and [DEVELOPMENT.md](DEVELOPMENT.md#vendoring-the-engine)). The plugin repository
owns only `tameru/__init__.py` in that folder, which records `VENDORED_FROM` (the engine commit).

## Where does my change belong?

| If it is about… | …it goes in |
|---|---|
| What gets kept or dropped from a tool output, a format Tameru does not understand, savings numbers, recovery storage | the **engine** |
| Making the engine usable from another framework (message shapes, task-derived queries) | the **engine** |
| When Tameru acts, how it tunes itself, the Desktop panel, hover cards, `/tameru` commands | this **plugin** |
| Restore, Keep, the per-turn note, usage numbers, what a chat surface may do | this **plugin** |
| Hermes settings, the catalogue entry, install and rollback | this **plugin** |

## Notes for readers of the engine code

- The engine has **no dependency on Hermes**. A few modules carry the name for history: `hermes_extractive_engine.py`
  and `agent_extractive_engine.py` are backwards-compatible aliases of the generic `transcript` adapter, and the
  engine recognises Hermes' tool-result wrapper as one of several input formats.
- Using Tameru outside Hermes needs only the engine: see its `docs/INTEGRATION.md`.
- Hermes users never need to install the engine separately; the plugin carries it.

## Releasing, in short

1. Engine change lands and is released in the engine repository (version bumped there).
2. This repository re-vendors it, sets `VENDORED_FROM`, and bumps its own version if its behaviour changed; the two versions need not match.
3. One catalogue pull request to Nous per release. Details in [RELEASING.md](RELEASING.md).
