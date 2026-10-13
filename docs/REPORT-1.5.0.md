# Tameru for Hermes 1.5.0: report

Date: 2026-10-13. Plugin `claude/funny-albattani-gac36w` (merge `559f61d` and later), engine Tameru 1.5.0 (`933aa33`).
Verified against Hermes `a0a4992` (CI pin), `63d0b94` and `6e7b83a2f1` (main on this date).

## 1. Summary

1.5.0 was a cost-anomaly and red-team round. The question was why the plugin cost *more* than Hermes in some
places (128K, 200K, many small outputs) and did nothing at 256K, and whether it is safe to serve.

- **No turn fails on a context overflow any more.** On a realistic replay (section 3), Hermes as shipped fails 76
  turns across the 60 measured cells (all at 32K-48K windows); the plugin as Hermes users install it today (1.4.1)
  fails 35; 1.5.0 fails **0**.
- **It keeps much more of the session.** Over the same 60 cells: gold facts 854 against Hermes' 705 (+21%), error
  templates 912 against 510 (+79%), full compactions (each an LLM summary call) 47 against 106 (-56%), re-fetches of
  an earlier tool call 151 against 228.
- **It is cheaper in 34 of 60 cells and dearer in 8**, by more than 0.5% of cost per answered turn; the mean is
  -7.7%. The dearer cells are the "many small outputs" scenario at 32K-64K (+1.6% to +8.9%) and the refactor
  scenario at 48K (+3.2% / +4.2%), all with equal or better retention.
- **On large windows it now equals Hermes where Hermes has nothing to do**, instead of pruning a session that would
  never have been compacted and losing facts there (1.4.1 at 200K/256K: 23-32% cheaper, but 13/17 gold facts and 1/7
  error templates where Hermes keeps everything).
- **Serving:** seven integration issues and six engine issues fixed (section 5); no cross-session leak found.

### What the earlier odd cells were

| Cell | 1.4.1 behaviour | Cause | 1.5.0 |
|---|---|---|---|
| 128K +4%, on-call 200K +24% | pruned once, then never again while Hermes' own compaction reset the context | Hermes re-arms the prune only after the prompt regrows by the whole trigger (about 0.7 of the threshold) | prunes again after 20% regrowth, only if that leaves the prompt at or below 30% of the threshold: -4.1% and -7.4% |
| 200K lost 3 facts | pruned a session that peaks at 0.731 of the threshold and is never compacted | trigger at 0.70 | trigger at 0.75: identical to Hermes, 17/17 |
| 256K "same as Hermes" | nothing to do: no scenario except on-call reaches the trigger | expected | unchanged; on-call 256K -36% |
| 32K/48K overflows | the plugin's prune never ran below ~57K windows (its 48K floor sat above the window) | trigger floor | `small_window_prune_ratio` 0.60 of the threshold below ~57K: 0 failed turns |
| 32K compacted 2.5x as often | 64K-sized retained budgets took twice their share of a 32K window | fixed budgets | budgets scale below 64K |
| Many small outputs +2-9% | nothing is prunable (all results under 8,000 chars); the summary carries the artifact trail and anchor lines | trail and Tameru refs in the summary prompt | refs and header detail no longer reach the summarizer (summary prompt -15 to -17%); the trail stays because it doubles the error templates kept |

## 2. Final matrix (seed 0)

Cost is per answered turn relative to Hermes as shipped (`stock_defaults`; failed turns are unbilled, so raw cost
would reward failing). g = gold facts, e = error templates, c = full compactions, f = failed turns. Seed 1 has the same
shape (raw data: the replay scripts in `tests/eval`).

