"""``matrix``: a matrix run is byte-reproducible, and (opt-in) writes the committed result rows.

Real Hermes only. ``TAMERU_MATRIX_OUT=<file> python -m pytest tests/eval/test_matrix_run.py`` writes a full matrix (arms, scenarios, windows
and seeds from ``TAMERU_MATRIX_ARMS`` / ``_SCENARIOS`` / ``_WINDOWS`` / ``_SEEDS``, defaulting to the development set). Running through pytest keeps
the conftest's isolated HERMES_HOME, so a matrix never reads or writes your real Hermes configuration.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("agent.context_compressor")

import matrix
import stats

pytestmark = pytest.mark.real_hermes
TINY = dict(arms=("stock_defaults", "tameru_1_4_product"), scenarios=("oncall",), windows=(64_000,), seeds=(0, 1))


def test_the_same_matrix_twice_is_byte_identical():
    first = matrix.dump(matrix.run(**TINY))
    assert first == matrix.dump(matrix.run(**TINY))
    assert '"timing"' not in first          # wall-clock seconds would break the identity, so they are never in the committed rows


def test_an_arm_against_itself_in_real_rows_is_exactly_zero():
    rows = matrix.run(**TINY)["rows"]
    result = stats.a_a_difference(rows, "tameru_1_4_product")
    assert result["cells"] == 2 and result["cluster"]["mean"] == 0.0 and not result["excludes_zero"]


def test_other_cache_prices_are_recorded_in_the_header():
    plain = matrix.run(**TINY)
    cheap = matrix.run(**TINY, read=0.05)
    assert plain["meta"]["read"] == 0.10 and cheap["meta"]["read"] == 0.05
    assert cheap["rows"][0]["base"] < plain["rows"][0]["base"]


@pytest.mark.skipif(not os.environ.get("TAMERU_MATRIX_OUT"), reason="set TAMERU_MATRIX_OUT=<file> to write a matrix")
def test_write_the_matrix():
    csv = lambda name, default, cast=str: [cast(p) for p in os.environ.get(name, "").split(",") if p] or list(default)
    result = matrix.run(csv("TAMERU_MATRIX_ARMS", matrix.ARMS), csv("TAMERU_MATRIX_SCENARIOS", matrix.DEVELOPMENT),
                        csv("TAMERU_MATRIX_WINDOWS", matrix.WINDOWS, int), csv("TAMERU_MATRIX_SEEDS", matrix.SEEDS, int))
    Path(os.environ["TAMERU_MATRIX_OUT"]).write_text(matrix.dump(result), encoding="utf-8")
