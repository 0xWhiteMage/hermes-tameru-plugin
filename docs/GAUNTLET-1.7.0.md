# 1.7.0 adaptive candidate: acceptance and coverage

Owner: lead agent. Started 2026-11-05, before adaptive implementation or tuning.
This document defines the work and records acceptance; it is not a pass report.

## Frozen starting point

- Core: `d32e88d9a14a542b26b161ef54696ce788be8a93`.
- Plugin: `2ce219abfb937207068087a4fbcd0457846abbf7`.
- Real-Hermes compatibility baseline: `a0a49926f44c231f3456faffeaa4b1cc8ee4274c`.
- Evidence: `REPORT-1.7.0.md`, `replay-1.7.0-20261105/`, and the core's
  `benchmarks/redteam/reports/1.7.0/`.
- Exploratory `docs/redteam/`, `tests/redteam_170/`, the core's original
  `run_redteam.py`/findings, and the unfinished Squeez scorer are outside this
  baseline. Preserve them locally; their claims do not count as acceptance.

The baseline includes completed targeted regression checks, not an assertion
that its last full assembly runs were uniformly green. It is committed locally;
publication is a separate event. Existing artifacts remain immutable.

## Required observable behavior

The candidate must provide independent activation, retention and recent-tail
controls, plus Save space, Balanced and Preserve detail preferences. Preferences
come from trusted configuration or an explicit session command; tool output
cannot change policy. Unknown task importance does not justify aggressive loss.

At low pressure, ordinary checks leave message bytes, object identity and cache
prefixes unchanged. At increasing pressure, a plan selects stronger eligible
reductions, remeasures the result and stops when its target is met. Hard pressure
can bypass ordinary rearm constraints but cannot relax the fidelity/trust floor.
If protected content prevents sufficient reduction, expose the shortfall and
defer to the host's supported handling.

Explicit Hermes settings retain authority. Model/window changes must recompute
derived limits; reset and session/profile changes must clear the correct state.
Accounting distinguishes current request occupancy from lifetime usage, net
input limits from raw capacity, and measured values from estimates.

The existing host commit path is the sole authority for historical rewrites.
Policy commands may change future planning preferences without editing history,
system prompts or tool schemas. Inspection does not change policy, stored
original eviction order, or model context.

## Test seams and method

The user's autonomous-work instruction authorizes choosing these seams without
another approval checkpoint:

1. Pure policy planning: immutable budget/request inputs produce an immutable
   decision with reason, trigger, target and retention/tail budgets.
2. Hermes context-engine interface: public prune/compress/status and session
   lifecycle behavior, with real imports and isolated profile/session storage.
3. Existing `/tameru` command interface and original-reader tools.
4. Desktop plugin interface: visible states, keyboard interaction, connection
   routing and asynchronous request ordering through the actual SDK contract.
5. Core `compress_context`: fidelity, format validity, fail-open, determinism,
   input immutability and provenance contracts.

Use behavior tests at these seams, following one red-to-green slice at a time.
Avoid assertions that merely copy implementation arithmetic or inspect source.
Focused tests follow each change; one final assembled validation pass follows
the final material changes. Record failures and fixes, including environment
contamination, rather than relabeling an incomplete run.

## Evaluation protocol and gates

The lead authors and checks all evaluation code, fixtures, scoring, sampling and
configuration. Reviewers can criticize the protocol; a protocol correction must
be justified independently and recorded before its replacement results are used.
Protocol clarification 2026-11-05, before any adaptive run: source review confirmed
that the frozen `cost_cache_priced` metric excludes auxiliary summary prompts,
and the stability monitor checks only surviving tool-row string content.
Those figures retain that narrower meaning; they do not prove total input cost
or whole-history immutability.

Development comparisons use the existing five replay shapes, six windows and
two seeds. Run baseline and candidate with identical host, inputs, clock and
accounting. Compare candidate versus the frozen Tameru baseline as well as stock
Hermes; do not substitute a stock comparison for evidence of this upgrade.

Held-out evaluation will use six newly authored workload recipes: long-form
qualified policy evidence, mixed-language incident evidence, structured
diagnostics, repeated reads/supersession, bursty tool output, and dense protected
evidence. Use windows 32K and 128K and seeds 17 and 41, yielding 24 paired cells.
Freeze the candidate source/configuration before authoring the exact fixture
contents; record both candidate and fixture hashes before execution. Evaluate
the static baseline, the three adaptive preferences, and a fixed policy using
the same preference budgets without occupancy-dependent intensity. Holdout
results are reported separately. Once results influence a change, the entire
exposed matrix becomes regression/development evidence, including its passing
siblings. A changed seed alone does not make the same exposed recipe independent.
Record the next candidate freeze and genuinely fresh challenges before claiming
new held-out success.

