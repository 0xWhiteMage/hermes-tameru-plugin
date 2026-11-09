# Tameru 1.7.0 candidate: validation and replay

Recorded 2026-11-05. This report describes the local 1.7.0 candidate and its
captured checks. It does not establish publication or catalog acceptance.

**Historical baseline, not the completed adaptive release.** A later audit
found that the cost figures below exclude auxiliary summary inputs and schema
costs. Four stock runs never sent the closing question. Identity reconstruction
also found a required literal lost by Tameru in both 64K `smallrows` seeds,
despite the favorable count comparison: “Do not bump its base image until its
schema migration has shipped.” These are scripted observations with a fake
summarizer, not measured live-agent failures.

The [frozen-data audit](replay-protocol-1-audit.json) preserves the original
captures and checks the original fixture hashes. Adding only their recorded raw
summary-prompt characters at uncached 1x changes the mean paired cost comparison
from −3.21% to −4.07%; 12 of 60 pairs still cost more with Tameru. This is a
historical sensitivity calculation, not the corrected protocol-2 cost metric.
Protocol 2 also prices declared schemas, captures immutable closing requests,
and compares fact identities. Its new candidate comparisons are still pending.

## What changed

The candidate adds a session-bound `/tameru` activity table, a bundled Desktop
composer panel, and a read-only original-output viewer. `tameru_read_original`
is the clearer recovery tool name; `tameru_expand` remains a compatible alias.
The viewer rejects stale responses after chat or session changes.

Other changes include profile-specific configuration resolution, shared live
engine lookup across Hermes discovery paths, bounded provenance receipts,
credential/size screening of recovery metadata, and improved whole-record
handling for structured reports and developer output. Copied or modified
Tameru-looking headers cannot borrow another result's original or diagnostics.

Original availability remains bounded. The UI viewer does not restore past
model context. An agent's recovery-tool response can supply original text as a
new tool result, but does not undo an earlier Hermes summary. Persistent restore
and Keep controls remain deferred pending a supported host transaction.

Pressure-aware, task-sensitive activation and retention policies remain a
research proposal; they are not implemented in this candidate.

## Frozen replay

The replay uses real Hermes imports at
`a0a49926f44c231f3456faffeaa4b1cc8ee4274c`, Python 3.14, and the plugin reporting
engine version `1.7.0`. Five scripted session shapes, six context windows
(32,000, 48,000, 64,000, 128,000, 200,000 and 256,000 tokens), and two seeds form
60 paired comparisons.

Each cell runs stock Hermes defaults and Tameru defaults on the same scripted
conversation. The clock advances 20 simulated seconds per request. Overflow
attempts are rejected and recovery is simulated. A lossy fake summarizer
replaces model-generated summaries.

The legacy factory name `tameru_1_4_product` means the current plugin defaults;
it is not a frozen 1.4 implementation. The captured engine version and source
hashes identify what ran.

- [Protocol, source hashes and environment](replay-1.7.0-20261105/manifest.json)
- [Aggregate and per-scenario results](replay-1.7.0-20261105/report.json)
- [Paired raw captures](replay-1.7.0-20261105/)
- [Capture/aggregation implementation](../tests/eval/release_matrix.py)

The source hash manifest was unchanged during the run and matched the working
source immediately after capture.

The core candidate was subsequently committed locally as
`d32e88d9a14a542b26b161ef54696ce788be8a93`. The plugin's `VENDORED_FROM` metadata
was then updated to that commit. A hash comparison confirmed that this package
marker was the only measured Python file changed after capture; its diff changes
the source pin/comment and line endings, not runtime behavior. The frozen replay
artifacts remain unchanged.

| Metric over 60 scripted sessions per arm | Stock Hermes | Tameru |
|---|---:|---:|
| Turns failing after context-overflow recovery | 76 | 0 |
| Rejected overflow requests | 76 | 0 |
| Gold literals under the historical observation rule | 705 / 1,044 | 859 / 1,044 |
| Distinct error-template opportunities retained | 510 / 1,056 | 940 / 1,056 |
| Fake-summary calls standing in for LLM summaries | 106 | 48 |
| Potential re-fetch calls identified from missing facts | 228 | 146 |
| Tool-call/result pairing violations | 0 | 0 |
| Historical-row rewrites outside commit boundaries | 0 | 0 |
| Unparsed summary prompts | 0 | 0 |

The historical gold-literal figures are 67.5% versus 82.3%; error-template figures are
48.3% versus 89.0%. These count opportunities across sessions, not globally
unique facts. Literal presence does not establish correct attribution,
understanding, or success on a live task.

### Historical main-message input cost

The harness prices input characters using exact common-prefix reuse and fixed
cache-read/write rates. It does not measure provider token billing or live cache
behavior.

Mean simulated main-message input cost per session was 1,698,322.45 character equivalents
for stock and 1,584,133.60 for Tameru.

