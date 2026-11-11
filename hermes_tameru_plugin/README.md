# Tameru (ためる)

**Smaller tool results. Clearer context changes.**

Tameru is a deterministic, extractive context engine for
[Hermes Agent](https://github.com/NousResearch/hermes-agent). When Hermes shortens
an old tool result, Tameru tries to retain useful excerpts and diagnostic evidence
instead of only a one-line summary.

Run `/tameru` to see what changed and inspect an original when it remains
available. A compatible Hermes Desktop installation can show the same activity
through the panel bundled with this plugin. It does not require a second plugin.

This plugin is the Hermes integration of Tameru (ためる). It carries the harness-agnostic
[Tameru engine](https://github.com/0xWhiteMage/tameru-compaction-system) inside it, so you never install the engine
separately; everything Hermes-specific below lives in this plugin.

## What you get

- **Excerpts and diagnostic checks.** Tameru checks error-template retention when
  choosing an extract or brief. Stronger pressure or retention-budget demotion
  can remove older bodies; a savings percentage is not a guarantee of understanding.
- **A view of context changes.** The activity table distinguishes retained,
  changed and removed results and shows original availability.
- **Read original.** `tameru_read_original` returns original payload lines from
  the bounded per-agent store or a reachable session archive. `tameru_expand`
  remains a compatible alias.
- **Local extraction.** The extractive pass adds no LLM calls or external network
  services. Hermes can still call an LLM to summarize, and prefix changes can
  affect cost. The interface uses Hermes' existing connection.

## Inspection and recovery

Viewing an original in the panel or with `/tameru view REF` does not change what
the model sees. To put an output back in front of the model, **Restore** (the
next few replies) and **Keep** (until you unpin it) are on each row in Desktop,
or `/tameru restore|keep REF`; both change only what is sent in the request, never
saved history, and wait for your next message. If the agent calls the recovery
tool, the returned text is a new result; it does not undo an earlier Hermes
summary.

A `ref=` identifies an original, but does not guarantee it remains available.
Storage limits, credential screening and archive availability can prevent
recovery. Web, browser and MCP output can never be restored or kept.

## Commands

`/tameru` shows what changed and the context gauge. `/tameru help` lists every verb;
`/tameru stats` prints numbers for the chat; `/tameru reset` puts the chat's
self-tuning back to neutral (it lists what it clears and asks first);
`/tameru pin TEXT` keeps a line of your own word for word through every summary;
`/tameru pause|resume` and `/tameru bias save|balanced|detail` are the two
choices that persist with a chat.

## The tool: `tameru_read_original`

The model can request original lines using the reference in a shortened result.
The old name `tameru_expand` is still answered but no longer listed, so the
schema is sent once. With the optional `lazy_tool: on` the tool is left out of
requests until Tameru has shortened something in the chat.

| Argument | What it does |
|---|---|
| `ref` (required) | The value after `ref=` in the result header. |
| `start_line`, `end_line` | Return this line range, 1-based and inclusive. |
| `grep` | Return lines matching this case-insensitive pattern. |
| `max_chars` | Response cap: 12,000 by default, at most 50,000; longer output includes a continuation hint. |

These tools are offered when Hermes permits context-engine tools: with no
restricted toolset, or one including `context_engine`.

## Install

```bash
hermes plugins install tameru
```

Select the engine in the active profile's `config.yaml` and restart Hermes:

```yaml
context:
  engine: tameru
```

Catalog installations use the version pinned by Hermes; a source checkout can
be ahead of that pin. Desktop also uses its normal plugin-enable control.

To have Desktop's Settings list the engine as "Tameru (ためる)", run `scripts/use_display_name.ps1` from the
repository (it renames the plugin folder and `context.engine` together; `-Revert` undoes it).

To return to Hermes' built-in behavior, set `context.engine: compressor` or
set this plugin's `enabled: false`.

## Settings and boundaries

Settings live under `plugins.entries.tameru.settings` in `config.yaml`.
The original store defaults to 32 million characters per agent; gateways serving
many sessions can lower `store_max_chars`. See the
[full configuration reference](https://github.com/0xWhiteMage/hermes-tameru-plugin#configuration).

- Tameru enables window-scaled proactive pruning only when the user has not set
  `compression.proactive_prune_tokens`; explicit host settings retain priority.
- It uses private Hermes compressor seams and checks compatibility at startup.
  A missing seam causes it to defer to Hermes' built-in behavior.
- A potentially slow recovery search uses a short-lived local Python child
  process with a timeout.
- Credential-like originals are not put into Tameru's store. This does not claim
  that Hermes' own transcript or model requests contain no sensitive content.
- Telemetry is off unless `telemetry_log` is configured.
- Use the default `plugins.isolation: in_process`. Under `host` isolation,
  archive-backed recovery after restart is limited.

## What was measured (version 1.9.0)

A replay of scripted agent sessions against real Hermes v0.23.1 (11 session shapes, six varied sessions per cell, Hermes' measured tool block, a deliberately
lossy fake summarizer, prefix-cache pricing; a fact an arm dropped is priced as a re-run of the tool) gives these costs against Hermes as shipped:

| Mean over the shapes | 64K window | 128K | 200K |
|---|---:|---:|---:|
| Tameru | -0.6% | -7.2% | -7.4% |
| Tameru with `lazy_tool: on` | -1.4% | -8.1% | -8.4% |

Hermes keeps 2,603 needed facts and loses 457; Tameru keeps 2,805, can read back 86 more and loses 169. Tameru costs more in some cases: at a 64K window with
`refactor`-like work (+8.4%), with many small results (+3.8% at 64K), and by about 2% in a session that never reaches a compaction (which `lazy_tool` removes).
On Hermes Desktop with Claude Haiku 4.5, one heavy-output task cost $0.2094 with Tameru against $0.2569 for stock (n=2 against 3); a second task was
inconclusive. These are synthetic and small live checks, not a promise about your bill.
[Method, every result and the rejected ideas](https://github.com/0xWhiteMage/hermes-tameru-plugin/blob/main/docs/COST-TUNING-2026-11-10.md).

## Links

- [Repository and full documentation](https://github.com/0xWhiteMage/hermes-tameru-plugin)
- [Changelog](https://github.com/0xWhiteMage/hermes-tameru-plugin/blob/main/CHANGELOG.md)
- [Tameru compaction engine](https://github.com/0xWhiteMage/tameru-compaction-system)

MIT licensed.
