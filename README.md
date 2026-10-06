# hermes-tameru-plugin

[Tameru](https://github.com/0xWhiteMage/tameru-compaction-system) deterministic, extractive context
compaction for [Hermes Agent](https://github.com/NousResearch/hermes-agent), as a standalone plugin (no Hermes
source changes). Version 1.4.0, verified against Hermes `1298c8e` (see [Compatibility](#compatibility)).

## What it does

The plugin is a Hermes context engine (`context.engine: tameru`). It subclasses Hermes' own
`ContextCompressor` and changes one thing: **what a tool result becomes when Hermes decides to demote it.**

Hermes already decides *when* to prune, *which* rows are old enough, and *whether* the pass is worth
committing (proactive-prune thresholds, tail protection, the minimum-reclaim and rearm gates, the
`archive_and_compact` commit, tool-call pairing repair). The plugin hooks Hermes' per-result demotion step, so all
of those gates and the archive still apply, and the plugin never rewrites history on its own schedule.
Where Hermes writes a one-line summary, the plugin runs a deterministic ladder:

1. **Supersession.** A read that a later write/patch of the same path made stale, a re-read of the same path and
   range, a repeated identical call, or an older state snapshot (`git status`, `ls`, a todo view, ...) becomes a
   header-only row with a "superseded by msg N" note.
2. **Extract.** A query-aware extractive cut of the result (the engine's `compress_context`; the query is the task: the
   user's words plus the calls around the result, minus any part that names a file or identifier absent from the
   output, which would only make the engine keep everything). Accepted only if it is not fail-open, its risk is within `max_risk`, it saves at
   least `min_savings`, fits `max_extract_chars`, and **keeps at least one exemplar line of every distinct
   ERROR/FAIL fingerprint the original had**.
   An extract over the cap is retried at fixed budgets; a forced cut must still keep one of the lines the
   query points at.
3. **Brief.** Otherwise a structural brief (error exemplars, query lines, warnings, head and tail; JSON-aware for
   JSON). It starts at `brief_chars` and grows in steps, up to the extract cap, until it holds an exemplar of
   every error fingerprint of the original.
4. **Header.** Otherwise, or for old rows once the retained-extract budget is spent, a header-only row.
5. **Hermes' own line** as the last resort.

Properties:

- **No LLM calls are added.** The ladder is pure Python and byte-deterministic. Hermes' own summarizer still runs
  whenever Hermes decides to run it; the plugin does not call it any more often.
- **Committed bytes do not change between commits.** A row the plugin already rendered is left alone unless
  Hermes itself is committing a pass that demotes it further, so the prompt-cache prefix is not broken by the
  plugin. Hermes' no-op contract (`prune_tool_results_only` returns the input object when nothing changed) is
  preserved, and `compress()` passes `bypass_cooldown` and any other keyword through untouched.
- **Error fingerprints are preserved** by the extract rung (checked plugin-side), by the brief (it keeps error
  exemplars), and, for rows cut down to a header, by the artifact trail below.
- **Every rendered row names its original.** The first line of a rendered row is Hermes' own one-line summary
  followed by `[tameru:<rung> kept K/N lines (KC of TC chars) ... ref=xxxxxxxx]`. Hermes' exit-code/error
  envelope fields (`exit_code`, `error`, `total_lines`) are kept on a compact meta line, so Hermes' own regexes
  still match. Gaps inside extracts use Hermes' own elision marker.
- **Recovery: `tameru_expand`.** The engine adds one tool, `tameru_expand(ref, start_line, end_line, grep,
  max_chars)`, to the tool list (on by default; `expand_tool: false` removes it). Given the 8 hex characters
  after `ref=`, it returns the exact original lines (a range, or only lines matching a regex), as JSON, capped
  at `max_chars` (default 12,000; maximum 50,000) with a continuation hint. Originals come from a per-agent
  in-memory LRU store (`store_max_entries`, `store_max_chars`) and, when the Hermes session database is
  reachable, from the compaction archive, so a ref also resolves after a restart or `--resume` once the row was
  archived. Originals that look like they contain a credential are never kept in the store. A miss tells the
  model to search the session with `session_search`. Refs are computed from the tool-call/message ids plus a hash
  of the original; they are not secret.
- **Artifact trail ledger** (`ledger`, on by default). When Hermes summarizes, the summary gets an exact
  `## Artifact Trail (Tameru, exact)` section derived mechanically from the summarized turns: files touched and
  how (read/write/patch/search, last outcome), commands with their last exit code, and an `Errors:` block with
  the first exemplar line of every distinct error fingerprint of the tool results being summarized or already
  reduced to a header. Paths and commands that look like credentials are shown as `<redacted>`.
- **Lean tail.** In Hermes' `tail_mode: lean`, tail rows that Hermes stubs get a Tameru header-only row (ref
  kept) instead of a stub that loses it.
- **`<persisted-output>` rows** (Hermes' own spill blocks) keep their "Full output saved to" path in the header.
- Exempt from the ladder (Hermes' own handling applies): `skill_view`, `skills_list`, `skill_manage`,
  `clarify`, `todo`, `todo_list`, `memory`, `session_search`, `tameru_expand`, `delegate_task`,
  `cronjob_manage`, `process_manage`, refused/blocked results, and anything in `exempt_tools`. Results shorter
  than `min_tool_chars` take Hermes' path.

## Requirements

- **Python >= 3.11** (the package declares `requires-python = ">=3.11"`; Hermes itself needs 3.11 to 3.14).
- Hermes Agent with the context-engine plugin API. The plugin hooks private `ContextCompressor` seams; see
  [Compatibility](#compatibility).
- No runtime dependencies (standard library only; `pyyaml` is used only as a fallback config reader if
  Hermes' own config loader is unavailable and it happens to be installed).

## Install

### Option A: user plugin directory (no pip)

```bash
git clone https://github.com/0xWhiteMage/hermes-tameru-plugin
cp -r hermes-tameru-plugin/hermes_tameru_plugin ~/.hermes/plugins/tameru
```

### Option B: pip (entry-point discovery)

```bash
pip install git+https://github.com/0xWhiteMage/hermes-tameru-plugin
```

The package registers the `hermes_agent.plugins` entry point, so Hermes discovers it. **Discovery is not
enabling**: Hermes loads pip-installed plugins only when they are in `plugins.enabled`.

### Enable

Select the engine in `~/.hermes/config.yaml`:

```yaml
context:
  engine: tameru
```

For **Option A** that is all: Hermes loads a user-installed context engine from `~/.hermes/plugins/tameru` when
`context.engine` names it (no `plugins.enabled` entry needed). For **Option B** the plugin is found through the
general plugin system, so it must also be enabled:

```bash
hermes plugins enable tameru
```

or `plugins: {enabled: [tameru]}` in the same file.

Restart Hermes. Check `get_status()["tameru"]` (see [Status](#status-and-isolation)) to confirm the seams were
found.

## Configuration

Every setting is read, highest priority first, from: the environment variable `TAMERU_HERMES_<FIELD>`
(field name upper-cased), `ctx.get_config(<field>)` when the host offers it, then the
`plugins.entries.tameru.settings` mapping of the Hermes config (a legacy `plugins.entries.tameru.config` mapping
is read as a fallback). Booleans accept `1/0/true/false/yes/no/on/off`; list settings take a YAML list or, in an
environment variable, a comma-separated string. A value that does not parse is ignored with a warning (listed in
`get_status()["tameru"]["warnings"]`) and the next layer or the default applies. Settings are read when the
engine is created: restart Hermes after changing them.

```yaml
plugins:
  entries:
    tameru:
      settings:
        max_extract_chars: 4000
        exempt_tools: [my_custom_tool]
```

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Kill switch. `false` makes the engine behave as stock Hermes (see below). |
| `min_tool_chars` | `800` | Results shorter than this take Hermes' own one-line path. |
| `max_extract_chars` | `6000` | Cap on a rendered extract row, header and markers included. |
| `brief_chars` | `1200` | Starting size of a brief row (it grows up to `max_extract_chars` while an error fingerprint is missing). |
| `brief_share` | `0.0` | Start the brief at this share of the result instead, when larger (0 to <1). `0.25` keeps more answers per row but, in the replay, fewer older rows keep a body. |
| `max_risk` | `high` | Highest engine `compression_risk` an extract may have (`low`, `medium`, `high`). The engine rates almost any cut past about 80% "high", so a lower ceiling mostly pushes rows to the query-blind brief. |
| `min_savings` | `0.10` | An extract must be at most `(1 - min_savings)` of the original (0 to <1). |
| `exempt_tools` | `[]` | Extra tool names left to Hermes' own handling (added to the built-in exempt set). |
| `protect_patterns` | `[]` | Regexes; lines matching them are pinned in extracts. |
| `expand_tool` | `true` | Add `tameru_expand` to the tool list. |
| `store_max_entries` | `512` | Originals kept in memory for `tameru_expand` (LRU). |
| `store_max_chars` | `32000000` | Total characters of originals kept in memory (LRU). |
| `pass_char_budget` | `4000000` | Original characters the ladder may process in one pass; rows beyond it use Hermes' line. |
| `retained_extract_budget_chars` | `6000` | Total size of extract/brief bodies rendered rows may keep. Committed rows keep theirs newest first; a new row gets what is left, else a header. Rows beyond it step down to a header (ref kept), only inside a pass Hermes already commits. |
| `prune_tail` | `tokens` | Tail protected by the proactive prune: `tokens` (the shorter of `protect_last_n` and the full compaction's tail token budget, and pass 4 also runs) or `count` (Hermes' `protect_last_n` messages, stock behaviour). |
| `supersession` | `true` | Enable structural supersession. |
| `ledger` | `true` | Add the artifact trail to Hermes' compaction summary. |
| `telemetry_log` | `""` | Path of a JSONL file for render events; empty means off. |
| `default_proactive_prune_tokens` | `48000` | Value used for `compression.proactive_prune_tokens` only if you did not set it. |
| `default_proactive_prune_min_result_chars` | `2000` | Value used for `compression.proactive_prune_min_result_chars` only if you did not set it. |

Environment variable names: `TAMERU_HERMES_ENABLED`, `TAMERU_HERMES_MIN_TOOL_CHARS`,
`TAMERU_HERMES_MAX_EXTRACT_CHARS`, `TAMERU_HERMES_BRIEF_CHARS`, `TAMERU_HERMES_MAX_RISK`,
`TAMERU_HERMES_MIN_SAVINGS`, `TAMERU_HERMES_EXEMPT_TOOLS`, `TAMERU_HERMES_PROTECT_PATTERNS`,
`TAMERU_HERMES_EXPAND_TOOL`, `TAMERU_HERMES_STORE_MAX_ENTRIES`, `TAMERU_HERMES_STORE_MAX_CHARS`,
`TAMERU_HERMES_PASS_CHAR_BUDGET`, `TAMERU_HERMES_RETAINED_EXTRACT_BUDGET_CHARS`, `TAMERU_HERMES_PRUNE_TAIL`,
`TAMERU_HERMES_SUPERSESSION`, `TAMERU_HERMES_LEDGER`, `TAMERU_HERMES_TELEMETRY_LOG`,
`TAMERU_HERMES_DEFAULT_PROACTIVE_PRUNE_TOKENS`, `TAMERU_HERMES_DEFAULT_PROACTIVE_PRUNE_MIN_RESULT_CHARS`.

### `compression.*` is mirrored

Hermes hands a plugin engine none of the user's `compression:` section, so the plugin copies it into the
compressor's constructor itself, using the same table as Hermes' `_parse_compression_config`:

| `compression.` key | `ContextCompressor` argument |
|---|---|
| `threshold` | `threshold_percent` |
| `protect_first_n` | `protect_first_n` |
| `protect_last_n` | `protect_last_n` |
| `target_ratio` | `summary_target_ratio` |
| `abort_on_summary_failure` | `abort_on_summary_failure` |
| `threshold_tokens` | `threshold_tokens_cap` |
| `proactive_prune_tokens` | `proactive_prune_tokens` |
| `proactive_prune_min_result_chars` | `proactive_prune_min_result_chars` |
| `proactive_prune_min_reclaim_tokens` | `proactive_prune_min_reclaim_tokens` |
| `min_tail_user_messages` | `min_tail_user_messages` |
| `tail_mode` | `tail_mode` |
| `model_thresholds` | `model_thresholds` |

Two of them have plugin defaults: unless your own config file sets them, `proactive_prune_tokens` is 48,000 and
`proactive_prune_min_result_chars` is 2,000 (the `default_*` settings above). Setting them explicitly wins. An
argument the running Hermes does not accept is dropped with a warning. The plugin also applies Hermes' Codex
threshold autoraise when the model is selected.

## Status and isolation

`engine.get_status()["tameru"]` reports (plain JSON):

- `version`, `engine_version`, `enabled`;
- `seams`: which Hermes hooks were found (`demote`, `pressure`, `lean_tail`, `augment`, `compress_kw`) and
  `reasons` for each missing one;
- `expand_injected`: whether `tameru_expand` has been added to a tool list;
- `store`: `entries` and `chars` of originals held;
- `isolated`: see below;
- `telemetry`: counters (below);
- `warnings`: configuration problems found at start-up.

**In-process requirement.** The engine needs to run in the Hermes process, which is Hermes' default
(`plugins.isolation: in_process`). With `plugins.isolation: host` Hermes gives the plugin an opaque placeholder
instead of its session database. The engine detects that (`isolated: true`) and never reads the archive: in that
mode `tameru_expand` serves only the originals held in the plugin's own memory, so refs do not resolve after a
restart. Other behaviour under `host` isolation (the seams patch private Hermes classes) is not tested here.

## Telemetry

Counters are content-free (counts, tool names, rungs and hashes; never message text) and reported under
`get_status()["tameru"]["telemetry"]`:

- `events`: per event a `count` and a per-label breakdown. Events: `render` (labels `rung`, `tool`, and
  `demoted` for rows aged down), `parent_line` (rows left to Hermes' line), `lean_tail`, `escalation`
  (see below), `budget_skip` (pass budget exhausted), `error` (labels `where`: `demote`, `lean_tail`,
  `ledger`; the plugin fell back to Hermes' behaviour for that row);
- `chars`: `before`, `after`, `saved` characters across rewritten rows;
- `time`: seconds and calls spent inside the engine's public calls.

With `telemetry_log` set, each rendered row also appends one JSON line (`event`, `rung`, `tool`, `ref`,
`chars_before`, `chars_after`) to that file. A logging error never affects a prune.

*Escalation:* if Hermes declines to commit a prune (its minimum-reclaim gate) although the plugin rendered rows,
the pass is retried once with header-only rungs, which reclaim more.

## Kill switch and fallbacks

- `TAMERU_HERMES_ENABLED=0` (or `enabled: false`) makes every override defer to Hermes: no ladder, no ledger, no
  `tameru_expand`, and `compression.*` defaults are Hermes' own. This is the stock `ContextCompressor`.
- **Missing Hermes seam.** At start the engine probes the Hermes hooks it needs (`_demote_tool_result_at` and its
  callers, the lean-tail and summary-augmentation hooks, `compress(bypass_cooldown=...)`). If the demotion seam is
  missing or has a different signature, the engine runs as stock Hermes and says why in
  `get_status()["tameru"]["seams"]["reasons"]`; a missing optional seam disables only that feature. An exception
  inside the ladder for one row falls back to Hermes' line for that row and is counted under `error`.
- Without Hermes importable the module still imports, but the engine cannot be constructed.
- The engine class is built from `agent.context_compressor` when `engine.py` is first imported. If a pip-installed
  plugin is imported while that Hermes module is itself still importing (observed only when a script imports
  `agent.context_compressor`, `agent.agent_init` or `gateway.run` before anything else; `hermes` and `hermes_cli`
  imports are fine), the engine is unavailable for that process and Hermes falls back to its built-in compressor.

## Limitations

- The plugin hooks private Hermes methods. It is tested against Hermes `1298c8e` (and nightly against `main`);
  a Hermes refactor can disable it (safely, as above) until the plugin is updated.
- It is extractive: an extract or brief keeps lines, not meaning. Facts that were only in a cut region are gone
  from the context until the model calls `tameru_expand` (or re-runs the tool). In the replay the "recall" probe
  category beats stock only narrowly (2 of 6 and 1 of 6 facts against 0 of 6).
- A row whose original was never stored (store evicted, credential-looking content, or a restart without a
  reachable session archive) cannot be expanded.
- The final request in the replay is still larger than stock's (123 KB against 84 KB, 1.46x): two large late
  results arrive after the last cache-breaking rewrite and stay verbatim. Total input and cache-priced cost are
  lower than stock's.
- Replay numbers come from one scripted 30-turn session with a fake lossy summarizer and Hermes' rough token
  estimator, not from live models. See [tests/eval/README.md](tests/eval/README.md) and
  [docs/compaction-research.md](docs/compaction-research.md) section 7 for the measured results and their limits.
- Output whose extract cannot shrink under `max_extract_chars` (big logs, `grep` output where every line matches,
  a pytest failure with a long captured log) ends as a brief. The brief keeps every error template, but a
  specific matching line can be cut: in the QA set of agent outputs, 2 of 14 answer strings were lost (a `grep`
  hit, a pytest assertion line), against 1 for plugin 1.3.0 and all 14 for stock Hermes
  ([report](docs/REPORT-1.4.0.md)).
- Results under `min_tool_chars` (800) get Hermes' own one-line summary, as in stock Hermes.
- Re-sync of the vendored engine invalidates the saved replay baselines on purpose; they must be re-recorded.

## Compatibility

- Hermes pinned in CI: `1298c8e74baa73e1a2b90124228d017261ac6bc4`; the full suite runs against it on Python 3.14
  and with a stub on Python 3.11 to 3.14. A nightly job runs it against Hermes `main` and may fail without
  blocking.
- Upgrading from 1.3.0: see [CHANGELOG.md](CHANGELOG.md).

## Uninstall

1. Set `context.engine: compressor` (or remove the `context.engine` line).
2. Remove `tameru` from `plugins.enabled` (`hermes plugins disable tameru`).
3. Delete `~/.hermes/plugins/tameru/`, or `pip uninstall hermes-tameru-plugin`.

Rows already rewritten stay in session history as ordinary text; their `ref=` can no longer be expanded once the
plugin is gone, except through the session archive and `session_search`.

## How the vendored engine stays in sync

`hermes_tameru_plugin/tameru/` is a copy of `tameru-compaction-system/src/tameru/`. The upstream commit it was
copied from is recorded in `hermes_tameru_plugin/tameru/__init__.py` as `VENDORED_FROM`. Upstream owns every file
under `tameru/` except `__init__.py`; this repo owns `__init__.py` and `plugin.yaml` (the sync only stamps the
`version:` and `Tameru X.Y.Z` text in the manifest). Never edit vendored files here; change upstream and re-sync:

```bash
# in the upstream checkout
python scripts/sync_to_harness.py <this_repo>/hermes_tameru_plugin/tameru --manifest ../plugin.yaml
# read-only drift check (exit 1 if a sync would change anything)
python scripts/sync_to_harness.py <this_repo>/hermes_tameru_plugin/tameru --manifest ../plugin.yaml --check
```

After a sync, update `VENDORED_FROM`, re-record the replay baselines and read the diff
([docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)). The upstream commits behind 1.4.0 are exported in
[docs/upstream-patches/](docs/upstream-patches/README.md).

## More

- [CHANGELOG.md](CHANGELOG.md)
- [docs/REPORT-1.4.0.md](docs/REPORT-1.4.0.md): what changed from 1.3.0, measured against 1.3.0 and stock Hermes, QA findings.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): tests, real-Hermes setup, replay harness, CI.
- [docs/compaction-research.md](docs/compaction-research.md): survey, gap analysis (G1 to G16) and results.

## License

MIT, same as upstream Tameru. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