- **Mean of the 60 paired relative cost changes:** **−3.21%**.
- **Change in the aggregate simulated input cost:** **−6.72%**.
- Tameru cost more in **12 of 60** pairs.
- Restricting to the **50 pairs where both arms completed every turn**, the mean
  paired relative cost change was **−4.32%**.

These are different estimands. In particular, the all-pairs comparison includes
stock runs with failed turns. The mean paired total-cost change is not the
historical 1.6.0 report's cost-per-answered-turn statistic.

| Session shape | Mean paired relative simulated input-cost change | Pairs costing more with Tameru |
|---|---:|---:|
| General debugging | +0.18% | 2 / 12 |
| On-call incident | −21.86% | 0 / 12 |
| Refactor | −4.04% | 2 / 12 |
| Many small outputs | +2.29% | 6 / 12 |
| Non-English output | +7.36% | 2 / 12 |

No pair had a lower total literal count with Tameru under the old observation
rule. That count comparison missed the `D3` identity loss in the two 64K
`smallrows` pairs. The four stock runs without a closing send were `oncall` and
`i18n` at 32K, both seeds; their earlier-request observations cannot establish
final-question retention. More retained text can also cost more.

The re-fetch row counts opportunities inferred from missing needed facts; the
harness did not execute those additional calls or charge their eventual cost.
Tool overhead is held at a fixed 3,000 tokens; the extra recovery-tool schema
is not separately priced. Failed turns still append scripted conversation rows,
so this is controlled replay rather than adaptive task execution.

## Core engine checks

The reviewed synthetic campaign ran **228 cases** under hash seeds 0 and 12345:

- All 228 returned successfully with no recorded harness violations.
- All 228 produced equal output bytes across the two seeds and passed
  byte-for-byte repeat-compression checks.
- All **224 required snippets across 172 cases** were present.
- **19 transformation fixtures** flagged by the conservative literal-line audit
  were separately reviewed from frozen inputs and outputs. The checks cover
  record structure, retained values and numeric lexemes, omission counts where
  applicable, and the specific historical defects.

These are fixture checks, not a general proof of semantic fidelity. The campaign
does not certify every omission-marker count in arbitrary output. Functional
timings were observational; no new comparative latency claim is made.

The engine's existing production battery also passed **15/15** cases. The
existing fixed-mode threshold sweep retained its required snippets without
forbidden snippets in **15/15** cases at each of 0.1, 0.2, 0.3, 0.5 and 0.7;
one case failed open at each operating point. This sweep does not evaluate an
adaptive controller or establish an optimal default.

Core artifacts:

