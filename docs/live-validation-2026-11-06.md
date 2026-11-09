# Live Hermes validation — 2026-11-06

Real stack: `hermes serve` (tui_gateway JSON-RPC, the Desktop wire path) on
127.0.0.1:9520, model gpt-4.1 via GitHub Copilot, plugin installed at
`~/AppData/Local/hermes/plugins/tameru`, enabled via `plugins.enabled`,
`context.engine: tameru`, `plugins.entries.tameru.settings.policy_mode: adaptive`.

## What ran for real
- session.create + session.resume over `/api/ws?token=` (loopback token auth)
- 10+ real turns; agent executed real `terminal` commands (verbatim `find`
  listings ~50KB each — had to forbid `| wc -l`, else the model shrinks them)
- `command.dispatch {name:"tameru"}` — same RPC plugin.js uses
- `command.dispatch {name:"compress"}` — manual compaction

## Verified live
- `Plugin 'tameru' registered context engine: tameru` (agent.log)
- Request observation: `Last check: ~57-68K tokens (whole request estimate)`
- Honest refusal: "Compression refused (summary would grow the conversation)"
- Honest gate: "No cleanup met the reclaim and cache requirements" (tail window)
- REAL demotion: `terminal · e014bcf7579264de | 474 → 32 lines | Available`
  — `Last adaptive action: Routine cleanup · Strong; ~12,184 message tokens reclaimed`
- REAL recovery: `/tameru view e014bcf7579264de` → chars 1–12,000 of 49,913,
  byte-exact original; "Viewing does not change the agent's context."

## Real bugs found and fixed (commit f7ee9e2)
1. /tameru "no live context" on the serve/Desktop path: command.dispatch invokes
   plugin handlers as handler(arg); session identity arrives via
   _set_session_context env (HERMES_SESSION_ID) — added env fallback in
   SessionRegistry.command.
2. doctor: observe_request missing **kwargs; provides_hooks missing
   pre_api_request — both fixed, doctor now clean.

## Known divergence worth noting
- `context.engine: tameru` in config selects LEGACY mode by default
  (back-compat); adaptive needs plugins.entries.tameru.settings.policy_mode.
- Hermes' request estimate (~15K) diverges ~4x from provider-billed tokens
  (~60K) on path-heavy content; Tameru's adaptive occupancy uses the estimate —
  on a 1M-window model it correctly says "enough room" at 1.4%.
- Proactive prune only runs inside the over-threshold preflight branch —
  unreachable below the compaction threshold regardless of
  proactive_prune_tokens.
- Resumed sessions start with an empty compressible window
  (failure_class=empty_post_handoff_window) — normal; fills as turns push
  content out of the protected tail.
