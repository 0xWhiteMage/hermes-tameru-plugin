"""Budget arithmetic and independent public policy controls."""
from dataclasses import replace

import pytest

from hermes_tameru_plugin.policy import Controls, PlanningInput, RequestBudget, Retention, plans


BASE = Retention(6000, 1200, 6000, 8000, 1600, 10_000)


def planning_input(**changes):
    current = PlanningInput(
        RequestBudget(128_000, 32_000, source="host"),
        current_tokens=65_000, input_complete=True, host_threshold_tokens=72_000,
        retention=BASE, minimum_reclaim_tokens=1000, minimum_result_chars=8000,
    )
    return replace(current, **changes)


def test_window_and_net_limits_count_the_output_allowance_once():
    assert RequestBudget(128_000, 32_000).usable_input_tokens == 96_000
    assert RequestBudget(128_000, 32_000, 96_000).usable_input_tokens == 96_000
    assert RequestBudget(input_limit_tokens=96_000).usable_input_tokens == 96_000
    assert RequestBudget(128_000).usable_input_tokens is None
    assert RequestBudget(128_000, 0).usable_input_tokens == 128_000
    assert RequestBudget(128_000, 128_000).usable_input_tokens == 0
    assert RequestBudget(128_000, 256_000).invalid_reason == "no_input_space"


@pytest.mark.parametrize("bad", [True, -1, 1.5, "128000"])
def test_budget_rejects_non_count_values(bad):
    with pytest.raises(ValueError):
        RequestBudget(window_tokens=bad)


def test_unknown_reserve_uses_the_host_trigger_without_claiming_window_occupancy():
    current = planning_input(budget=RequestBudget(128_000), current_tokens=40_000)
    plan = plans(Controls(), current)[-1]
    assert plan.usable_input_tokens is None
    assert plan.budget_source == "unknown"
    assert plan.pressure_basis == "host_trigger"
    assert plan.pressure == pytest.approx(5 / 9)
    assert plan.activation_tokens == 43_200
    assert plan.consider is False


def test_previous_request_capacity_is_explicitly_an_estimate():
    current = planning_input(
        budget=RequestBudget(128_000, 32_000, source="previous_request", estimated=True))
    plan = plans(Controls(), current)[0]
    assert plan.usable_input_tokens == 96_000
    assert plan.pressure == pytest.approx(65_000 / 96_000)
    assert plan.budget_estimated is True
    assert plan.budget_source == "previous_request"
    assert plan.pressure_basis == "estimated_input_limit"


def test_timing_changes_do_not_change_retention_and_recent_is_independent():
    current = planning_input()
    earlier = plans(Controls(activation="earlier", recent="standard"), current)[-1]
    later = plans(Controls(activation="later", recent="standard"), current)[-1]
    assert earlier.retention == later.retention
    assert earlier.target_tokens == later.target_tokens == 61_200
    assert earlier.activation_tokens == 50_400 and earlier.consider
    assert later.activation_tokens == 72_000 and not later.consider
    longer = plans(Controls(activation="earlier", recent="long"), current)[-1]
    assert longer.activation_tokens == earlier.activation_tokens
    assert longer.retention.recent_tokens == 15_000
    assert replace(longer.retention, recent_tokens=10_000) == earlier.retention


def test_stages_preserve_more_early_and_keep_fixed_retention_constant():
    low = planning_input(current_tokens=50_000)
    high = planning_input(current_tokens=93_000)
    low_plans, high_plans = plans(Controls(), low), plans(Controls(), high)
    assert [p.stage for p in low_plans] == ["standard"]
    assert [p.stage for p in high_plans] == ["standard", "strong"]
    assert low_plans[0].retention.retained_chars == 4500
    assert high_plans[-1].retention.retained_chars == 3000
    assert plans(Controls(mode="fixed"), low)[0].retention == plans(Controls(mode="fixed"), high)[0].retention


def test_preferences_keep_their_detail_tradeoff_even_under_pressure():
    current = planning_input(current_tokens=93_000)
    save = plans(Controls(preference="save_space"), current)[-1]
    balanced = plans(Controls(), current)[-1]
    preserve = plans(Controls(preference="preserve_detail"), current)[-1]
    assert [p.retention.retained_chars for p in (save, balanced, preserve)] == [4500, 3000, 12_000]
    assert balanced.activation_tokens < save.activation_tokens < preserve.activation_tokens


def test_growth_is_bounded_and_cache_batching_counts_only_the_invalidated_suffix():
    current = planning_input(recent_growth_tokens=1_000_000)
    plan = plans(Controls(), current)[-1]
    assert plan.growth_headroom_tokens == 9600
    assert plan.target_tokens == 51_600
    assert plan.required_reclaim(20_000) == 7000
    assert plan.required_reclaim(1000) == 1000
    urgent = plans(Controls(), replace(current, current_tokens=93_000))[-1]
    assert urgent.required_reclaim(20_000) == 1000


def test_exhausted_or_inconsistent_budgets_never_become_full_window_capacity():
    for budget in (RequestBudget(128_000, 128_000), RequestBudget(128_000, 32_000, 100_000)):
        plan = plans(Controls(), planning_input(budget=budget, full=True))[-1]
        assert not plan.consider
        assert plan.pressure is None and plan.projected_pressure is None
        assert plan.reason in {"no_input_space", "inconsistent_input_limit"}


def test_explicit_proactive_disable_does_not_disable_host_full_compaction():
    current = planning_input(current_tokens=100_000, proactive_trigger_override=0)
    assert not plans(Controls(), current)[-1].consider
    assert plans(Controls(), replace(current, full=True))[-1].consider
    assert plans(Controls(mode="legacy"), current) == ()
