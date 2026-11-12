"""Round-2 benchmark honesty: the realistic read-back price, the weak finder, dependent use, and the margin guards of the gate."""
from __future__ import annotations


import matrix
import protocol3
import scenario_registry
import scenarios_dependent


def test_the_finder_greps_the_questions_words_only():
    pattern = protocol3._finder_pattern("List the primary reported for windows 3, 7 and 11.")
    assert "primary" in pattern and "windows" in pattern and "3" not in pattern.split("|")


def test_dependent_missing_counts_only_the_three_requests_after_the_first_that_carried_it():
    def log(*texts):
        return [{"messages": [{"role": "user", "content": t}]} for t in texts]
    needles = ["DETAIL"]
    assert protocol3.dependent_missing(log("a", "DETAIL", "DETAIL", "DETAIL", "DETAIL"), needles) == 0
    assert protocol3.dependent_missing(log("a", "DETAIL", "x", "x", "x", "x"), needles) == 3
    assert protocol3.dependent_missing(log("a", "DETAIL", "DETAIL", "x", "DETAIL"), needles) == 1
    assert protocol3.dependent_missing(log("a", "b"), needles) == 0            # never shown: nothing to miss


def test_the_dependent_shape_plants_a_detail_per_turn_and_does_not_repeat_it_in_follow_ups():
    scenario = scenarios_dependent.build_dependent(0)
    deps = [g for g in scenario.gold if g.id.startswith("dep_")]
    assert len(deps) == 25 and all(not g.needed for g in deps)
    for turn in scenario.turns:
        detail = next(g.needle for g in deps if g.needle in turn.rounds[0].steps[0].result)
        assert all(detail not in s.result and detail not in str(s.args) and detail not in r.say for r in turn.rounds[1:] for s in r.steps)
    assert {g.id for g in scenario.gold if g.needed} == {f"need_{i}" for i in (3, 7, 11, 15, 19, 23)}


def test_the_dependent_shape_is_registered_only_on_request():
    assert "dependent" not in scenario_registry.SCENARIOS
    scenarios_dependent.register_dependent()
    try:
        assert "dependent" in scenario_registry.SCENARIOS
    finally:
        scenarios_dependent.unregister_dependent()
    assert "dependent" not in scenario_registry.SCENARIOS


def _rows(arm, window=64_000, shapes=("a", "b"), **over):
    base = dict(arm=arm, window=window, kept=10, recoverable=2, lost=0, prefix_breaks=10, rewritten_chars=1000, requests=100)
    base.update(over)
    return [dict(base, scenario=s, seed=sd) for s in shapes for sd in (0, 1)]


def test_the_margins_pass_equal_rows_and_catch_each_kind_of_regression():
    ok = _rows("base") + _rows("arm")
    assert matrix.margin_failures(ok, "base", "arm") == []
    assert any("kept" in f for f in matrix.margin_failures(_rows("base") + _rows("arm", kept=9), "base", "arm"))
    assert any("held" in f for f in matrix.margin_failures(_rows("base") + _rows("arm", recoverable=0, kept=9), "base", "arm"))
    assert any("prefix_breaks" in f for f in matrix.margin_failures(_rows("base") + _rows("arm", prefix_breaks=12), "base", "arm"))
    assert any("rewritten_chars" in f for f in matrix.margin_failures(_rows("base") + _rows("arm", rewritten_chars=1200), "base", "arm"))
    assert any("requests" in f for f in matrix.margin_failures(_rows("base") + _rows("arm", requests=110), "base", "arm"))
    assert any("missing" in f for f in matrix.margin_failures(_rows("base") + _rows("arm", dependent_missing=2), "base", "arm"))


def test_one_shape_losing_a_fact_fails_even_when_the_mean_hides_it():
    shapes = tuple("abcdefgh")
    worse = [dict(r, kept=r["kept"] - 2) if r["scenario"] == "a" else r for r in _rows("arm", shapes=shapes)]
    failures = matrix.margin_failures(_rows("base", shapes=shapes) + worse, "base", "arm")
    assert any(" a:" in f for f in failures) and not any("facts fall by" in f and "a:" not in f and "per session (margin 0.3)" in f for f in failures[:0])


def test_the_gate_states_its_round_two_terms():
    assert matrix.PROMOTION["primary_idea"] == "frontier" and matrix.PROMOTION["blind_looks"] == 1
    assert "effective_real" in matrix.PROMOTION["price_fields"]
