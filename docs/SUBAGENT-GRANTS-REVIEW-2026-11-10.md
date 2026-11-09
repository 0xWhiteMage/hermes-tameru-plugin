# Subagent grants: security design review (2026-11-10)

Scope: should a delegate_task subagent be able to call tameru_read_original on refs its parent holds? Read-only review; no code changed.

## 1. What Hermes exposes today (verified in source)

- `pre_tool_call` (plugins.py ~1907): kwargs tool_name, args, task_id, session_id, tool_call_id, turn_id. It is fail-closed on timeout and can block or "modify" args. Other plugins' modify directives can rewrite the delegate_task args after we saw them.
- `subagent_start` (delegate_tool.py ~353): fires during child construction. Kwargs: parent_session_id, parent_turn_id, parent_subagent_id, child_session_id, child_subagent_id, child_role, child_goal. It is observer-only (plugins_dispatch.py says so) and wrapped in `_quiet`, so a plugin error or a missed event is silent. It does NOT carry tool_call_id, the toolsets list, or the task index.
- `subagent_stop` is fired once per child from the results path, on the caller thread. It is skipped if the run dies before results are built.
- Child toolsets: `_resolve_child_toolsets(parent, toolsets, role)` and the call site passes `toolsets=None` ("always inherit the parent's toolsets"). The child cannot have more than the parent today, but this is invisible to hooks and a config change could alter it.
- Children get their own session id and their own AIAgent. The tool handler sees only `self._session_id`, so the caller identity is whatever session the engine instance belongs to. There is no parent-link field on the engine side.
- Plugin side: `handle_tool_call` (engine.py 1874) serves only `self._store` plus the session archive looked up by the owning `_session_id`. `screen_original` is the single secret gate. Web/MCP originals are already wrapped as untrusted.

## 2. Can a grant be bound unforgeably?

No. Staging happens at pre_tool_call using model-supplied args. Binding happens at subagent_start, where the only join key is child_goal (a model-chosen string) plus parent_session_id and parent_turn_id. Hermes provides no shared correlation id between the two events. A batch of tasks with equal or similar goals, or a goal rewritten by another plugin's modify directive, makes the join ambiguous. A heuristic join is not a capability.

## 3. Threat model

| Threat | Assessment |
|---|---|
| Confused deputy | The child acts with the parent's data authority, steered by a goal the parent wrote. High. |
| Prompt injection steers parent to grant | Injected web text asks the parent to delegate with refs. The grant list is model-chosen, so injection controls it directly. High. |
| Exfiltration via child | Child inherits parent toolsets today, so it has the same network reach as the parent. The grant widens who reads, not what can leave, but a child runs with a thinner prompt and less oversight. Medium, rises if Hermes ever lets children carry extra toolsets. |
| Ref guessing | Refs are 8 or 16 hex. An 8-hex prefix is guessable by a child. Grants must be exact-ref only, never prefix. Medium. |
| Stale grants | Missed subagent_stop (crash, interrupt) leaves a live grant. Needs a TTL. Medium. |
| Cross-session leakage | A grant keyed by goal digest can bind to a different child or a concurrent session in the same process. High while the join is heuristic. |
| Stage/bind race | Two parallel delegate_task calls or a blocked/failed delegation leave a staged grant that the next child consumes. High. |
| Nested subagents | Grandchild has a different session and parent. Must not inherit. Easy to forbid, easy to miss. |
| Replay after restart | In-memory only would die safely. Persisting would make a restart bind a grant to a recycled goal. Do not persist. |
| NAS / multi-profile | Archives can be shared across profiles. A profile child can load a different home. Grants must never cross session or profile. |

## 4. Decision: NO-GO now

The core control, binding a grant to one specific child, cannot be made unforgeable with the current hook surface, and the data is exactly what the secret screen exists to protect. A fail-closed rule would drop most legitimate use, and a fail-open one is a data leak. Building it on goal-digest heuristics would look secure and not be.

## 5. What Hermes must expose first

1. `tool_call_id` (and task index) in `subagent_start` and `subagent_stop`, so stage and bind join on a Hermes-issued id instead of model text.
2. `child_toolsets` (resolved, post-strip) in `subagent_start`, so the plugin can refuse a child that has network tools the parent lacks.
3. A blocking subagent_start (or a post-construction callback) so a failed bind can veto the child rather than being swallowed by `_quiet`.
4. A way for a child's engine to learn its parent_session_id from Hermes, not from a string the parent controls.

## 6. If those land: minimal design

- Hooks: stage at `pre_tool_call(delegate_task)` keyed by tool_call_id, read the final args only after the modify phase. Bind at `subagent_start` by tool_call_id + parent_session_id, recording child_session_id. Revoke at `subagent_stop`, on parent lifecycle reset, and on TTL.
- Data: process-local dict `grants[child_session_id] = {parent_session_id, refs: frozenset of exact 16-hex, expires_at, reads_left}`. A staging table keyed by tool_call_id, TTL 60 s. Never written to disk.
- Limits: 8 refs per grant, 3 grants per parent turn, reads_left 20, TTL 15 min, 50k chars per read as today. Default off (`subagent_grants: false`), and a per-task opt-in, never automatic.
- Eligibility at stage and again at every read: ref is live in the parent store, not a web/browser/MCP tool, passes `screen_original`, digest matches the activity record. Refs are exact, never prefix.
- Child-side rule: handle_tool_call consults grants only when `session_id` is a bound child; the read is returned wrapped as untrusted data; grandchildren get nothing; child_toolsets must be a subset of the parent's.
- Fail closed: any missing id, ambiguity, hook error, lifecycle change, restart or expiry yields the existing "unavailable" error with no detail on whether the ref exists.

## 7. Adversarial tests (for a future build)

1. Two parallel delegate_task calls, identical goals: each child gets only its own refs.
2. Goal rewritten by a modify hook between stage and bind: no grant.
3. Staged then blocked delegation: staging entry expires, next child gets nothing.
4. Prefix read (8 hex) of a granted 16-hex ref: refused.
5. Ref containing a credential, or secret appearing after staging: refused at read time.
6. Web-sourced ref staged: rejected at stage.
7. Grandchild reads the child's grant: refused.
8. subagent_stop missed: TTL expiry blocks the read.
9. Gateway restart: grants gone.
10. Child toolset not a subset of the parent's: no bind.
11. Different profile or session with the same ref: refused.
12. Ref in the injected text of a tool result as the only grant source: the system reports a refusal, not a grant.
13. Hook exception at bind: child runs with zero grants.
14. Burst beyond the 3-grant/8-ref/20-read caps: refused, counters honest.

Interim: the parent can already read an original itself and pass only the needed excerpt in the goal, which keeps the human-visible, parent-judged data flow.
