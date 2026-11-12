"""Frontier shortening (1.10, experimental, off): the pieces that decide when it applies. The replay verdict is in docs/FRONTIER-1.10.md."""
from __future__ import annotations

from dataclasses import make_dataclass

from engine_support import make_engine

from hermes_tameru_plugin.adaptive import _pending_round
from hermes_tameru_plugin.config import TameruSettings


def _plan(**over):
    base = dict(consider=False, reason="ample_room", input_tokens=40_000, urgent=False, minimum_reclaim_tokens=4096, minimum_result_chars=8000)
    base.update(over)
    return make_dataclass('P', list(base), frozen=True)(**base)


def _engine(**settings):
    return make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **settings))


def test_the_pending_round_is_the_last_assistant_call_and_its_results():
    msgs = [{"role": "user"}, {"role": "assistant", "tool_calls": [1]}, {"role": "tool"}, {"role": "assistant", "tool_calls": [2]}, {"role": "tool"}, {"role": "tool"}]
    assert _pending_round(msgs) == 3
    assert _pending_round([{"role": "user"}]) == 2


def test_frontier_applies_only_when_enabled_below_activation_and_not_paused():
    assert _engine()._frontier_plan(_plan()) is None                               # off by default
    on = _engine(frontier=True)
    plan = on._frontier_plan(_plan())
    assert plan is not None and plan.reason == "frontier" and plan.consider and plan.minimum_reclaim_tokens == 0
    assert plan.minimum_result_chars == 12_000                                       # the larger of the plan's floor and frontier_min_chars
    assert on._frontier_plan(_plan(consider=True, reason="pressure")) is None       # above activation the normal pass decides
    assert on._frontier_plan(None) is None
    on._policy.paused = True
    assert on._frontier_plan(_plan()) is None


def test_the_frontier_settings_parse():
    assert TameruSettings().frontier is False and TameruSettings().frontier_min_chars == 12_000
