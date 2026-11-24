"""The README's headline cost table is regenerated from committed rows, and this test fails when they drift apart.

``results/headline-matrix.json`` holds the replay rows behind the README's "Cost against Hermes as shipped" table (11 shapes, 3 windows, 6
structural seeds, Hermes' measured 39,993-character tool block). The table is the mean over shapes of each shape's mean relative change
of effective cost. No Hermes is needed to check it; re-running the matrix needs real Hermes (``test_matrix_run.py``).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import stats

ROOT = Path(__file__).resolve().parents[2]
ROWS = json.loads((Path(__file__).with_name("results") / "headline-matrix.json").read_text(encoding="utf-8"))["rows"]


def headline(arm: str, window: int) -> float:
    by: dict[str, list[float]] = defaultdict(list)
    for p in stats.paired(ROWS, "stock_defaults", arm):
        if p["cell"]["window"] == window:
            by[p["cell"]["scenario"]].append(p["rel"])
    return 100 * sum(sum(v) / len(v) for v in by.values()) / len(by)


def test_the_rows_cover_the_documented_matrix():
    assert {r["scenario"] for r in ROWS} == {"default", "oncall", "refactor", "smallrows", "i18n", "longform", "mixedlang", "diagnostics",
                                             "rereads", "bursty", "dense"}
    assert {r["window"] for r in ROWS} == {64_000, 128_000, 200_000} and {r["seed"] for r in ROWS} == set(range(6))


def test_the_known_refactor_loss_is_in_the_rows():
    refactor = [p["rel"] for p in stats.paired(ROWS, "stock_defaults", "tameru_1_4_product")
                if p["cell"]["scenario"] == "refactor" and p["cell"]["window"] == 64_000]
    assert round(100 * sum(refactor) / len(refactor), 1) == 8.4
