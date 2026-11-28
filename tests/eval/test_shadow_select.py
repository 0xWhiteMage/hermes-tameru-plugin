"""Unit tests for conservative shadow posture selection (no real Hermes needed)."""
from __future__ import annotations

import pytest

import shadow_select


def row(arm="adaptive_balanced", scenario="s", window=64_000, seed=0, cost=100.0, **overrides):
    base = {"arm": arm, "scenario": scenario, "window": window, "seed": seed, "effective_real": cost,
            "kept": 4, "recoverable": 1, "lost": 0, "lost_unrecoverable": 0, "failed_turns": 0,
            "errors_kept": 2, "prefix_breaks": 1, "rewritten_chars": 100, "requests": 10}
    base.update(overrides)
    return base


def test_a_safe_materially_cheaper_candidate_wins():
    rows = [row(), row("adaptive_save", cost=80.0)]
    cell = shadow_select.select(rows)["cells"][0]
    assert cell["recommended_arm"] == "adaptive_save" and cell["action"] == "candidate_wins"
    assert cell["apply_automatically"] is False and cell["improvement"] == 0.2


def test_a_fact_losing_candidate_is_rejected_even_when_cheaper():
    rows = [row(), row("adaptive_save", cost=50.0, kept=2, lost=2)]
    cell = shadow_select.select(rows)["cells"][0]
    assert cell["recommended_arm"] == "adaptive_balanced" and cell["action"] == "hold_baseline"
    assert cell["candidates"]["adaptive_save"]["safe"] is False


def test_marginal_cost_wins_do_not_recommend_churn():
    rows = [row(), row("adaptive_save", cost=99.5)]
    cell = shadow_select.select(rows)["cells"][0]
    assert cell["recommended_arm"] == "adaptive_balanced" and cell["improvement"] == 0.0


def test_unpaired_and_missing_candidate_rows_do_not_crash_selection():
    rows = [row(seed=0), row(seed=1), row("adaptive_save", seed=0, cost=80.0)]
    report = shadow_select.select(rows)
    assert report["cells"][0]["candidates"]["fixed_detail"]["rejected"] == ["missing rows"]
    assert report["cells"][0]["recommended_arm"] in shadow_select.CANDIDATES


def test_report_output_is_byte_stable():
    rows = [row(), row("adaptive_detail", cost=90.0)]
    first = shadow_select.dump_report(shadow_select.select(rows))
    assert first == shadow_select.dump_report(shadow_select.select(rows))
    assert '"production_mutation": false' in first


def test_show_names_the_recommendation_without_claiming_live_adaptation():
    rows = [row(), row("fixed_save", cost=70.0)]
    report = shadow_select.select(rows)
    text = shadow_select.show(report)
    assert "fixed_save" in text and "production is unchanged" in text



def test_shadow_candidate_engines_construct_in_the_eval_registry():
    import engines

    for arm in shadow_select.CANDIDATES:
        assert engines.make_engine(arm) is not None


@pytest.mark.real_hermes
def test_a_shadow_posture_completes_one_real_end_to_end_replay():
    pytest.importorskip("agent.context_compressor")
    import protocol3

    row = protocol3.measure("adaptive_save", "oncall", 64_000, 0, structural=True)
    assert row["requests"] > 0 and row["failed_turns"] == 0
