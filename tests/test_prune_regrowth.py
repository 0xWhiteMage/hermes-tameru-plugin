"""After a prune commit the plugin's next prune follows the window, not Hermes' full-trigger runway.

Hermes asks for a regrowth of ``max(reclaimed, proactive_prune_tokens)`` before the next commit. With the plugin's
own, window-scaled trigger a committed prune re-arms at the retained body plus ``max(gap, required reclaim)`` —
``prune_regrowth_ratio`` shortens the runway to a share of the compaction threshold (a prune that needed a full
trigger-sized regrowth behaves like a compaction against stock Hermes, which compacted and reset). Adaptive policy
is exercised separately in test_adaptive.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from engine_support import Chat, server_log

import hermes_tameru_plugin.engine as engine_mod
from hermes_tameru_plugin.config import (
    TameruSettings,
    prune_regrowth_tokens,
    prune_trigger_follows_window,
    reprune_gate_applies,
)
from hermes_tameru_plugin.engine import TameruContextEngine
from hermes_tameru_plugin.hermes_compat import message_budget_tokens

GATES_OPEN = {"proactive_prune_min_reclaim_tokens": 0, "proactive_prune_min_result_chars": 200, "protect_last_n": 4}


@pytest.fixture
def user_config(monkeypatch):
    def serve(compression=None):
        merged = {"compression": dict(compression or {})}
        raw = {"compression": dict(compression or {})} if compression else {}
        monkeypatch.setattr(engine_mod, "read_hermes_config", lambda: (merged, raw))

    serve()
    return serve


def _engine(window: int = 200_000, settings: TameruSettings | None = None, **kwargs) -> TameruContextEngine:
    legacy = replace(settings or TameruSettings(), policy_mode="legacy")
    engine = TameruContextEngine(settings=legacy, quiet_mode=True, **{**GATES_OPEN, **kwargs})
    engine.update_model("test-model", window)
    return engine


def _grow(base: list[dict], tokens: int, start: int = 20) -> list[dict]:
    """``base`` plus tool rounds until it has regrown by more than ``tokens``."""
    grown, i = list(base), start
    while message_budget_tokens(grown) - message_budget_tokens(base) <= tokens:
        grown += _logs(1, start=i).messages[2:]
        i += 1
    return grown


def _logs(n: int, start: int = 0) -> Chat:
    chat = Chat("why did the payment gateway time out?")
    for i in range(start, start + n):
        chat.tool("terminal", {"command": f"tail -n {400 + i} app.log"}, server_log(i))
    return chat


# ---- the pure helpers ----------------------------------------------------------------------------
@pytest.mark.parametrize(("threshold", "expected"), [
    (0, 0), (None, 0), ("junk", 0), (-1, 0), (54_400, 10_880), (96_000, 19_200), (150_000, 30_000),
])
def test_prune_regrowth_tokens(threshold, expected):
    assert prune_regrowth_tokens(TameruSettings(), threshold) == expected
    assert prune_regrowth_tokens(TameruSettings(prune_regrowth_ratio=0), threshold) == 0


@pytest.mark.parametrize(("threshold", "expected"), [
    (0, False), (None, False), ("junk", False), (54_400, False),   # 64K window: the 48K floor sets the trigger
    (64_000, False), (64_001, True), (96_000, True), (192_000, True),
    (27_200, True), (40_800, True), (48_000, True),                # under ~57K: the fixed trigger is not below the threshold
    (48_001, False), (51_000, False),
])
def test_prune_trigger_follows_window(threshold, expected):
    assert prune_trigger_follows_window(TameruSettings(), threshold) is expected


@pytest.mark.parametrize(("threshold", "expected"), [
    (0, False), (None, False), ("junk", False), (27_200, False), (40_800, False), (54_400, False), (64_000, False),
    (64_001, True), (96_000, True), (192_000, True),
])
def test_the_reprune_target_gate_is_for_large_windows(threshold, expected):
    assert reprune_gate_applies(TameruSettings(), threshold) is expected
    assert not reprune_gate_applies(TameruSettings(default_proactive_prune_ratio=0), 150_000)


def test_the_trigger_never_follows_the_window_when_scaling_is_off():
    assert not prune_trigger_follows_window(TameruSettings(default_proactive_prune_ratio=0), 150_000)
    assert not prune_trigger_follows_window(TameruSettings(default_proactive_prune_tokens=0), 150_000)


def test_message_budget_tokens_matches_hermes_per_message():
    from agent.context_compressor import _estimate_msg_budget_tokens

    chat = _logs(2)
    assert message_budget_tokens(chat.finish()) == sum(_estimate_msg_budget_tokens(m) for m in chat.messages)
    assert message_budget_tokens([]) == 0


# ---- the engine ----------------------------------------------------------------------------------
def test_a_commit_shortens_the_runway_and_preserves_configured_gates(user_config):
    engine = _engine()
    trigger, min_reclaim = engine.proactive_prune_tokens, engine.proactive_prune_min_reclaim_tokens
    gap = prune_regrowth_tokens(engine._settings, engine.threshold_tokens)
    messages = _logs(4).finish()
    pruned, count = engine.prune_tool_results_only(messages, 500_000)
    assert pruned is not messages and count >= 3
    reclaimed = message_budget_tokens(messages) - message_budget_tokens(pruned)
    native_after = message_budget_tokens(pruned)
    assert reclaimed > gap                                    # otherwise the shortened mark is indistinguishable
    assert engine._proactive_prune_rearm_tokens == native_after + max(gap, min_reclaim)
    assert engine._proactive_prune_rearm_tokens < native_after + max(reclaimed, trigger, min_reclaim)
    assert engine.proactive_prune_tokens == trigger and engine.proactive_prune_min_reclaim_tokens == min_reclaim


def test_ratio_zero_keeps_hermes_runway(user_config):
    engine = _engine(settings=TameruSettings(prune_regrowth_ratio=0))
    messages = _logs(4).finish()
    pruned, _ = engine.prune_tool_results_only(messages, 500_000)
    reclaimed = message_budget_tokens(messages) - message_budget_tokens(pruned)
    native_after = message_budget_tokens(pruned)
    assert engine._proactive_prune_rearm_tokens == native_after + max(
        reclaimed, engine.proactive_prune_tokens, engine.proactive_prune_min_reclaim_tokens)


def test_the_next_prune_waits_for_the_regrowth_then_fires_below_the_old_trigger(user_config):
    engine = _engine()
    assert engine._regrowth_active()
    first, _ = engine.prune_tool_results_only(_logs(4).finish(), 500_000)
    assert message_budget_tokens(first) < engine.proactive_prune_tokens
    rearm = engine.get_policy_status()["rearm_body_tokens"]
    runway = rearm - message_budget_tokens(first)

    short = _grow(first, runway // 2)
    assert message_budget_tokens(short) < rearm
    res = engine.prune_tool_results_only(short, engine.threshold_tokens - 1)   # under it: the rearm gate decides
    assert res[0] is short and res[1] == 0

    grown = _grow(first, runway)
    assert message_budget_tokens(grown) < engine.proactive_prune_tokens + message_budget_tokens(first)
    pruned, count = engine.prune_tool_results_only(grown, engine.threshold_tokens - 1)
    assert pruned is not grown and count >= 1
    assert engine.get_policy_status()["rearm_body_tokens"] >= message_budget_tokens(grown)


def test_a_re_prune_must_leave_the_prompt_at_the_target_share(user_config):
    engine = _engine()
    first, _ = engine.prune_tool_results_only(_logs(4).finish(), 500_000)
    target = round(engine._settings.reprune_target_ratio * engine.threshold_tokens)
    grown = _grow(first, target)
    assert message_budget_tokens(grown) > target
    assert engine._reprune_min_reclaim(grown, 0) == message_budget_tokens(grown) - target
    assert engine._reprune_min_reclaim(first, 0) == 0                  # already under the target: nothing extra to ask
    assert engine._reprune_min_reclaim(grown, 10**9) == 10**9          # Hermes' own minimum is never lowered


def test_a_declined_re_prune_leaves_the_transcript_and_the_gates_alone(user_config):
    engine = _engine(settings=TameruSettings(reprune_target_ratio=0.0001, policy_mode="legacy"))   # nothing can reach the target
    first, _ = engine.prune_tool_results_only(_logs(4).finish(), 500_000)
    runway = engine.get_policy_status()["rearm_body_tokens"] - message_budget_tokens(first)
    grown = _grow(first, runway)
    trigger, min_reclaim, rearm = (
        engine.proactive_prune_tokens, engine.proactive_prune_min_reclaim_tokens, engine._proactive_prune_rearm_tokens,
    )
    res = engine.prune_tool_results_only(grown, engine.threshold_tokens - 1)
    assert res[0] is grown and res[1] == 0
    assert (engine.proactive_prune_tokens, engine.proactive_prune_min_reclaim_tokens) == (trigger, min_reclaim)
    assert engine._proactive_prune_rearm_tokens == rearm


def test_the_first_prune_has_no_target_gate(user_config):
    engine = _engine()
    messages = _logs(4).finish()
    assert engine._reprune_min_reclaim(messages, 123) == 123
    assert _engine(settings=TameruSettings(reprune_target_ratio=0))._reprune_min_reclaim(messages, 7) == 7


def test_a_compaction_rearms_the_plugin_trigger(user_config):
    engine = _engine()
    engine.prune_tool_results_only(_logs(4).finish(), 500_000)
    assert engine._regrowth_trigger(engine.proactive_prune_tokens) < engine.proactive_prune_tokens
    engine._proactive_prune_rearm_tokens = 0   # what Hermes does on a compaction or a session reset
    assert engine._regrowth_trigger(engine.proactive_prune_tokens) == engine.proactive_prune_tokens


@pytest.mark.parametrize("how", [
    "user", "argument", pytest.param("small_window", marks=pytest.mark.real_hermes), "disabled",   # 64K: Hermes' threshold
])
def test_only_the_plugins_own_window_scaled_trigger_changes(user_config, how):
    kwargs: dict = {}
    window = 200_000
    settings = None
    if how == "user":
        user_config({"proactive_prune_tokens": 30_000})
    elif how == "argument":
        kwargs["proactive_prune_tokens"] = 30_000
    elif how == "small_window":
        window = 64_000
    else:
        settings = TameruSettings(enabled=False)
    engine = _engine(window, settings, **kwargs)
    messages = _logs(4).finish()
    engine.prune_tool_results_only(messages, 500_000)
    assert engine._regrowth_trigger(77) == 77
    assert engine._reprune_min_reclaim(messages, 5) == 5


def test_a_clone_keeps_the_rule(user_config):
    engine = _engine()
    engine.prune_tool_results_only(_logs(4).finish(), 500_000)
    clone = copy.deepcopy(engine)
    assert clone._regrowth_trigger(clone.proactive_prune_tokens) == engine._regrowth_trigger(engine.proactive_prune_tokens)


# ---- a small window ------------------------------------------------------------------------------
@pytest.mark.real_hermes   # the compaction threshold of a 32K / 64K window is Hermes'
def test_a_32k_window_follows_the_window_with_regrowth_but_without_the_target_gate(user_config):
    engine = _engine(window=32_000, proactive_prune_min_reclaim_tokens=0)
    assert engine.threshold_tokens == 27_200 and engine.proactive_prune_tokens == 16_320
    assert engine._regrowth_active()
    messages = _logs(4).finish()
    gap = prune_regrowth_tokens(engine._settings, engine.threshold_tokens)
    pruned, count = engine.prune_tool_results_only(messages, 500_000)
    assert pruned is not messages and count >= 3
    native_after = message_budget_tokens(pruned)
    assert engine._proactive_prune_rearm_tokens == native_after + max(gap,
        engine._reprune_min_reclaim(messages, engine._raw_native("proactive_prune_min_reclaim_tokens")))
    # the target gate would ask a re-prune to leave <= 0.3 of the threshold; here it asks nothing extra
    assert engine._reprune_min_reclaim(_grow(pruned, gap), 123) == 123


@pytest.mark.real_hermes
def test_a_64k_window_keeps_hermes_runway(user_config):
    engine = _engine(window=64_000)
    assert engine.proactive_prune_tokens == 48_000 and not engine._regrowth_active()
