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

The UI's original viewer is read-only: it does not restore past model context.
If the agent calls the recovery tool, the returned text can be used as a new
result; this does not undo an earlier Hermes summary.

A `ref=` identifies an original, but does not guarantee it remains available.
Storage limits, credential screening and archive availability can prevent
recovery. Persistent Restore and Keep controls are not implemented.

## The tool: `tameru_read_original`

The model can request original lines using the reference in a shortened result.
The alias `tameru_expand` accepts the same arguments.

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

## Measured on the 1.7.0 candidate (first released in 1.8.0)

A replay compared 60 scripted sessions per arm at six context-window sizes
against Hermes `a0a49926`:

| Metric | Stock Hermes | With Tameru |
|---|---:|---:|
| Turns failing after overflow recovery | 76 | 0 |
| Required snippets present in final requests | 705 / 1,044 | 859 / 1,044 |
| Fake-summary calls | 106 | 48 |

Mean paired simulated input-cost change was **−3.21%**; Tameru cost more in
12 of 60 pairs. These are synthetic retention and character-priced cache checks,
not live task-success or provider-billing measurements.
[Full report and raw captures](https://github.com/0xWhiteMage/hermes-tameru-plugin/blob/main/docs/REPORT-1.7.0.md).

Live Electron validation remains pending; the bundled panel's DOM behavior tests
pass. Pressure-aware task preferences are a research proposal, not a shipped
feature of this candidate.

## Links

- [Repository and full documentation](https://github.com/0xWhiteMage/hermes-tameru-plugin)
- [Changelog](https://github.com/0xWhiteMage/hermes-tameru-plugin/blob/main/CHANGELOG.md)
- [Tameru compaction engine](https://github.com/0xWhiteMage/tameru-compaction-system)

MIT licensed.
