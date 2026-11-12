"""``payback``: the break-even arithmetic, the rules built on it, and how the engine applies them. No Hermes needed."""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from engine_support import make_engine

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.payback import (
    DEADLINE_REQUESTS, PRIOR_SHRINK, RULES, Pricing, avoided_compaction_per_token, required_reclaim, requests_left,
)

W, R = 1.25, 0.10


def _need(rule, **kw):
    kw.setdefault("suffix", 1000)
    kw.setdefault("rewritten", 1000)
    kw.setdefault("share", 0.35)
    kw.setdefault("minimum", 0)
    return required_reclaim(rule, **kw)


# ---- the fixed shares --------------------------------------------------------------------------------
def test_the_fixed_share_uses_the_whole_suffix_and_the_cached_share_only_the_cached_part():
    assert _need("share", suffix=1000, rewritten=600) == 350
    assert _need("share_cached", suffix=1000, rewritten=600) == 210
    assert _need("share_cached", suffix=1000, rewritten=None) == 350            # unknown cached part: the whole suffix
    assert _need("share", suffix=1000, minimum=500) == 500                      # the host's own minimum still applies


# ---- the early rule ----------------------------------------------------------------------------------
@pytest.mark.parametrize("n,expected", [(20, 354), (5, 658), (50, 184)])
def test_the_early_rule_is_the_closed_form(n, expected):
    # g * (W + R*n) >= (W - R) * rewritten  ->  g = ceil(1.15 * 1000 / (1.25 + 0.10 * n))
    assert _need("payback_early", headroom_tokens=n * 100, growth_per_request=100) == expected


def test_a_session_with_few_requests_left_must_remove_more_and_one_with_many_may_remove_less():
    few = _need("payback_early", headroom_tokens=300, growth_per_request=100)
    many = _need("payback_early", headroom_tokens=10_000, growth_per_request=100)
    assert few > _need("share_cached") > many


def test_the_required_amount_is_exactly_the_break_even():
    """At the required gain the prune pays for itself within n requests; one token less and it does not."""
    for cached in (400, 1_000, 25_000):
        for n in (1, 3, 10, 40, 200):
            g = _need("payback_early", suffix=cached, rewritten=cached, headroom_tokens=n * 50, growth_per_request=50)
            extra = (W - R) * cached - W * g              # cost now
            assert R * g * n >= extra - 1e-9, (cached, n, g)
            if g > 1:
                assert R * (g - 1) * n < (W - R) * cached - W * (g - 1), (cached, n, g)


# ---- the deadline rule -------------------------------------------------------------------------------
def test_near_the_threshold_the_avoided_compaction_lowers_the_bar():
    kappa = avoided_compaction_per_token(100_000, None, Pricing())
    assert kappa == pytest.approx((100_000 + (W - R) * PRIOR_SHRINK * 100_000) / ((1 - PRIOR_SHRINK) * 100_000))
    early_only = _need("payback_early", headroom_tokens=100, growth_per_request=100)          # n = 1
    with_deadline = _need("payback", headroom_tokens=100, growth_per_request=100, kappa=kappa)
    assert with_deadline == math.ceil((W - R) * 1000 / (W + kappa)) < early_only
    far = dict(headroom_tokens=100_000, growth_per_request=100, kappa=kappa)                  # n = 1000: no deadline credit
    assert _need("payback", **far) == _need("payback_early", headroom_tokens=100_000, growth_per_request=100)
    assert DEADLINE_REQUESTS == 2


def test_kappa_follows_what_hermes_actually_kept():
    prior = avoided_compaction_per_token(100_000, None, Pricing())
    small = avoided_compaction_per_token(100_000, 10_000, Pricing())          # a deep compaction: little left to rewrite
    large = avoided_compaction_per_token(100_000, 70_000, Pricing())          # a shallow one: the summary saves little per token
    assert small < prior < large
    assert avoided_compaction_per_token(100_000, 999_999, Pricing()) == prior  # nonsense input falls back to the prior


