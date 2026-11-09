"""Adaptive full cleanup through native finalization and the host's adoption fence.

The local branch changes tool evidence only. Manual/focused requests and host
guidance retain the native summary path. A useful local candidate that cannot
satisfy the goal is reused as that path's preprocessing, without a second
planning pass or an early cache publication.
"""
from __future__ import annotations

import inspect
from copy import deepcopy

from .hermes_compat import message_budget_tokens
from .policy import plans
from .render import parse_header


_FULL_HOOKS = {
    "_begin_compress_attempt": {"current_tokens", "force"},
    "_finalize_compressed": {
        "compressed", "messages", "n_messages", "spare_pending_images", "pruned_count",
    },
    "_spared_pending_tool_round": {"messages"},
    "_drop_blank_echoes": {"messages"},
    "_protect_head_size": {"messages"},
    "_sanitize_tool_pairs": {"messages"},
    "_find_inflight_user_task": {"messages"},
    "_reappend_inflight_user_task": {"compressed", "inflight"},
}


def check_attempt(engine) -> bool:
    """Fence before touching plugin state; older hosts still take their native path."""
    try:
        from agent.conversation_compression import _raise_if_stale_attempt
    except ImportError:
        return False
    _raise_if_stale_attempt(engine)
    return True


def _native_ready(engine) -> bool:
    try:
        return all(
            names <= set(inspect.signature(getattr(engine, name)).parameters)
            for name, names in _FULL_HOOKS.items()
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _prepare(engine, scope, messages, current_tokens, force):
    body = message_budget_tokens(messages)
    with engine._activity.lifecycle_lock:
        if not engine._pass_is_current(scope):
            return None
        engine._observe_policy_body(body)
        current = engine._planning_input(
            messages, current_tokens, body_tokens=body, full=True, force=force)
        permitted = () if engine._policy.paused else plans(engine._policy.controls, current, engine._tuning())
        first = permitted[0] if permitted else None
        if first is None or not first.consider:
            engine._record_check(
                "decline", current, body, first.reason if first else "not_considered", scope, first)
            return None
        scope.policy_plans = permitted
        # This is also the fallback's retention when no local candidate qualifies.
        scope.plan = permitted[-1]
        scope.settings = engine._settings_for_plan(scope.plan)
        scope.policy_before_tokens = body
        scope.policy_stage_index = len(permitted) - 1
        engine._record_check("attempt", current, body, first.reason, scope, first)
        return current


def _local_candidate(engine, scope, candidate, count, *, summary_requested: bool):
    from .engine import _BODYLESS

    plan = scope.plan
    if summary_requested or count <= 0 or plan is None or not plan.input_complete:
        return None
    for message in candidate:
        if engine._known_render(message) is not None:
            header = parse_header(message["content"])
            if header is not None and header["rung"] in _BODYLESS:
                # The native summary ledger is still needed for these originals.
                return None
    # Native finalization can restate a task behind an existing summary. Price
    # that addition before deciding whether the whole request meets the goal.
    preview = engine._reappend_inflight_user_task(
        engine._sanitize_tool_pairs(deepcopy(candidate)), engine._find_inflight_user_task(scope.messages))
    after = message_budget_tokens(preview)
    gain = scope.policy_before_tokens - after
    # A body-only result cannot erase fixed prompt overhead from the estimate.
    if not preview or max(after, plan.input_tokens - gain) > plan.target_tokens:
        return None
    return preview


def _finalize_local(engine, scope, messages, candidate, count, current_tokens, held):
    with engine._activity.lifecycle_lock:
        if not engine._pass_is_current(scope):
            return messages
        check_attempt(engine)
        telemetry = engine._begin_compress_attempt(current_tokens, False)
        canonical = engine._drop_blank_echoes(messages)
        # Native media/replay cleanup may edit nested values. No caller-owned
        # containers may become its working copy.
        result = engine._finalize_compressed(
            deepcopy(candidate), canonical, len(canonical),
            bool(engine._spared_pending_tool_round(messages)), count,
        )
        if not engine._pass_is_current(scope):
            return messages
        telemetry["method"] = "tameru_local"
        scope.full_local = True
        scope.policy_after_tokens = message_budget_tokens(result)
        held.apply()
        return result


def full_result(
    engine, native_compress, native_pruner, scope, messages, current_tokens,
    focus_topic, force, memory_context, kwargs, *, fenced: bool,
):
    from .engine import _SCOPE

    def native():
        return native_compress(
            messages, current_tokens=current_tokens, focus_topic=focus_topic, force=force,
            memory_context=memory_context, **kwargs,
        )

    if engine._policy.controls.mode == "legacy" or not fenced or not _native_ready(engine):
        return native()
    if len(messages) <= engine._protect_head_size(messages) + 4:
        return native()
    try:
        planning = _prepare(engine, scope, messages, current_tokens, force)
    except Exception:
        engine._telemetry.count("error", where="full_policy_plan")
        return native() if engine._pass_is_current(scope) else messages
    if not engine._pass_is_current(scope):
        return messages
    if planning is None:
        return native()
    with engine._telemetry.hold() as held:
        candidate, count = engine._adaptive_candidates(
            native_pruner, messages, engine.protect_last_n, engine._raw_tail_budget())
    selected = _SCOPE.get()
    if selected is None or not engine._pass_is_current(selected):
        return messages
    # Only one immutable plan tuple is evaluated per public attempt.
    selected.policy_plans = ()
    summary_requested = (
        force or bool(focus_topic) or bool(memory_context and memory_context.strip())
        or any(key != "bypass_cooldown" or value for key, value in kwargs.items())
    )
    local = _local_candidate(engine, selected, candidate, count, summary_requested=summary_requested)
    if local is not None:
        return _finalize_local(engine, selected, messages, local, count, current_tokens, held)
    # Phase 1 of the supported native pipeline receives this exact input list.
    # Its single replay merges the selected telemetry into the outer held batch.
    selected.prepared_prune = (messages, deepcopy(candidate), count, held)
    return native()