Protocol-2 clarification, before any adaptive execution: use
`total_simulated_input_cost = cost_cache_priced + cost_auxiliary_input`.
The auxiliary term serializes every summary-request message with the same
message projection used for the main channel and charges an assumed uncached
1 character-equivalent per character. The earlier proposed raw-prompt term
missed JSON envelope/escape characters and additional summary messages.
Main-channel pricing now includes a stable schema prefix: the existing assumed
3000-token baseline overhead is represented by 12000 characters, plus the
engine's declared additional schema JSON, frozen once per session. These are
simulation assumptions, not measured provider payloads or token bills.
Report both channels separately. Output costs remain unmeasured.

The frozen protocol-1 captures contain raw `summary_prompt_chars`; adding those
to their old main-message cost is a historical sensitivity calculation only.
They lack the new immutable closing witness and schema accounting and cannot
be relabeled as protocol-2 results. Preserve those captures unchanged and use
versioned captures for the corrected comparison.

Cost acceptance uses the arithmetic mean of paired relative changes:
`mean(candidate_total / baseline_total - 1)` across the complete stated matrix.
Report the aggregate-cost change separately; it is not the acceptance statistic.
Each baseline denominator must be positive. Rewrite comparison uses the ratio
of summed `(prune_commits + compactions)` across the complete family; a zero
baseline sum cannot satisfy the rewrite-improvement target. These are replay
operations, not independently witnessed durable host transactions.

Preserve final-request text, source/fixture hashes, closing-probe success and
the position of the observed request in new captures. Score the closing probe;
an unsuccessful closing probe fails completion and is not silently replaced by
an earlier successful request. Retention comparisons use fact identities and
literal/association witnesses, not only counts. Historical frozen results with
no such witness remain historical results with their original observation rule.
Existing frozen IDs can be reconstructed from per-category missing-ID lists and
the unchanged scenario definition; do not infer missing association evidence.

Hard gates:

- No known critical/high-impact fidelity, security, isolation or commit defect.
- Every explicitly protected literal/association in the new fixtures survives
  or is left unchanged by fail-open. Neither a marker nor an accessible archive
  counts as active-context presence.
- Existing core golden/fidelity cases remain green; changed transformations
  require structural review rather than weaker gold assertions.
- Zero pairing violations, unauthorized historical rewrites and forged-reference
  ownership in the integration checks.
- No extra overflow failures versus the frozen Tameru baseline in paired replay.
- The Balanced candidate retains every baseline-retained required fact identity
  in every development pair, plus at least its error-template count. New
  diagnostic fixtures also check literal values and owning-record associations;
  masked template equality alone is insufficient.
- The default Balanced candidate's mean paired relative total simulated input
  cost change across the development matrix is at most zero. Report every
  more-expensive cell.

Declared efficiency targets are the many-small-output and non-English replay
families where the baseline's cost was unfavorable against stock. A useful
efficiency improvement is at least 5% lower mean paired relative total simulated
input cost or 10% fewer summed replay rewrite operations in at least one of
these two complete families,
without failing the hard gates. These are finite-corpus engineering targets,
not claims of statistical generalization or provider billing.

Product improvement over the old baseline is distinct from improvement due to
adaptation. The budget-matched fixed-policy arm is required on development and
held-out matrices. If it matches or dominates the adaptive variant in every
measured outcome, simplify the controller rather than claiming the additional
adaptive machinery earned its cost. Any adaptive advantage must identify the
specific pressure/workload conditions and quality/cost trade-off it demonstrates.

Preset checks must also demonstrate their intended trade-offs: Preserve detail
keeps more source detail or postpones elective rewrites under moderate pressure;
Save space can reclaim more space earlier while preserving the same protected
evidence. Report cost and retention consequences for every preset.

Keep actual provider usage, character-priced simulations, estimated tokens and
Devin task outcomes distinct. Use controlled live-agent probes through available
Devin capabilities; label them as probes over the tested surfaces, not native
Hermes end-to-end model runs if that path was not exercised.

The pure planner must use bounded state and no transcript-wide scan, file access
or model call. Measure its overhead separately from token counting and rendering.
Broader latency claims require an explicit repeated timing protocol.

