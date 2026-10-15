# Tameru (貯める)

**Big tool outputs, kept small. Nothing lost.**

Tameru is a context engine for [Hermes Agent](https://github.com/NousResearch/hermes-agent). When a long session
fills up with old tool results (logs, test runs, file reads, search output), Hermes normally replaces each one
with a one-line summary. Tameru keeps the lines that matter instead, always keeps every distinct error, and lets the
agent fetch the exact original back whenever it needs it.

*Tameru* (貯める) is Japanese for "to store up": what leaves the context is kept aside, not thrown away.

## What you get

- **Longer sessions before the context fills up.** A 451-line server log drops to 33 lines, 89% fewer characters.
- **Errors stay in view.** At least one line of every distinct error (`ERROR`, `FAIL`, tracebacks...) is kept, so
  the agent does not forget what broke.
- **Nothing is lost.** Every shortened result carries a `ref=`. The agent can call `tameru_expand` to get the exact
  original lines back, all of them or only the lines matching a search.
- **No extra cost or risk.** No LLM calls and no network access are added; the trimming is plain, deterministic
  Python. Hermes still decides when to trim and which results.

## How it works

1. A tool returns a large result, and the agent carries on working.
2. When Hermes trims old tool results to make room, Tameru writes a short version in its place: the lines that
   match the task, every distinct error, and a header with a `ref=`.
3. If the agent later needs something that was cut, it calls `tameru_expand` with that ref and gets the original
   text back, word for word.

## Example

A `terminal` call returned 451 lines of a payment service log (49,431 characters). In context it becomes 33 lines
(5,221 characters). The header and some of the kept lines:

```text
[terminal] {"command": "tail -n 450 /var/log/payments/gateway.log"} (49,431 chars) [tameru:extract kept 33/451 lines (5,012 of 48,930 chars) omitted: log 443 ref=e0d1a349035b54c9]
[×3] 2025-03-14T02:14:22.500Z FATAL worker.pool WorkerCrash: worker-4 exited with signal 9 (out of memory)
[×3] 2025-03-14T02:14:59.909Z ERROR http.server UpstreamBadGateway: 502 from inventory-svc POST /v2/reserve trace_id=d3b6ebf3
[×6] 2025-03-14T02:15:18.152Z ERROR payments.gateway PaymentGatewayTimeout: upstream stripe-proxy timed out after 2318ms (order_id=ORD-27624, attempt=3)
[×3] 2025-03-14T02:17:40.646Z ERROR ledger.writer LedgerWriteError: duplicate key value violates unique constraint "ledger_entries_pkey" (entry_id=8fa7bdb1-9f49-6417-11ce-d79c33086062)
```

`[×6]` means six lines of the same kind, shown once. To see every timeout, the agent calls `tameru_expand` with:

```json
{"ref": "e0d1a349035b54c9", "grep": "timeout"}
```

and gets the 6 matching lines back exactly as the tool printed them, with their line numbers.

## The tool: `tameru_expand` (展開, *tenkai*: "unfold")

Gives back the full, exact text of a tool result that Tameru shortened. The model calls it on its own when it needs
something that was cut; you never have to call it yourself.

| Argument | What it does |
|---|---|
| `ref` (required) | The code after `ref=` in the shortened result's header. |
| `start_line`, `end_line` | Return only this range of lines (1-based, inclusive). |
| `grep` | Return only the lines matching this case-insensitive pattern. |
| `max_chars` | Cap on the answer (default 12,000, at most 50,000). A longer answer says where to continue. |

The tool is available when Hermes offers context-engine tools: with no toolset restriction, or with one that includes
`context_engine`.

## Install

```bash
hermes plugins install tameru
```

Then select the engine in `~/.hermes/config.yaml` and restart Hermes:

```yaml
context:
  engine: tameru
```

To go back to Hermes' built-in behaviour, set `context.engine: compressor`, or set the plugin's `enabled: false`.

## Settings

The defaults work as they are. Settings go under `plugins.entries.tameru.settings` in `config.yaml`, or in
`TAMERU_HERMES_<SETTING>` environment variables. For a gateway serving many sessions in one process, consider
`store_max_chars: 8000000` (the per-session store of originals defaults to 32 million characters). The full list is
in the [repository README](https://github.com/0xWhiteMage/hermes-tameru-plugin#configuration).

## Good to know

- **It turns on early trimming.** Hermes ships its proactive tool-result pruning off. Unless you set
  `compression.proactive_prune_tokens` yourself, Tameru turns it on with a trigger sized to the model's context
  window. Your own `compression.*` values always win.
- **It builds on a private part of Hermes.** It extends Hermes' `ContextCompressor` and checks at startup that the
  parts it needs are there. If a Hermes update changes them, it steps aside and Hermes compacts as usual.
- **It runs one small helper process.** A `tameru_expand` search pattern that could run slowly is checked in a
  short-lived local Python process with a time limit.
- **No data leaves your machine.** No network calls and no credentials. Results that look like they contain a
  secret are never stored. A telemetry file is written only if you set `telemetry_log`.
- **Keep the default `plugins.isolation: in_process`.** Under `host` isolation, refs work only until Hermes
  restarts.

## Measured

A replay of 60 scripted sessions (5 session types, 32K to 256K context windows) against Hermes with its default
settings:

| | Hermes as shipped | With Tameru |
|---|---|---|
| Turns that failed because the context overflowed | 76 | **0** |
| Key facts still in context at the end | 705 | **854** |
| Full compactions (LLM summary calls) | 106 | **47** |
| Cost per answered turn | | **7.7% lower** on average (lower in 34 sessions, higher in 8) |

These are scripted sessions, so they measure what stays in context and what it costs, not task success with a live
model. Method and full results:
[REPORT-1.5.0](https://github.com/0xWhiteMage/hermes-tameru-plugin/blob/main/docs/REPORT-1.5.0.md).

## Links

- [Repository and full documentation](https://github.com/0xWhiteMage/hermes-tameru-plugin)
- [Changelog](https://github.com/0xWhiteMage/hermes-tameru-plugin/blob/main/CHANGELOG.md)
- [Tameru compaction engine](https://github.com/0xWhiteMage/tameru-compaction-system)

MIT licensed.
