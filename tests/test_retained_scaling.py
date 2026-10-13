"""The retained-extract budget follows the model's window (``retained_extract_budget_ratio``).

On 128K to 256K windows a fixed 6,000 chars threw away facts a larger budget keeps (replay, 200K: 11 gold facts
at 6,000, 13 at 15,000) for 1-2% of the cost; at 64K a bigger budget lost facts, so small windows keep the
floor. The effective budget is ``max(retained_extract_budget_chars, round(ratio * compaction threshold))``,
computed when a pass needs it, because the threshold moves with ``update_model``.
"""
from __future__ import annotations

import copy

import pytest
from engine_support import Chat, make_engine, prune_pass

from hermes_tameru_plugin.config import TameruSettings, scaled_retained_budget
from hermes_tameru_plugin.render import parse_header


@pytest.mark.parametrize(("threshold", "expected"), [
    (0, 6000), (None, 6000), ("junk", 6000), (-5, 6000),
    (54_400, 6000),            # 64K window: 5,440 is under the floor
    (60_000, 6000), (96_000, 9600), (150_000, 15_000), (192_000, 19_200),
])
def test_scaled_retained_budget(threshold, expected):
    assert scaled_retained_budget(TameruSettings(), threshold) == expected


def test_ratio_zero_is_the_fixed_budget():
    assert scaled_retained_budget(TameruSettings(retained_extract_budget_ratio=0), 192_000) == 6000


def test_the_floor_is_a_floor_not_a_cap():
    settings = TameruSettings(retained_extract_budget_chars=30_000)
    assert scaled_retained_budget(settings, 150_000) == 30_000
    assert scaled_retained_budget(TameruSettings(retained_extract_budget_chars=0), 150_000) == 15_000


@pytest.mark.parametrize(("window", "expected"), [(64_000, 6000), (128_000, 9600), (200_000, 15_000), (256_000, 19_200)])
def test_the_engine_budget_follows_hermes_threshold(window, expected):
    engine = make_engine(config_context_length=window)
    threshold = engine.threshold_tokens
    assert engine._retained_budget() == max(6000, round(0.10 * threshold))
    if threshold == window * 3 // 4:   # Hermes main compacts at 75%
        assert engine._retained_budget() == expected


def test_a_model_switch_rescales_and_a_clone_follows():
    engine = make_engine(config_context_length=64_000)
    assert engine._retained_budget() == 6000
    engine.update_model("test-model", 200_000)
    assert engine._retained_budget() == max(6000, round(0.10 * engine.threshold_tokens))
    assert engine._retained_budget() > 6000
    assert copy.deepcopy(engine)._retained_budget() == engine._retained_budget()


def test_ratio_zero_keeps_the_configured_budget_on_a_big_window():
    engine = make_engine(TameruSettings(retained_extract_budget_ratio=0), config_context_length=256_000)
    assert engine._retained_budget() == 6000


def _logs(rows: int = 5) -> tuple[list[dict], list[int]]:
    from engine_support import server_log

    chat = Chat("why did the payment gateway time out?")
    idxs = [chat.tool("terminal", {"command": f"tail -n {400 + i} app.log"}, server_log(i)) for i in range(rows)]
    return chat.finish(), idxs


def _bodied(messages: list[dict], idxs: list[int]) -> int:
    return sum(1 for i in idxs if (parse_header(messages[i]["content"]) or {}).get("rung") in ("brief", "extract"))


def test_a_large_window_keeps_more_bodies_than_a_small_one():
    messages, idxs = _logs()
    small = make_engine(config_context_length=64_000)
    large = make_engine(config_context_length=256_000)
    out_small, _ = prune_pass(small, messages)
    out_large, _ = prune_pass(large, messages)
    assert _bodied(out_large, idxs) > _bodied(out_small, idxs)
    # ... and never more than the budget it was given
    for engine, out in ((small, out_small), (large, out_large)):
        bodies = sum(
            len(out[i]["content"]) for i in idxs
            if "\n" in out[i]["content"] and parse_header(out[i]["content"])["rung"] != "errors"
        )
        assert bodies <= engine._retained_budget()