## UI and release gates

The overview exposes the preference, pressure/accounting source and last
decision reason. Details explain changed results, current context visibility,
original availability, and limitations. Unknown values remain visibly unknown.
Loading, empty, stale, unavailable and failed states have actionable copy.

Keyboard access, stable focus and stale-response rejection are mandatory.
Use supported Hermes UI elements and themes; no separate installation,
background focus stealing, arbitrary HTML execution or new model-facing tool
schema is needed for the controls.

Exercise real Hermes command/plugin loading and profile/session lifecycle.
Attempt live Desktop verification using an isolated test profile. Distinguish
live results, DOM tests and remaining platform limitations in the release report.

Final package checks cover metadata, vendored source pin/synchronization, runtime
files, excluded tests/private artifacts, supported imports and the repository's
required lint/build checks. Final documentation and demo claims use captured
results from the accepted candidate.

## Coverage ledger

Reviewers are separate AI contexts, not statistically independent human audits.
The lead adjudicates findings using evidence. Initial audit is round 0; two
substantive rounds without worthwhile further changes begin only after all
mandatory gates are satisfied.

| Area | Purpose / principal evidence | Initial reviewer | Status / open work |
|---|---|---|---|
| Core selection and formats | Source associations, numeric/Unicode fidelity, frozen transforms | Fidelity specialist | Baseline recorded; fresh audit pending |
| Adaptive policy | Independent controls, pressure/rearm, safe inability to reclaim | Policy specialist + lead | Design/implementation pending |
| Recovery and provenance | Availability, screening, bounded state, forged receipts | Security specialist | Baseline regressions recorded; fresh audit pending |
| Configuration | Explicit precedence, validation, profile ownership | Integration + security specialists | Adaptive settings pending |
| Hermes integration | Host commits, cache discipline, route changes | Integration specialist | Baseline real-import checks; adaptive checks pending |
| Session/backend lifecycle | Clone, reset, rotation, multi-profile routing | Security + integration specialists | Fresh audit pending |
| UI/UX/accessibility | Preference/pressure explanation, states, keyboard, original view | UX specialist | Existing viewer; adaptive UI/live verification pending |
| Evaluation tooling | Comparable inputs, honest metrics, frozen/held-out protocol | Evaluation specialist + lead | Baseline frozen; new protocol/harness pending |
| Packaging/docs/demo | Reproducible artifacts, compatibility and accurate claims | Release/evaluation specialist | Baseline wheels; final artifacts pending |

## Completion

Readiness requires all mandatory gates, resolved material blockers and complete
coverage. Then perform two fresh substantive review rounds. Record each round's
new challenges, findings, disposition and evidence. Stop after two rounds find
no change whose expected benefit justifies its cost and risk.

Unavailable evidence remains an explicit limitation or blocker for its stated
criterion. Do not convert missing validation into approval. Publish and update
the catalog only after the release gates pass; social/submission text stays draft.

## Round 0 review tracking

Started six separate read-only reviewer contexts:

- Fidelity: `b869cfaa` (report received).
- Adaptive policy/research: `3b82ca15`.
- Security/recovery: `d0adbf09`.
- Hermes integration/cache: `038c9dfc`.
- Native UX: `6cb69cc6` (report received).
- Evaluation/release methodology: `bbc5087b` (report received).

The following received findings await lead source verification and disposition;
they are not accepted product defects merely because a reviewer reported them.

UX reports that the pinned host's public SDK does not export
`gatewayActivationEpoch` or `useGatewayActivationEpoch`, while the plugin's
registration guard requires them and the synthetic DOM test invents them.
This may silently suppress the native panel. Verify the actual SDK contract
before selecting an alternative; removing source-invalidation protection or
using a constant epoch is not a valid fix.

Other UX findings: fallback focus is missing when an original-view row vanishes
or becomes disabled; stale refresh failures are hidden in the detail view;
original details lose result identity and active-context/archival distinctions.
Adaptive state/control presentation is an expected baseline gap.

Evaluation reports these additional protocol gaps:

- `cost_cache_priced` counts conversation requests but excludes separately
  recorded auxiliary summary inputs; qualify the old report's scope.
- The current rewrite monitor covers surviving tool-row string content only,
  not all history fields/order/deletion or actual host commit authorization.
- Retention counts do not establish preservation of the same fact identities;
  literal-line membership does not establish record associations/order.