- [Reviewed campaign and transformation checks](https://github.com/0xWhiteMage/tameru-compaction-system/tree/main/benchmarks/redteam/reports/1.7.0)
- [Battery and fixed-mode sweep capture](https://github.com/0xWhiteMage/tameru-compaction-system/blob/main/benchmarks/release-1.7.0-gates.json)

## Integration and packaging checks

Full core, stub-plugin and real-Hermes runs were performed during assembly.
They found failures that were then fixed and rerun in targeted groups. This
report does not describe those initial full runs as entirely green or claim
that every full suite was rerun after the final fix.

Recorded post-fix checks include:

- 97 core tests for malformed JSONL, structured formats, brief selection and
  wrapped payloads.
- Focused plugin groups: 228 stub checks and 136 real-Hermes checks.
- Retained-budget and idempotency regression reruns, 14 real-Hermes expansion
  E2E checks, and seven targeted forged/oversized-header checks.
- The stub import-negative contract passed in clean Python 3.13. Its earlier
  failure under the Python 3.14 environment came from an editable Hermes import
  finder exposing a real module to the stub test; no product workaround was added.
- 12 Desktop DOM behavior tests.
- Hermes plugin validator: 15 checks passed, zero warnings.
- Core and plugin wheels built and inspected. The plugin wheel includes the
  manifest, Desktop entry and new vendored report modules; tests, red-team
  material and the private roadmap are excluded.
- Ruff passed on the touched files at the package verification stage.

After the source pin and final README changes, both wheels were rebuilt,
vendored synchronization passed, and the packaging suite passed 9/9 checks.

Real loading, session/profile routing, rotation/reset and original lookup were
exercised. **Live Electron Desktop validation remains pending**; DOM behavior
tests are not a substitute for that surface.

## Protocol-2 adaptive matrix (development + release + held-out)

Recorded post-tune at `cc5538f`/`859c58d`. Protocol 2 prices declared schemas,
auxiliary summary input and cache-read/write economics; it captures immutable
closing probes and compares gold fact *identities*, not just counts. Arms: the
frozen pre-policy engine (`2ce219a` worktree), stock Hermes, current legacy mode,
and adaptive/fixed pairs of the three preferences, all on one scripted harness
with a fake summary model. **These are scripted observations, not live-agent task
results.**

### Development matrix — 540 paired cells

5 recipes (`default`, `oncall`, `refactor`, `smallrows`, `i18n`) × 6 windows
(32K–256K) × 2 seeds × 9 arms. Artifacts: `adaptive-release-07/` (manifest +
captures + `summary.json`, verified against the frozen manifest).

| Arm | Aggregate Δ vs frozen | Cache-priced | Aux summary input | Failed turns | Gold ids | Prunes | Compactions |
|---|---:|---:|---:|---:|---:|---:|---:|
| frozen (baseline) | — | 102.52M | 1.22M | 0 | 845/1044 | 162 | 53 |
| stock | +5.98% | 108.17M | 1.76M | **76** | 659/1044 | 0 | 106 |
| legacy_current | **±0.00%** | identical | identical | 0 | 845/1044 | 162 | 53 |
| **adaptive_balanced** | **−0.24%** | **101.01M (−1.5%)** | 2.48M | 0 | **846/1044** | 213 | 47 |
| fixed_balanced | −0.31% | 100.91M | 2.51M | 0 | 844/1044 | 213 | 47 |
| adaptive_save_space | +2.11% | 103.50M | 2.42M | 0 | 850/1044 | 195 | 48 |
| fixed_save_space | +1.90% | 103.30M | 2.41M | 0 | 851/1044 | 195 | 47 |
| adaptive_preserve_detail | +15.87% | 116.51M | 3.70M | 0 | 806/1044 | 75 | 99 |
| fixed_preserve_detail | +15.57% | 116.13M | 3.76M | 0 | 806/1044 | 67 | 101 |

`legacy_current` is byte-identical to frozen across all 60 cells (the regrowth
rearm fix, `f3d8475`, restored exact trajectory parity). Adaptive and fixed
balanced converged after the tuning (`e73d893`, `6e55fd2`, `cc5538f`): same prune
cadence, same compaction count, cost within 0.07% of each other. Both beat frozen
on aggregate under flat **and** cache-priced models.

**Strict identity caveat.** Every non-parity arm loses individual
baseline-retained ids to trajectory divergence (adaptive_balanced: 12 cells, 8
distinct ids `{C1,D2,D3,R1..R5}`) even while retaining more gold in aggregate
(846 vs 845). Only legacy mode is an exact-identity superset; the report's
aggregate gold number must not be read as identity parity. Recorded per-cell
losses live in `KNOWN_LOSSES` (strict xfails, measured numbers).

### Held-out matrix — 216 cells

6 recipes authored after the tuning freeze (`longform`, `mixedlang`,
`diagnostics`, `rereads`, `bursty`, `dense`) × {32K,128K} × seeds {17,41} × 9
arms. Artifacts: `adaptive-holdout-02/`.

- `adaptive_balanced` **−0.42% mean paired vs frozen**, 0 failures, **380/380
  gold ids — no lost identities on any held-out cell.**
- `legacy_current` exact frozen parity again.
- stock +12.46%, 20 compactions vs 2, 368/380 gold.
- `adaptive_preserve_detail` +27.84% and loses `B1/B2` at `bursty/32K`
  (retention inflation → compaction cascade; same mechanism as stock, milder).
- `bursty` is the discriminating recipe: alternating tiny/huge rows punish
  over-retention; balanced's worst held-out cell is +2.2%.

### Under-trigger schema overhead (honest cost)

The declared recovery/activity schemas cost input on sessions that never reach
the plugin's trigger: ~15–28K char-eq absolute (~1% on the 1.5M+ dev sessions,
up to ~10% relative on the ~150–270K held-out sessions). This is recorded
per-cell in `KNOWN_LOSSES` across `test_large_windows`, `test_scenario_replay`
and `test_small_window_replay` rather than hidden by a loosened assertion.

### Test suite state

`HERMES_REPO_ROOT=<real checkout> python -m pytest tests/ -q`: **2618 passed,
3 skipped, 70 strict xfails** — fully green. Resolved en route: light-stage
references retuned to the two-stage ladder, legacy-path engine tests pinned to
`policy_mode="legacy"`, the speculative-telemetry assertion updated for
adoption-time publication (`compress()` publishes only after
`on_turn_complete`), baselines regenerated under `TAMERU_WRITE_BASELINES=1`,
and the activity/policy test helper defaulted to adaptive mode for the
adaptive-command surface. Stub-mode `stub_only` tests require a Python
environment without the editable `hermes-agent` install; the editable install
leaks the real `agent.agent_init` into the stub scope (documented env leak, not
a product regression). The release wheel was rebuilt post-tune and verified to
carry the final policy constants.

## Interpretation and remaining release work

The candidate improves the specific safety and structured-output cases examined
and exposes context changes through the existing Tameru installation. Its
extractive pass adds no LLM or network calls. Hermes can still use an LLM for
summarization, and prefix changes can affect cost.

Before publication, finish the plugin commit, final release review and catalog
update. A source release and catalog admission are separate events.
Use these captured measurements when preparing the demo; do not reuse the old
cost statistic or describe synthetic retention as live-agent task success.
