"""``stats``: paired differences, deterministic bootstrap intervals, and the A/A check. No replay, no Hermes."""
from __future__ import annotations

import stats


def _rows(deltas: dict[str, float], *, seeds=(0, 1, 2), windows=(64_000, 128_000), base=100.0):
    """Two arms; ``deltas`` gives each scenario's relative change of arm ``b`` against arm ``a``."""
    rows = []
    for scenario, rel in deltas.items():
        for window in windows:
            for seed in seeds:
                jitter = 1 + 0.01 * seed
                rows.append({"arm": "a", "scenario": scenario, "window": window, "seed": seed, "effective": base * jitter})
                rows.append({"arm": "b", "scenario": scenario, "window": window, "seed": seed, "effective": base * jitter * (1 + rel)})
    return rows


def test_an_arm_against_itself_is_exactly_zero():
    result = stats.a_a_difference(_rows({"s1": 0.1, "s2": -0.2}), "a")
    assert result["cluster"]["mean"] == 0.0 and result["cluster"]["lo"] == 0.0 and result["cluster"]["hi"] == 0.0
    assert result["naive"]["mean"] == 0.0 and result["excludes_zero"] is False


def test_paired_differences_are_relative_and_matched_by_cell():
    pairs = stats.paired(_rows({"s1": 0.10}), "a", "b")
    assert len(pairs) == 6 and all(abs(p["rel"] - 0.10) < 1e-12 for p in pairs)
    rows = [r for r in _rows({"s1": 0.10}) if not (r["arm"] == "b" and r["seed"] == 2)]     # b is missing seed 2
    assert len(stats.paired(rows, "a", "b")) == 4


def test_the_bootstrap_is_deterministic_and_brackets_the_mean():
    values = [0.02, -0.01, 0.05, 0.03, 0.00, 0.04, -0.02, 0.01]
    one = stats.bootstrap_ci(values, resamples=2_000, seed=7)
    two = stats.bootstrap_ci(values, resamples=2_000, seed=7)
    assert one == two and one["lo"] <= one["mean"] <= one["hi"]
    assert stats.bootstrap_ci(values, resamples=2_000, seed=8) != one                       # the seed is the only randomness


def test_the_cluster_interval_is_wider_than_the_naive_one_when_shapes_differ():
    rows = _rows({"s1": 0.30, "s2": 0.30, "s3": -0.30, "s4": -0.30}, seeds=(0, 1, 2, 3, 4))
    result = stats.compare(rows, "a", "b", resamples=3_000)
    naive_width = result["naive"]["hi"] - result["naive"]["lo"]
    cluster_width = result["cluster"]["hi"] - result["cluster"]["lo"]
    assert cluster_width > naive_width
    assert result["cluster"]["clusters"] == 4 and abs(result["cluster"]["mean"]) < 1e-9


def test_a_consistent_win_excludes_zero_and_a_split_does_not():
    win = stats.compare(_rows({f"s{i}": -0.05 - 0.005 * i for i in range(6)}), "a", "b", resamples=3_000)
    assert win["excludes_zero"] and win["cluster"]["hi"] < 0
    split = stats.compare(_rows({"s1": 0.2, "s2": -0.2, "s3": 0.1, "s4": -0.1}), "a", "b", resamples=3_000)
    assert not split["excludes_zero"]


def test_per_cell_reports_one_row_per_scenario_and_window():
    cells = stats.per_cell(_rows({"s1": 0.1, "s2": -0.1}), "a", "b", resamples=500)
    assert [(c["scenario"], c["window"]) for c in cells] == [("s1", 64_000), ("s1", 128_000), ("s2", 64_000), ("s2", 128_000)]
    assert all(c["n"] == 3 for c in cells)


def test_empty_input_is_handled():
    assert stats.bootstrap_ci([])["n"] == 0
    assert stats.cluster_bootstrap_ci({})["clusters"] == 0
    assert stats.compare([], "a", "b")["cells"] == 0