| Scenario | Window | Hermes as shipped | 1.4.1 (catalogue today) | 1.5.0 |
|---|---|---|---|---|
| default | 32K | g5 e0 c3 f6 | +2.4% g9 e7 c3 f5 | **-4.9%** g11 e7 c1 f0 |
| default | 48K | g5 e0 c2 | +3.4% g6 e7 c2 | **-9.3%** g12 e7 c0 |
| default | 64K | g6 e0 c2 | -5.7% g13 e7 c1 | -2.9% g13 e7 c1 |
| default | 128K | g9 e0 c1 | -5.6% g13 e1 c0 | -4.1% g14 e7 c0 |
| default | 200K | g17 e7 c0 | -31.5% g13 e1 | 0.0% g17 e7 |
| default | 256K | g17 e7 c0 | -31.5% g13 e1 | 0.0% g17 e7 |
| on-call | 32K | g11 e0 c6 f6 | -4.2% g12 e11 c6 f6 | **-37.6%** g14 e16 c2 f0 |
| on-call | 48K | g6 e0 c4 f4 | -0.3% g12 e10 c3 | **-43.9%** g18 e17 c0 f0 |
| on-call | 64K | g10 e0 c4 | -5.0% g12 e10 c2 | -11.1% g12 e14 c2 |
| on-call | 128K | g10 e0 c2 | -10.6% g17 e13 c0 | -12.9% g17 e17 c0 |
| on-call | 200K | g13 e0 c1 | -15.6% g17 e13 c0 | -7.4% g17 e17 c0 |
| on-call | 256K | g12 e0 c1 | -47.0% g17 e13 c0 | -36.0% g17 e17 c0 |
| refactor | 32K | g9 e0 c4 f5 | -0.6% g11 e9 c4 f5 | **-16.7%** g14 e9 c1 f0 |
| refactor | 48K | g10 e0 c3 | +1.8% g9 e9 c3 | +3.2% g13 e9 c1 |
| refactor | 64K | g13 e0 c2 | +3.5% g14 e9 c1 | -1.4% g14 e9 c1 |
| refactor | 128K | g13 e0 c1 | -17.2% g14 e1 c0 | -19.0% g14 e9 c0 |
| refactor | 200K / 256K | g17 e9 c0 | -23.4% g14 e1 | 0.0% g17 e9 |
| many small outputs | 32K | g5 e10 c5 | +7.1% g5 e11 c6 | +8.9% g6 e11 c6 |
| many small outputs | 48K | g5 e10 c3 | +2.0% g6 e13 c3 | +1.6% g6 e13 c3 |
| many small outputs | 64K | g9 e10 c2 | +20.0% g14 e18 c1 | +1.7% g9 e21 c2 |
| many small outputs | 128K+ | g18 e38 c0 | +1.9% g17 e22 | 0.0% g18 e38 |
| non-English | 32K | g12 e15 c2 f17 | -19.0% g12 e17 c5 | **-19.4%** g14 e17 c2 f0 |
| non-English | 48K | g12 e15 c2 | -8.4% g11 e16 c3 | -3.6% g14 e17 c1 |
| non-English | 64K | g12 e9 c2 | -2.8% g14 e17 c1 | -2.7% g14 e17 c1 |
| non-English | 128K | g12 e6 c1 | -14.0% g13 e11 c0 | -10.0% g13 e16 c0 |
| non-English | 200K / 256K | g15 e17 c0 | -27.2% g13 e11 | 0.0% g15 e17 |

Totals, seeds 0 and 1 (60 cells per engine):

| | Hermes as shipped | 1.4.1 | 1.5.0 |
|---|---|---|---|
| Failed turns (context overflow) | 76 | 35 | **0** |
| Gold facts kept | 705 | 761 | **854** |
| Error templates kept | 510 | 659 | **912** |
| Full compactions | 106 | 88 | **47** |
| Re-fetch calls | 228 | 188 | **151** |
| Cost per answered turn, mean vs Hermes | - | -9.4% | -7.7% |
| Cells dearer than Hermes (> 0.5%) | - | 19 | 8 |

1.4.1's lower mean cost comes mostly from 200K/256K sessions that Hermes never compacts: it pruned them anyway and
lost facts and error templates Hermes keeps. A user who prefers that trade can set
`compression.proactive_prune_tokens: 48000` (the old fixed trigger); the plugin never overrides a user value.

## 3. Measuring honestly: the realistic replay

The replay used to run a whole session in seconds of wall-clock time. Hermes' no-op and anti-thrash backoffs are
300 s of wall-clock, so after one no-op neither engine compacted again, and at 32K the harness billed requests up to
2.1x the window, which a provider rejects. That made the plugin look 17-28% cheaper at 32K than it was, and made stock
look like it compacted rarely when it had in fact stalled.

The harness now runs on a simulated clock (20 s per request: one agentic round trip) and mirrors Hermes'
context-overflow recovery (`agent/turn_overflow.py`): an over-window request is rejected unbilled and forces a
compression with the cooldown bypassed, three attempts per turn, then the turn fails. 64K and larger windows give
identical results on both harnesses; `--old-harness` reproduces every old baseline byte for byte.

Sensitivity: 32K results depend on the clock rate (at 5 s per request Hermes fails more turns, at 60 s fewer); the
plugin has 0 failed turns at 5, 20, 60 and 300 s. Overflow is judged with Hermes' rough token estimate, which can be
~30% off a real tokenizer. Hermes' preflight loop (several compressions per request) is not modelled.

## 4. What changed

| Area | Change | Setting (default) |
|---|---|---|
| Large windows | prune again after 20% regrowth of the threshold, only if it leaves the prompt at or below 30% of it | `prune_regrowth_ratio` (0.20), `reprune_target_ratio` (0.30) |
| Large windows | first prune at 0.75 of Hermes' threshold (was 0.70) | `default_proactive_prune_ratio` (0.75) |
| Small windows | below ~57K the trigger is 0.60 of the threshold (the 48K floor sat above the window) | `small_window_prune_ratio` (0.60) |
| Small windows | retained-extract and error budgets scale below a 64K window | `small_window_threshold_tokens` (54,400) |
| Summaries | Hermes' summarizer and anchor index see Tameru rows as Hermes' own one-liners, without refs | - |
| Engine | Tameru 1.5.0: red-team fixes, wider secrets screen, ~4.6x faster | - |

A user's own `compression.proactive_prune_tokens` (or an explicit constructor argument) still disables every
window-derived trigger rule. `enabled: false` is byte-identical to Hermes in the replay.

## 5. Red team

### Inside Hermes (plugin)

