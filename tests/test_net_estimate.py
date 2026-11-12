"""``net_estimate``: the signed estimate is arithmetic on recorded numbers, off by default, and says when it is unsure."""
from __future__ import annotations

from engine_support import make_engine
from test_autotune_engine import bind_meta

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.net_estimate import describe, estimate


def test_a_cleanup_that_pays_back_is_a_saving():
    result = estimate([(10_000, 12_000, 5)], responses_now=25)     # saving 10k x 20 replies x 0.1 = 20,000; cost 12k x 1.15 - 10k x 1.25 = 1,300
    assert result["sign"] == "saving" and result["net"] == 18_700 and result["unknown_cost"] == 0


def test_a_cleanup_with_no_replies_after_it_is_a_cost():
    result = estimate([(1_000, 30_000, 7)], responses_now=7)
    assert result["sign"] == "cost" and result["net"] == -33_250          # 30k x 1.15 - 1k x 1.25
    assert describe(result).startswith("cost about 33,250")


def test_an_unmeasured_rewrite_is_named_not_hidden():
    result = estimate([(5_000, None, 0)], responses_now=10)
    assert result["unknown_cost"] == 1 and "leans toward saving" in describe(result)


def test_nothing_recorded_says_so():
    assert estimate([], 3) is None and describe(None) == "no cleanups yet"
    assert estimate([(0, 10, 0), ("x", 1, 0)], 3) is None


def test_the_credit_per_cleanup_is_capped():
    assert estimate([(1_000, 0, 0)], responses_now=10_000)["saving"] == 1_000 * 200 * 0.1


def _stats(**settings):
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **settings))
    bind_meta(engine, None, "s1")
    engine._policy.cost_events.append((8_000, 1_000, 0))
    engine._responses = 10
    return engine, engine._activity_registry.command("stats", session_id="s1", platform="desktop")


def test_stats_show_the_estimate_only_when_it_is_on():
    _, off = _stats()
    assert "Net estimate" not in off
    _, on = _stats(net_estimate=True)
    assert "Net estimate" in on and "saved about" in on and "estimate" in on


def test_the_rewrite_cost_does_not_count_tokens_the_cleanup_removed():
    # The removed tokens are not written to the cache at all, so they cannot be part of the rewrite cost.
    whole = estimate([(1_000, 1_000, 0)], responses_now=0)
    assert whole["cost"] == 0                      # the cleanup removed the whole suffix: nothing is rewritten
    half = estimate([(500, 1_000, 0)], responses_now=0)
    assert half["cost"] == round(1_000 * 1.15 - 500 * 1.25)


def test_a_recorded_rewritten_part_replaces_the_whole_suffix_in_the_cost():
    # The new assistant call and tool results are written to the cache either way; only the cached part is a rewrite.
    detail = {"rewritten": 2_000, "unsent": 1_000}
    result = estimate([(500, 3_000, 0, detail)], responses_now=0)
    assert result["cost"] == round(2_000 * 1.15 - 500 * 1.25)
    assert estimate([(500, 3_000, 0)], responses_now=0)["cost"] == round(3_000 * 1.15 - 500 * 1.25)    # the old 3-field event still works
