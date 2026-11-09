# Adaptive controller: budget and commit contracts

Status: design work in progress, not release evidence. The acceptance protocol
is `GAUNTLET-1.7.0.md`. No adaptive candidate or held-out fixtures have been
executed under this design.

## Verified host boundaries

The inspected Hermes checkout is
`a0a49926f44c231f3456faffeaa4b1cc8ee4274c`.

- `agent/agent_init.py::_build_context_engine` passes model, context length and
  route information to an external engine's `update_model`. It does not pass
  `_compressor_max_tokens(agent)`, although the built-in constructor receives it.
- `_compressor_max_tokens` accounts for the native Gemini adapter's default
  output allowance. Inferring a reserve from a model name or from the number of
  output tokens actually generated would not reproduce that contract.
- `ContextCompressor.update_model` already accepts `max_tokens`. The missing
  piece is the host's call into the plugin, not that method's capability.
- `pre_api_request` supplies `max_tokens` and a sanitized request after
  middleware. It runs after preflight. Its request payload can be truncated;
  an absent cap in such a payload is not a zero allowance.
- `select_context` exposes assembled request messages and conversation messages,
  but its `budget_tokens` is the raw context length. It must not be reinterpreted
  as a net input limit.
- The host's request estimate includes system/tool overhead. Its proactive
  rearm mark uses message bodies only. These are different accounting bases.
- Native proactive pruning persists history and its rearm mark in the same
  `archive_and_compact` call. Full `compress` prepares a candidate; the host can
  postprocess or reject it before committing.
- A successful full commit calls
  `on_session_start(..., boundary_reason="compression", old_session_id=...)`.
  Outer transactions defer this notification until their own commit succeeds.
  The callback also runs for in-place compaction with the same session ID.
- The timeout worker snapshots messages, not the compressor object. Native
  `_compression_attempt_generation` distinguishes overlapping attempts.

The plugin must use these boundaries without editing Hermes core.

## Budget representation

The pure planner receives immutable scalar inputs. It does not read transcript
text, configuration files, archives or model APIs.

Represent a limit explicitly as one of:

```python
RequestBudget(window_tokens=128_000, output_reserve_tokens=32_000)
RequestBudget(input_limit_tokens=96_000)
```

These examples have the same usable input limit, 96,000 tokens. For a raw
window, subtract a known reserve once. For a net input limit, do not subtract a
reserve again. A reserve of `None` means unknown, distinct from an explicitly
known zero. Reject booleans, negative values and non-integral numeric values.
A reserve consuming the whole window means no available input space; it must
not silently restore the full raw window.

Input usage covers the complete request. Do not subtract system/tool overhead
from the limit if it is already included in usage. A body-only estimate must
carry that limitation rather than being displayed as complete occupancy.

Keep these sources distinct:

1. Explicit plugin configuration or a host-provided budget for this runtime.
2. A cap observed on a previous request on the same route and lifecycle.
3. The host's current compaction threshold, which is an activation ceiling,
   not evidence of raw capacity or of a known output allowance.
4. Unknown budget information.

An observation of a previous request is an estimate for a future request.
Ephemeral caps, provider defaults and middleware can change it. It cannot
establish that the first preflight, or the next changed request, has the right
reservation. No observer should silently replace that missing guarantee.

Refresh the host's current context length and threshold for every decision:
the host can change them without calling `update_model`, including through an
auxiliary-model ceiling or an overflow correction. Model/route changes and
unrelated session changes invalidate observations. A legitimate same-database
compression rotation may preserve state for the continuing conversation.

The UI must distinguish current estimated input, the last measured input,
explicit limits, observations and unknown headroom. Do not call a ratio against
the host's trigger “percentage of the context window.”

## Independent controls

The planned controls have separate responsibilities:

- **Preference:** Save space, Balanced or Preserve detail selects retention
  budgets and the desired cost/detail trade-off.
- **Activation:** an override changes when cleanup is considered, without
  changing the retention preference.
- **Recent protection:** an override changes the recent evidence budget,
  without changing activation or the older-evidence budgets.

A preset may supply defaults for all three, but explicit overrides must remain
independent. Settings changes affect future plans and never rewrite history,
change tool schemas or rebuild the system prompt themselves.

Each pass captures one immutable decision. Later changes to controls must not
change a pass already being prepared. Uncertain task importance does not invoke
an automatic classifier; use the selected preference.

The initial scalar planner is `hermes_tameru_plugin/policy.py`, with independent
controls `preference`, `activation` and `recent`. Activation choices are Auto,
Earlier, Standard and Later; recent protection is Auto, Short, Standard or Long.
Modes are Adaptive, budget-matched Fixed, and Legacy retention. Fixed and Legacy
are comparison/compatibility modes, not additional task-importance preferences.

The initial table is frozen before any adaptive development comparisons:

| Preference | Default fraction of host trigger | Detail multiplier | Recent multiplier | Minimum reclaim / invalidated suffix | Native regrowth floor / host trigger |
| --- | ---: | ---: | ---: | ---: | ---: |
| Save space | 0.70 | 0.75 | 0.80 | 0.20 | 0.10 |
| Balanced | 0.85 | 1.00 | 1.00 | 0.35 | 0.15 |
| Preserve detail | 1.00 | 2.00 | 1.50 | 0.50 | 0.20 |