| # | Severity | Finding | Status |
|---|---|---|---|
| H1 | medium, security | Web/browser/MCP results lost Hermes' untrusted-data wrapper in the `errors` rung, in `tameru_expand` answers and in the artifact trail (which sits in the summary, a higher-authority position) | fixed |
| H2 | medium, privacy | The artifact trail skipped Hermes' compaction redactor (a `postgres://user:password@` URL, an `hf_` token and a Google key got through) | fixed |
| H3 | medium | `tameru_expand` could not find a ref after a compaction that rotated the session | fixed |
| H4 | medium | Building the engine looked the placeholder model up in the online model catalogue on every agent start (up to 15 s when unreachable) | fixed |
| H5 | low | With `enabled: false`, Codex gpt-5.4/5.5 missed the built-in compressor's threshold autoraise | fixed |
| H6 | low | A NaN/infinite `compression.threshold` reached `update_model`; a grep child crashed with no `sys.executable`; memo bounded by entries only | fixed |
| H7 | info | A forged `[tameru:` header in a tool result keeps that row from being pruned (bloat bounded by Hermes' result spill) | accepted |

Checked and clean: 12-24 concurrent agents in one process (no cross-talk; another agent's ref is "not found"), fault
injection into 58 functions (every failure falls back to Hermes' behaviour), 22 malformed message shapes, every config
key with hostile values, the tool schema through Hermes' sanitizers, Hermes' catalogue validator (14/14, scan "safe";
one informational note for the private `ContextCompressor` import), and the 216 Hermes commits up to `6e7b83a2f1`
(no seam changed).

### The engine as a general library

| # | Severity | Finding | Status |
|---|---|---|---|
| E1 | high | Quadratic time on runs of blank lines (`"\n" * 20000`: over 20 s) | fixed (ms) |
| E2 | high | Quadratic time on a long single-word query (20 KB: ~30 s) | fixed (ms) |
| E3 | medium | Per-term regex recompiles (30 KB query: 8 s) | fixed (0.5 s) |
| E4 | medium | The receipt copied the whole input, credentials on dropped lines included | fixed |
| E5 | medium | Crash on a lone surrogate in the query | fixed |
| E6 | medium | `contains_secret` recognised 29 of 80 common credential shapes | 70 of 80 |
| E7 | medium | Unbounded same-text memo in the speed-up work (57 MB after five 8 MB inputs) | bounded |

Documented, not changed: dense combining marks are linear but slow (~5 s/MB), so a multi-tenant server should cap
input (e.g. 1M chars) and use a worker timeout; output containing the engine's own recursion marker is passed through
unchanged (a host should use a nonce'd gap marker); `ccr=True` writes originals to a shared directory (the Hermes plugin
does not use it).

## 6. Remaining trade-offs

- **Many small outputs** stays 1.6-8.9% dearer at 32K-64K. Nothing in it is prunable; the cost is the artifact trail in
  the compaction summary, which keeps twice the error templates (21 vs 10 at 64K). Shrinking it enough to remove the
  extra compaction at 32K loses error templates.
- **Refactor 48K** is 3-4% dearer, for 13-15 gold facts against 10 and 0-1 compactions against 3.
- **The 200K default session is protected by a margin, not a rule**: it peaks at 0.731 of the threshold against a 0.75
  trigger. A proactive prune cannot know whether a session will ever be compacted; `tameru_expand` recovers what a
  prune cut.
- **Discontinuity near 57K windows** where the small-window rule hands over to the 48K floor; cells on both sides
  behave sensibly.
- All numbers come from scripted replays with a fake summarizer, not live sessions with a real model; they measure
  what stays in context, not task success.

## 7. Verification

| Check | Result |
|---|---|
| Real-Hermes suite at `a0a4992`, `63d0b94`, `6e7b83a2f1` | 2,009 passed, 6 skipped, 9 strict xfails (known losses) on each |
| Stub suite (clean venv without Hermes) | 1,386 passed |
| `ruff check .` | clean |
| `sync_to_harness.py --check` against the engine | in sync |
| Engine suite (Tameru 1.5.0), Python 3.10-3.14 | 1,318 passed, 3 skipped |
| Engine benchmark battery and threshold sweep | unchanged from 1.4.1 |

## 8. Catalogue update

One PR to `NousResearch/hermes-agent` `plugin-catalog/tameru.yaml`, after this branch is merged to the plugin's
`main`: the new `sha` and `version: "1.5.0"`. The current disclosure names `proactive_prune_tokens=48000` and a
2,000-char minimum, both out of date; suggested text (no tuning numbers, so later retunes need no disclosure edit):

> Replaces Hermes' context engine (subclasses `ContextCompressor` through a private import). Unless you configure
> `compression.proactive_prune_tokens`, it turns on Hermes' proactive tool-result pruning with a trigger scaled to the
> model's context window. Adds the `tameru_expand` tool. Grep patterns with repeats run in a short-lived stdlib-only
> `python -I -S` child with a timeout. No network calls, no credentials; writes a telemetry file only when
> `telemetry_log` is set; in-process isolation only.

See [RELEASING.md](RELEASING.md).
