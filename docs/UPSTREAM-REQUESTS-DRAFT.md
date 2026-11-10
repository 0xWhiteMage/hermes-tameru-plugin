# Requests to Hermes: drafts (nothing has been filed)

Four changes to Hermes that would let a context-engine plugin do things it cannot do today, each with the evidence behind it. They are drafts for the maintainers' repository;
filing them needs the owner's approval. References are to Hermes v0.23.0 (`2bd378ed`), the version the plugin's baselines were recorded on.

## 1. A turn-start hook that offers the engine the history before the request is built

**Today.** The proactive prune is offered to the engine only after tool results inside a turn (`agent/turn_preflight.py`, `compress_after_tool_results`). At the start of a turn
(`run_preflight_compression`) the engine can only answer `should_compress`, and a yes is a full compaction with its summary.

**Why it matters.** The start of a turn after a pause is when the provider's prompt cache has most likely expired: live on Hermes Desktop with Claude Haiku the last reply showed a 100% cache hit two
minutes after the previous turn and 0% after 28 minutes. A request after expiry writes the whole prompt, so shortening old tool results at that moment adds no rewrite, and the smaller prompt is
written at the write price. A plugin cannot use it: by the first prune hook of that turn the first request has already rewritten the cache. A cold-cache pruning rule was built and measured and made
no difference in any of 27 cells, because it was never asked at a cold moment (the engine's check ran 59 times in a session, always about 20 seconds after the previous reply). In the replay,
idle gaps already favour Tameru by about 4 to 5 points at 128K (mean -19% with ten-minute pauses before every third message, against -15% without); pruning when cold is the part left on the table.

**Request.** A hook (or an optional engine method) called once at the start of a user turn, before the request is assembled, that receives the history and may return a pruned copy, with
the same commit rules as the proactive prune. It would also give an engine the time since the last reply without keeping its own clock.

> **Withdrawn 2026-11-11.** The replay oracle (`docs/COST-TUNING-2026-11-10.md`, "Ceiling measured") shows a turn-start prune hook would not lower
> the mean cost: +0.09% (mixed idle) and +0.32% (slow idle) across 33 cells each, with a few cells 4 to 15% dearer. Do not file this one.

## 2. Ids in the subagent hooks

**Today.** `subagent_start` and `subagent_stop` (`hermes_cli/plugins.py`, the valid-hooks list) are observer-only and carry no `tool_call_id` and no toolset list, so a plugin cannot tell which
`delegate_task` call started a child, or which tools the child may use.

**Why it matters.** A plugin that wanted to give a child the same read-back access as its parent (the recovery tool for the parent's shortened results) cannot bind a grant to a specific call,
so it cannot do it without a way to forge one. The security review is in `docs/SUBAGENT-GRANTS-REVIEW-2026-11-10.md` (verdict: no-go until this exists).

**Request.** Add `tool_call_id`, the parent session id, the child session id and the child's enabled toolsets to `subagent_start`, and the same ids to `subagent_stop`.

## 3. Let an engine re-list its tools at a compaction boundary, or defer them

**Today.** An engine's tool schemas are read once at agent start (`agent/agent_init.py`, `_inject_context_engine_tools`) and pinned for the session. Hermes does refresh the tool snapshot at every
compaction (`agent/conversation_compression.py:171`, `refresh_agent_mcp_tools`, described there as free because the cache is already broken), and that refresh does ask the engine
(`tools/mcp_tool_agent.py:452`, `_schema_getter("context_compressor", "get_tool_schemas")`), but only when MCP tools are registered; the Tool Search "defer" list
(`hermes_cli/config_defaults.py`, around lines 1969-2008) covers registry tools only, not an engine's.

**Why it matters.** The recovery tool is useful only once something has been shortened, yet it is listed (about 1,300 characters at the head of every cached prefix) from the first request. The plugin
now hides it with an `llm_request` middleware until it is needed, which works on chat-completions and Anthropic requests but not on Codex requests (Hermes derives that route's cache key from the
tool list before a plugin can change the request) and costs a deep copy of every request. Re-listing at the compaction boundary would give the same result without any of that.

**Request.** Either always honour an engine's `get_tool_schemas()` at the compaction-time refresh (not only when MCP tools exist), or let an engine tool be named in the Tool Search `defer` list.

## 4. A stable public seam for pruning and demoting a tool result

**Today.** The plugin subclasses `ContextCompressor` and overrides private methods (`_prune_old_tool_results`, the demote step); a startup check (`tests/test_seam_contract.py`) compares their shape and the plugin
falls back to stock behaviour when it changed. Hermes moves quickly (the replay baselines break on every Hermes commit), so each release is a risk the plugin has to re-verify.

**Request.** Name and document the demote and prune seams as part of the context-engine interface, with a version number, so an engine can declare which it needs.

## What would not need Hermes

Everything else in the plugin plan is plugin-side. Nothing here asks for a change to how Hermes summarizes, to its defaults, or to any provider path.