Light, Standard and Strong stages multiply older-evidence budgets by 2.5, 1.5
and 1.0 respectively. Recent protection in Auto gets an additional 1.25 factor
only in the Light stage. Explicit recent choices use 0.75, 1.0 or 1.5 of the
native recent budget; they do not change with stage or preference. The recent
token target is capped at a quarter of known usable input; native message-count
and unread-round protections still apply. Explicit activation uses 0.70, 0.85
or 1.0 of the host trigger, independent of preference.

Known input-limit pressure permits Standard at 65% and Strong at 85%. Unknown
capacity instead uses 75% and 100% of the host trigger, explicitly labeled as
that different basis. Candidate stages run least destructive first and are
remeasured. Automatic full compaction may try all stages; manual force selects
Strong. Fixed mode always uses Standard retention. It shares activation and
commit accounting with Adaptive.

Growth headroom is capped at 10% of the applicable reference. Known input
capacity caps activation at 85%; the post-cleanup target is the lower of 85%
of the host trigger and 70% of usable input, with growth headroom subtracted
once. The target does not follow the activation control: changing timing must
not silently request a deeper cleanup. This independence correction was made
before runtime integration or any adaptive comparison. Near capacity (95% of
known input, or reaching the host trigger when capacity is unknown), capacity
takes priority over the suffix-based batching floor. Every candidate still
needs positive reclaim and the native absolute minimum. These numerical choices
are engineering hypotheses, not claims of optimality or provider billing.

## Preparation, selection and publication

Prepare candidates from the same original input. Check actual reclaim after
each stage and select the least destructive candidate that satisfies the
current goal and mandatory protection. A persistence failure must not be
interpreted as evidence that a stronger candidate is needed.

The proactive path submits one selected candidate through one native commit
attempt. Its recovery store, extraction memo, receipts and committed-change
statistics are published only when that attempt succeeds. Rejected or
superseded candidates cannot evict previously recoverable originals.

The full path needs the same staging, including reads used by the summary
ledger. A returned list alone does not prove a durable commit. Confirm adoption
from an observed installed history or the supported compression-boundary
callback with actual committed rows. Check lifecycle, database, store and
attempt ownership again after any database read.

Host postprocessing may augment the trailing user message. Confirmation must
support that without accepting arbitrary mismatched candidates or forged
headers. Source identities and exact produced tool bytes remain authoritative.
Cloning an agent must not carry a pending transaction into the clone.

The adaptive full path should first try useful deterministic reclaim.
An auxiliary summary is justified only when safe local reclaim cannot satisfy
the goal, or an explicit manual/focused request requires the host summary path.
This needs a guarded integration with native finalization and its progress
state; bypassing native commit/fence logic is not an option. Initially, a local
full result containing an owned bodyless Tameru row must fall through to the
native summary path: that path can recover mechanical error/artifact evidence
from originals into its ledger. Skipping it must not silently lose that
contribution. This conservative guard can be relaxed only with equivalent
evidence preservation and measured benefit.

## Cache cost and durable rearm

Frequent checks must not imply frequent rewrites. Under a simplified cache
price of `c` per cached character and `1` per uncached character, let `P` be the
previously cached suffix invalidated by a rewrite and `D` the characters
removed from that suffix. The immediate cost change is:

```text
(P - D) - c*P = (1 - c)*P - D
```

Each later identical-prefix request saves `c*D`. For example, with `c=0.1` and
`D=0.5*P`, eight later requests offset the initial rebuild cost. This is a
planning model, not a measured provider bill or a prediction of how many
requests remain.

Batching must therefore consider reclaim and the invalidated suffix, alongside
pressure and the selected preference. Near a hard limit, making room can take
priority over that cost model, while protected information remains protected.

The current post-commit `_arm_regrowth` adjustment changes memory after the
host has persisted a different rearm value. Replace that behavior in the
adaptive implementation. Use the native atomic commit calculation; do not
repair it with a second racing metadata write. Keep message-body regrowth
separate from complete-request activation.

## Remaining implementation and evidence

- Full-compaction staging and pre-commit escalation selection are implemented.
  Focused tests cover real SessionDB commit callbacks, failed persistence,
  delayed adoption, postprocessing, stale attempts, cloning, reset, and empty
  or unchanged rejected candidates. These are integration tests with scripted
  summary responses, not live model evaluations.
- The initial pure policy table is implemented; wire and verify its runtime,
  configuration and controls. Twelve focused scalar tests passed before runtime
  integration. This does not establish compaction benefits.
- Verify fidelity across the whole ladder. Existing `protect_patterns` is
  documented as pinning extracts; brief/header/summary behavior must not be
  assumed to inherit the core extractor's pin guarantees.
- Implement the native controls and explain their accounting sources.
- Compare the frozen baseline, adaptive preferences and matched fixed policy
  under protocol 2; then freeze the candidate before authoring fresh holdouts.
- Resolve or explicitly retain the first-request budget handoff limitation.
  A late observation is not a passing result for that missing host contract.