- Precisely settle mean paired relative versus aggregate cost acceptance and
  rewrite aggregation; distinguish adaptive benefit from a static retune.
- Freeze the candidate before holdout exposure; after result-informed tuning,
  retire the exposed matrix/recipe family from held-out claims, not just failures.
- Node UI tests and Desktop asset checks are absent from required CI.
- Captures do not preserve closing-question success and retention witnesses;
  last-successful-request retention can differ from closing-probe retention.

No adaptive implementation or evaluation has run yet. Resolve protocol issues
before new outcomes. The original evidence remains frozen.

Fidelity reviewer reports five bounded public-API reproductions, to be checked
by the lead before implementation:

1. SARIF's early selection precedes `pin_patterns`/`pin_recent`, losing pinned
   records (`compress_context.py:6626–6629,7083–7105`).
2. Embedded JSON with literal key `"service.catalog"` raises `KeyError: service`
   because flattened dotted paths are split for replacement
   (`compress_context.py:5049–5096,6815`).
3. SARIF query `café` matches a literal string but drops its equivalent
   `caf\u00e9` record; match decoded values while retaining original spans
   (`structured_reports.py:118–123`).
4. `git log --oneline --notes` query hits retain a note without its owning SHA,
   subject or `Notes:` header; full-format atomic expansion misses oneline
   records (`agent_formats.py:4172–4182,4350–4369`).
5. YAML subtree selection retains `<<: *defaults` but drops `defaults: &defaults`
   and inherited numeric settings, yielding an undefined alias while claiming
   structural validity (`format_adapters.py:723–792`).

The reviewer ran five small stdin probes, not a full suite, and made no edits.
These are new round-0 findings; no source repair has yet been authorized to an
implementer. Lead should inspect the reported paths, author focused behavioral
cases and batch grounded fixes for implementation.

## Core repair checkpoint — 30b6f50

The lead accepted all five fidelity findings after source inspection. The core
repair is committed locally at `30b6f50`; the frozen baseline remains `d32e88d`.
The plugin has not yet been synchronized.

Literal JSON keys now use tuple traversal. A reduced embedded JSON payload is
kept as a complete projection, preventing subsequent scoring from detaching its
parent key. SARIF selection matches decoded strings while emitting original
record spans. Git oneline note/patch hits retain their owning commit. YAML
references conservatively return the exact original document, including wrapped
outputs. Original pinned blocks are checked before accepting extraction or a
summary; loss produces an explained fail-open.

Eight focused regression/control tests were added in the existing core
reliability file. The three-file gate reported 99 passed and one skipped.
Lead review strengthened the collision and escaped-string witnesses; those two
tests then executed separately and passed. Targeted Ruff still reports 13
pre-existing findings outside the edits; this is not a clean-lint claim.
This checkpoint is implementation evidence, not independent final approval.

The six initial reviews are now collected. Integration work remains open for
output-reservation handoff, speculative recovery writes, durable rearm state,
commit confirmation after host postprocessing, and persistence-failure retries.
The detailed provenance/recovery review remains private during remediation.
Upstream Hermes `84a0301415edd6d1385207ec96e4d7f2b6c1fcd9` exports the Desktop
epoch APIs absent from the frozen host pin, but still omits the per-agent output
reservation when initializing a plugin engine. It has not yet been validated as
the candidate's supported host.

## Lead source observations for the implementation design

The inspected host's `threshold_tokens` already derives from the effective input
window after output reservation (`context_compressor.py:2735–2805`); avoid
subtracting that reservation again. `turn_preflight.py:374–393` calls the public
prune method with current request tokens, and accepts only a new list with a
nonzero changed count. The host's prune method prepares a candidate through
`_prune_old_tool_results`, enforces meaningful reclaim, persists it through
`archive_and_compact`, then updates the rearm state.

The existing plugin scopes ladder/retention changes through `_Pass`/`ContextVar`,
temporarily overrides prune gates and may retry with a stronger representation.
This is the seam to extend, rather than adding a background history rewrite.
Host rearm accounting is message-only while `current_tokens` includes request
overhead. Any target/rearm comparison must preserve that distinction.

Design remains unsettled pending the other reviews. Intended shape: pure
immutable planning inputs/decisions; per-session bounded preference/growth state;
per-pass captured decision; staged preparation before one host-authorized commit;
explicit host overrides preserved; UI/command preference changes affect future
planning only. No automatic task-importance classifier is justified yet.
