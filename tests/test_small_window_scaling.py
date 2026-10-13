"""The retained budgets shrink on a window below the 64K reference (``small_window_threshold_tokens``).

The fixed 6,000 chars of retained bodies and 8,000 chars of retained error rows were sized on a 64K window
(compaction threshold 54,400 tokens). On a 32K window (threshold 27,200) they are twice the share of the
context: the post-compaction context came out bigger and the next compaction came sooner (replay, i18n at 32K:
five compactions where stock made two). Below the reference both scale in proportion; from it up nothing changes.
"""
from __future__ import annotations

import copy

import pytest
from engine_support import Chat, make_engine, prune_pass, server_log

from hermes_tameru_plugin.config import (
    TameruSettings, scaled_error_budget, scaled_retained_budget, window_scale,
)
from hermes_tameru_plugin.render import parse_header

DEFAULT = TameruSettings()


@pytest.mark.parametrize(("threshold", "scale"), [
    (0, 1.0), (None, 1.0), ("junk", 1.0), (-5, 1.0),
    (54_400, 1.0), (54_401, 1.0), (96_000, 1.0), (192_000, 1.0),
    (27_200, 0.5), (13_600, 0.25),
])
def test_window_scale(threshold, scale):
    from hermes_tameru_plugin.config import _threshold

    assert window_scale(DEFAULT, _threshold(threshold)) == pytest.approx(scale)


def test_zero_reference_switches_scaling_off():
    off = TameruSettings(small_window_threshold_tokens=0)
    assert window_scale(off, 10_000) == 1.0
    assert scaled_retained_budget(off, 27_200) == 6000
    assert scaled_error_budget(off, 27_200) == 8000


@pytest.mark.parametrize(("threshold", "extract", "errors"), [
    (27_200, 3000, 4000),      # 32K window: half the reference
    (40_800, 4500, 6000),      # 48K window: three quarters
    (54_400, 6000, 8000),      # 64K window: the reference, unchanged
    (96_000, 9600, 8000),      # larger windows: unchanged (ratio rule for the extract budget)
    (192_000, 19_200, 8000),
])
def test_scaled_budgets(threshold, extract, errors):
    assert scaled_retained_budget(DEFAULT, threshold) == extract
    assert scaled_error_budget(DEFAULT, threshold) == errors


def test_the_ratio_still_lifts_a_budget_above_the_scaled_floor():
    settings = TameruSettings(retained_extract_budget_ratio=0.5)
    assert scaled_retained_budget(settings, 27_200) == 13_600


def _engine(settings: TameruSettings, window: int):
    engine = make_engine(settings)
    engine.update_model("test-model", window)
    return engine


def test_the_engine_follows_a_model_switch_and_a_clone():
    engine = _engine(DEFAULT, 32_000)
    small = engine.threshold_tokens
    assert small < DEFAULT.small_window_threshold_tokens
    assert engine._retained_budget() == round(6000 * small / 54_400)
    assert engine._retained_error_budget() == round(8000 * small / 54_400)
    engine.update_model("test-model", 400_000)
    assert engine._retained_budget() == max(6000, round(0.10 * engine.threshold_tokens)) > 6000
    assert engine._retained_error_budget() == 8000
    assert copy.deepcopy(engine)._retained_budget() == engine._retained_budget()


def _logs(rows: int = 5) -> tuple[list[dict], list[int]]:
    chat = Chat("why did the payment gateway time out?")
    idxs = [chat.tool("terminal", {"command": f"tail -n {400 + i} app.log"}, server_log(i)) for i in range(rows)]
    return chat.finish(), idxs


def _body_chars(messages: list[dict], idxs: list[int]) -> int:
    return sum(
        len(messages[i]["content"]) for i in idxs
        if (parse_header(messages[i]["content"]) or {}).get("rung") in ("brief", "extract")
    )


def test_a_small_window_keeps_fewer_body_chars_within_its_budget():
    messages, idxs = _logs()
    scaled = _engine(DEFAULT, 32_000)
    fixed = _engine(TameruSettings(small_window_threshold_tokens=0), 32_000)
    out_scaled, _ = prune_pass(scaled, messages)
    out_fixed, _ = prune_pass(fixed, messages)
    assert scaled._retained_budget() < fixed._retained_budget() == 6000
    assert _body_chars(out_scaled, idxs) <= scaled._retained_budget()
    assert _body_chars(out_scaled, idxs) <= _body_chars(out_fixed, idxs) <= fixed._retained_budget()
    assert _body_chars(out_fixed, idxs) > 0