# ---- missing information falls back, it does not guess -----------------------------------------------
def test_without_a_growth_rate_the_payback_rules_use_the_cached_share():
    for rule in ("payback_early", "payback"):
        assert _need(rule, headroom_tokens=5_000, growth_per_request=None, rewritten=800) == _need("share_cached", rewritten=800)
        assert _need(rule, headroom_tokens=5_000, growth_per_request=0, rewritten=800) == _need("share_cached", rewritten=800)
        assert _need(rule, headroom_tokens=None, growth_per_request=100, rewritten=800) == _need("share_cached", rewritten=800)
    assert requests_left(5_000, 0) is None and requests_left(5_000, 100) == 50 and requests_left(-5, 100) == 0


# ---- accrual -----------------------------------------------------------------------------------------
def test_accrual_asks_less_as_the_carried_cost_grows_and_nothing_once_it_covers_the_rewrite():
    cold = _need("accrual", carried=0.0)
    warm = _need("accrual", carried=300.0)
    assert cold == math.ceil((W - R) * 1000 / W) and warm < cold
    assert _need("accrual", carried=(W - R) * 1000, minimum=0) == 1


# ---- the contract ------------------------------------------------------------------------------------
def test_an_unknown_rule_and_nonsense_prices_are_refused():
    with pytest.raises(ValueError):
        _need("hope")
    with pytest.raises(ValueError):
        Pricing(write=0.1, read=0.2)
    assert set(RULES) == {"share", "share_cached", "payback_early", "payback", "accrual"}


def test_the_rules_are_deterministic():
    kw = dict(headroom_tokens=7_300, growth_per_request=211, kappa=2.1, carried=40.0, rewritten=4_321, suffix=5_000)
    for rule in RULES:
        assert required_reclaim(rule, share=0.35, minimum=3, **kw) == required_reclaim(rule, share=0.35, minimum=3, **kw)


# ---- the engine applies them -------------------------------------------------------------------------
def _engine(rule):
    return make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", decision_rule=rule))


def _plan(**over):
    base = dict(urgent=False, minimum_reclaim_share=0.35, minimum_reclaim_tokens=0, input_tokens=60_000,
                required_reclaim=lambda suffix: max(1, math.ceil(suffix * (0 if over.get("urgent") else 0.35))))
    base.update(over)
    return SimpleNamespace(**base)


def test_the_share_rule_is_unchanged_and_an_urgent_plan_keeps_its_waiver_under_every_rule():
    engine = _engine("share")
    assert engine._required_reclaim(_plan(), 10_000, 4_000) == 3_500
    for rule in RULES:
        assert _engine(rule)._required_reclaim(_plan(urgent=True), 10_000, 4_000) == 1


def test_the_payback_rule_reads_growth_and_headroom_from_the_engine():
    engine = _engine("payback_early")
    threshold = int(engine._policy_threshold_tokens())
    engine._policy.growth.extend([800, 1_000, 1_200])
    plan = _plan(input_tokens=threshold - 20_000)             # median growth 1000 a request: 20 requests left
    got = engine._required_reclaim(plan, suffix=9_000, rewritten=3_000)
    assert got == math.ceil((W - R) * 3_000 / (W + R * 20))
    cheaper = _plan(input_tokens=threshold - 100_000)         # far more room: a smaller prune is enough
    assert engine._required_reclaim(cheaper, suffix=9_000, rewritten=3_000) < got


def test_the_accrual_rule_resets_its_carried_cost_at_a_commit_and_hosts_compaction_sets_kappa():
    engine = _engine("accrual")
    engine._policy.carry = 123.0
    engine._record_commit(SimpleNamespace(plan=_plan(stage="standard", reason="test", input_tokens=1, minimum_reclaim_tokens=0, regrowth_tokens=1,
                                                      controls=None), full=False, full_local=False, policy_before_tokens=1000,
                                          policy_after_tokens=800, policy_stage_index=0, changes={}))
    assert engine._policy.carry == 0.0
    engine.observe_host_compaction(session_id="s1", kind="auto", before_tokens=90_000, after_tokens=30_000)
    assert engine._policy.host_after_tokens == 30_000
