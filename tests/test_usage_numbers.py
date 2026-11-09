"""Real usage numbers: what the provider reported for the last replies, shown honestly (or not at all)."""
from __future__ import annotations

import copy

import pytest
from engine_support import make_engine

from hermes_tameru_plugin.config import TameruSettings


def _engine():
    return make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))


def _usage(prompt, read=0, write=0, fresh=None):
    return {"prompt_tokens": prompt, "completion_tokens": 50, "total_tokens": prompt + 50,
            "input_tokens": prompt - read if fresh is None else fresh, "output_tokens": 50,
            "cache_read_tokens": read, "cache_write_tokens": write, "reasoning_tokens": 0}


def test_nothing_is_measured_before_the_first_reply():
    snap = _engine().usage_snapshot()
    assert snap["measured"] is False and snap["cache_hit_rate"] is None and snap["samples"] == 0


def test_the_cache_share_is_the_part_of_the_prompt_read_from_cache():
    engine = _engine()
    engine.update_from_response(_usage(10_000, read=8_200, write=300))
    snap = engine.usage_snapshot()
    assert snap["measured"] is True and snap["cache_hit_rate"] == 82
    assert snap["prompt"] == 10_000 and snap["cache_read"] == 8_200 and snap["cache_write"] == 300


def test_a_provider_that_never_reports_cache_use_shows_no_percentage_not_zero():
    engine = _engine()
    for _ in range(3):
        engine.update_from_response(_usage(5_000))
    snap = engine.usage_snapshot()
    assert snap["measured"] is True and snap["cache_hit_rate"] is None


def test_once_cache_use_has_been_seen_a_zero_reply_is_a_real_zero():
    engine = _engine()
    engine.update_from_response(_usage(10_000, read=9_000))
    engine.update_from_response(_usage(12_000, read=0, write=12_000))      # the cache was rebuilt
    assert engine.usage_snapshot()["cache_hit_rate"] == 0


@pytest.mark.parametrize("bad", [{}, {"prompt_tokens": "lots"}, {"prompt_tokens": -5}, {"prompt_tokens": None, "cache_read_tokens": 7},
                                 {"prompt_tokens": True}, None, "text", 5])
def test_a_malformed_usage_dict_never_raises_and_never_invents_numbers(bad):
    engine = _engine()
    try:
        engine.update_from_response(bad)
    except Exception as exc:       # only Hermes' own parent method may complain, never Tameru's bookkeeping
        assert "tameru" not in repr(exc).lower()
    snap = engine.usage_snapshot()
    assert snap["cache_hit_rate"] is None
    assert snap["prompt"] in (None, 0)


def test_cache_read_can_never_exceed_the_prompt():
    engine = _engine()
    engine.update_from_response(_usage(1_000, read=5_000))
    assert engine.usage_snapshot()["cache_hit_rate"] == 100


def test_only_the_last_eight_replies_are_kept_and_a_clone_starts_independent():
    engine = _engine()
    for index in range(20):
        engine.update_from_response(_usage(1_000 + index, read=500))
    assert engine.usage_snapshot()["samples"] == 8
    clone = copy.deepcopy(engine)
    clone.update_from_response(_usage(2_000, read=0, write=2_000))
    assert engine.usage_snapshot()["prompt"] == 1_019, "a clone does not write into the original"


def test_the_snapshot_and_the_status_carry_the_usage_block_and_a_new_session_clears_it():
    from test_recovery_actions import _engine_with_one_shortened_output
    engine, *_ = _engine_with_one_shortened_output()
    engine.update_from_response(_usage(20_000, read=15_000))
    progress = engine.activity_snapshot()["progress"]
    assert progress["usage"]["cache_hit_rate"] == 75
    engine.on_session_reset()
    assert engine.usage_snapshot()["measured"] is False


# ---- timing from post_api_request: numbers only, first chunk only when it was reported ----------------------------
def test_response_timing_is_recorded_and_the_first_chunk_is_optional():
    from engine_support import make_engine
    engine = make_engine()
    engine.update_from_response({"prompt_tokens": 1000, "cache_read_tokens": 800, "cache_write_tokens": 0, "input_tokens": 200})
    engine.record_response_timing(2.5, 100.0, None)
    snap = engine.usage_snapshot()
    assert snap["api_ms"] == 2500 and snap["first_chunk_ms"] is None and snap["fresh_input"] == 200
    engine.record_response_timing(2.5, 100.0, 100.4)
    assert engine.usage_snapshot()["first_chunk_ms"] == 400


@pytest.mark.parametrize("bad", [None, "x", -1, True, 10**9, float("nan")])
def test_a_malformed_duration_is_not_a_number(bad):
    from engine_support import make_engine
    engine = make_engine()
    engine.update_from_response({"prompt_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0, "input_tokens": 10})
    engine.record_response_timing(bad, 1.0, None)
    assert engine.usage_snapshot()["api_ms"] is None
