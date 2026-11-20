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
                        csv("TAMERU_MATRIX_WINDOWS", matrix.WINDOWS, int), csv("TAMERU_MATRIX_SEEDS", matrix.SEEDS, int),
                        price_log=os.environ.get("TAMERU_MATRIX_PRICE_LOG") == "1")
    Path(os.environ["TAMERU_MATRIX_OUT"]).write_text(matrix.dump(result), encoding="utf-8")


def test_the_pricing_ledger_changes_nothing_else_and_reproduces_the_original_price():
    import repricing

    plain = matrix.run(**TINY)
    priced = matrix.run(**TINY, price_log=True)
    assert matrix.dump(plain) == matrix.dump(matrix.run(**TINY))      # a default run is byte-identical with or without the option
    assert priced["meta"].pop("price_log") is True and priced["meta"] == plain["meta"]
    for row, other in zip(plain["rows"], priced["rows"]):
        ledger = other.pop("pricing")
        assert other == row
        again = repricing.original_fields(dict(row, pricing=ledger))
        assert again == {k: row[k] for k in again}, (row["arm"], row["seed"])


def test_post_hoc_ttl_expiry_equals_the_replay_run_with_that_ttl():
    import engines
    import replay
    import repricing

    import protocol3

    idle = protocol3.IDLE_PROFILES["mixed"]          # the same think time in both runs (the clock can move Hermes' backoffs)
    for ttl in (15.0, 300.0):
        plain = replay.run_session(lambda: engines.make_engine("tameru_1_4_product"), scenario="oncall", context_length=64_000,
                                   idle_seconds=idle, price_log=True)
        timed = replay.run_session(lambda: engines.make_engine("tameru_1_4_product"), scenario="oncall", context_length=64_000,
                                   idle_seconds=idle, cache_ttl_s=ttl)
        assert timed["metrics"]["cache_expiries"] > 0
        row = {"pricing": {"answered": 1, "requests": plain["price_log"]["requests"], "summaries": plain["price_log"]["summaries"],
                           "readback_terms": {"effective": [], "effective_real": [], "effective_finder": []}}}
        priced = repricing.price_row(row, repricing.Pricing("t", r=0.10, w=1.25, ttl_s=ttl))
        assert priced["expiries"] == timed["metrics"]["cache_expiries"]
        assert priced["base"] == timed["metrics"]["total_simulated_input_cost"]
