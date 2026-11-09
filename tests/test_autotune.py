"""The self-tuner: starts at the policy table, moves within bounds, never thrashes, always relaxes."""
from __future__ import annotations

import pytest

from hermes_tameru_plugin import policy
from hermes_tameru_plugin.autotune import SIGNALS, Tuner, Tuning, astuple


def _row(name):
    p = policy._PREFERENCES[name]
    return (p.activation, p.detail, p.recent, p.minimum_reclaim_share, p.regrowth)


def test_the_starting_point_is_the_policy_tables_balanced_row():
    assert astuple(Tuner().tuning()) == _row("balanced")
    assert astuple(Tuning()) == _row("balanced")


def test_the_bias_extremes_are_the_tables_other_rows():
    assert astuple(Tuner(-1).tuning()) == pytest.approx(_row("save_space"))
    assert astuple(Tuner(1).tuning()) == pytest.approx(_row("preserve_detail"))
    middle = astuple(Tuner(0.5).tuning())
    assert all(min(a, b) <= m <= max(a, b) for m, a, b in zip(middle, _row("balanced"), _row("preserve_detail")))


@pytest.mark.parametrize("bad", [float("nan"), "high", None, True])
def test_a_bad_bias_is_refused(bad):
    with pytest.raises(ValueError):
        Tuner().set_bias(bad)


def test_a_bias_outside_the_range_is_clamped():
    assert Tuner(5).bias == 1.0 and Tuner(-5).bias == -1.0


def test_unknown_signals_are_refused():
    with pytest.raises(ValueError):
        Tuner().signal("made_up")


def test_a_host_summary_makes_cleanup_start_earlier():
    tuner = Tuner()
    before = tuner.tuning()
    adjustment = tuner.signal("host_summarized")
    after = tuner.tuning()
    assert after.activation < before.activation and after.minimum_reclaim_share < before.minimum_reclaim_share
    assert adjustment is not None and "activation" in adjustment.changed and "summarize" in adjustment.reason
    assert tuner.posture() in ("cleaning up earlier", "balanced")


def test_rereading_an_original_keeps_more_detail():
    tuner = Tuner()
    before = tuner.tuning()
    tuner.signal("original_reread")
    after = tuner.tuning()
    assert after.detail > before.detail and after.recent > before.recent


def test_fast_regrowth_cuts_deeper():
    tuner = Tuner()
    before = tuner.tuning()
    tuner.signal("fast_regrowth")
    after = tuner.tuning()
    assert after.detail < before.detail and after.regrowth > before.regrowth


def test_the_same_signal_inside_the_cooldown_is_ignored():
    tuner = Tuner()
    assert tuner.signal("host_summarized") is not None
    snapshot = tuner.tuning()
    assert tuner.signal("host_summarized") is None and tuner.tuning() == snapshot
    for _ in range(3):
        tuner.tick()
    assert tuner.signal("host_summarized") is not None


def test_a_flood_of_signals_never_leaves_the_bounds():
    for bias in (-1, 0, 1):
        tuner = Tuner(bias)
        for _ in range(200):
            for name in SIGNALS:
                tuner.signal(name, count=3)
            for _ in range(3):
                tuner.tick()
        values = astuple(tuner.tuning())
        for value, (low, high) in zip(values, ((0.35, 1.0), (0.4, 2.5), (0.6, 2.0), (0.08, 0.6), (0.06, 0.3))):
            assert low - 1e-9 <= value <= high + 1e-9


def test_calm_ticks_return_every_value_to_the_starting_point():
    tuner = Tuner()
    for name in SIGNALS:
        tuner.signal(name)
    moved = astuple(tuner.tuning())
    assert moved != astuple(Tuner().tuning())
    for _ in range(400):
        tuner.tick()
    assert astuple(tuner.tuning()) == pytest.approx(astuple(Tuner().tuning()), abs=1e-3)


def test_opposing_signals_do_not_ratchet_one_way():
    tuner = Tuner()
    start = astuple(tuner.tuning())
    for _ in range(40):
        tuner.signal("host_summarized")
        for _ in range(3):
            tuner.tick()
        tuner.signal("original_reread")
        for _ in range(3):
            tuner.tick()
    end = astuple(tuner.tuning())
    assert all(abs(a - b) < 0.45 * max(abs(a), 1e-9) for a, b in zip(start, end))


def test_a_signal_at_a_bound_reports_nothing():
    tuner = Tuner(1.0)                      # activation already at 1.0 (the upper bound)
    for _ in range(60):
        tuner.signal("original_reread")
        for _ in range(3):
            tuner.tick()
    assert tuner.signal("original_reread") is None or astuple(tuner.tuning())[0] <= 1.0


def test_history_is_bounded_and_holds_only_short_text():
    tuner = Tuner()
    for _ in range(100):
        for name in SIGNALS:
            tuner.signal(name)
        for _ in range(3):
            tuner.tick()
    assert 0 < len(tuner.history) <= 24
    assert all(len(a.reason) <= 120 and a.changed for a in tuner.history)


def test_state_round_trips_and_malformed_state_is_ignored():
    tuner = Tuner(0.4)
    tuner.signal("host_summarized")
    other = Tuner()
    assert other.load(tuner.state()) is True
    assert astuple(other.tuning()) == pytest.approx(astuple(tuner.tuning()))
    clean = astuple(Tuner().tuning())
    for bad in (None, [], {"version": 2}, {"version": 1, "bias": 0, "offsets": [1, 1]},
                {"version": 1, "bias": "x", "offsets": [1] * 5},
                {"version": 1, "bias": 0, "offsets": [1, 1, 1, 1, float("nan")]}):
        fresh = Tuner()
        assert fresh.load(bad) is False and astuple(fresh.tuning()) == clean


def test_loaded_offsets_are_clamped():
    tuner = Tuner()
    assert tuner.load({"version": 1, "bias": 0, "offsets": [100, -100, 100, -100, 100]})
    low, high = 0.55, 1.45
    assert all(low <= o <= high for o in tuner._offsets)
