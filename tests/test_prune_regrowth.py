"""After a prune commit the plugin's next prune follows the window, not Hermes' full-trigger runway.

Hermes asks for a regrowth of ``max(reclaimed, proactive_prune_tokens)`` before the next commit. With the plugin's
window-scaled trigger (0.75 of the compaction threshold) that is longer than the stretch to the compaction the
prune is meant to replace: a 200K session regrew raw rows for the rest of its life (oncall 200K replay: +23.7% cost
against stock Hermes, which compacted and reset). ``prune_regrowth_ratio`` shortens the runway to a share of the
threshold and ``reprune_target_ratio`` makes a later commit worth its cache break: it must leave the prompt at or
below that share of the threshold, as a compaction would. Both apply only to the plugin's own, window-scaled
trigger (above 85K tokens of window, or under ~57K where ``small_window_prune_ratio`` replaces a fixed trigger that
would never fire); a user's value, an explicit argument and a 64K window keep Hermes' behaviour. The re-prune target
gate applies to the large windows only: on a small one the fixed part of the prompt alone is ~0.3 of the threshold.
"""
from __future__ import annotations

import copy

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
    engine = TameruContextEngine(settings=settings or TameruSettings(), quiet_mode=True, **{**GATES_OPEN, **kwargs})
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
def test_a_commit_shortens_the_runway_and_restores_every_gate(user_config):
    engine = _engine()
    trigger, min_reclaim = engine.proactive_prune_tokens, engine.proactive_prune_min_reclaim_tokens
    gap = prune_regrowth_tokens(engine._settings, engine.threshold_tokens)
    messages = _logs(4).finish()
    pruned, count = engine.prune_tool_results_only(messages, 500_000)
    assert pruned is not messages and count >= 3
    assert engine._proactive_prune_rearm_tokens == message_budget_tokens(pruned) + gap
    assert engine.proactive_prune_tokens == trigger and engine.proactive_prune_min_reclaim_tokens == min_reclaim


def test_ratio_zero_keeps_hermes_runway(user_config):
    engine = _engine(settings=TameruSettings(prune_regrowth_ratio=0))
    messages = _logs(4).finish()
    pruned, _ = engine.prune_tool_results_only(messages, 500_000)
    runway = max(message_budget_tokens(messages) - message_budget_tokens(pruned), engine.proactive_prune_tokens)
    assert engine._proactive_prune_rearm_tokens == message_budget_tokens(pruned) + runway


def test_the_next_prune_waits_for_the_regrowth_then_fires_below_the_old_trigger(user_config):
    engine = _engine()
    assert engine._regrowth_active()
    gap = prune_regrowth_tokens(engine._settings, engine.threshold_tokens)
    first, _ = engine.prune_tool_results_only(_logs(4).finish(), 500_000)
    assert message_budget_tokens(first) < engine.proactive_prune_tokens

    short = _grow(first, gap // 2)
    assert message_budget_tokens(short) - message_budget_tokens(first) < gap
    res = engine.prune_tool_results_only(short, engine.threshold_tokens - 1)   # under it: the rearm gate decides
    assert res[0] is short and res[1] == 0

    grown = _grow(first, gap)
    assert message_budget_tokens(grown) < engine.proactive_prune_tokens + message_budget_tokens(first)
    pruned, count = engine.prune_tool_results_only(grown, engine.threshold_tokens - 1)
    assert pruned is not grown and count >= 1
    assert engine._proactive_prune_rearm_tokens == message_budget_tokens(pruned) + gap


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
    engine = _engine(settings=TameruSettings(reprune_target_ratio=0.0001))   # nothing can reach the target
    first, _ = engine.prune_tool_results_only(_logs(4).finish(), 500_000)
    grown = _grow(first, prune_regrowth_tokens(engine._settings, engine.threshold_tokens))
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
    before = engine._proactive_prune_rearm_tokens
    engine._arm_regrowth(messages[:3])
    assert engine._proactive_prune_rearm_tokens == before


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
    assert engine._proactive_prune_rearm_tokens == message_budget_tokens(pruned) + gap
    # the target gate would ask a re-prune to leave <= 0.3 of the threshold; here it asks nothing extra
    assert engine._reprune_min_reclaim(_grow(pruned, gap), 123) == 123


@pytest.mark.real_hermes
def test_a_64k_window_keeps_hermes_runway(user_config):
    engine = _engine(window=64_000)
    assert engine.proactive_prune_tokens == 48_000 and not engine._regrowth_active()
