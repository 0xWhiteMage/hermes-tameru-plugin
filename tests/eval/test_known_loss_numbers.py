"""The numbers behind every expected failure, kept as data and checked against today's replay.

The ``KNOWN_LOSSES`` tables in ``test_scenario_replay.py``, ``test_small_window_replay.py`` and ``test_large_windows.py``
say *why* the plugin loses a check; a strict ``xfail`` only notices the day a loss turns into a win. It does not notice a
loss that grew, shrank or changed shape, and numbers written into prose went stale that way. So the measured pair for
every entry (the plugin's value and the reference's, seed 0) lives in ``known_loss_numbers.json``, and this test fails
when a replay no longer gives those numbers or when the tables and the file list different entries.

``TAMERU_WRITE_BASELINES=1`` rewrites the file; read the diff before committing it. Needs real Hermes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

if os.environ.get("HERMES_REPO_ROOT", "").strip():
    import agent.context_compressor  # noqa: F401
else:
    pytest.importorskip("agent.context_compressor")

import engines
import replay
import test_large_windows
import test_scenario_replay
import test_small_window_replay

pytestmark = pytest.mark.real_hermes
PATH = Path(__file__).with_name("known_loss_numbers.json")
WRITE = os.environ.get("TAMERU_WRITE_BASELINES", "").strip() == "1"
REFERENCE = {"tameru_1_4": "stock", "tameru_1_4_product": "stock_defaults"}   # equal gates; Hermes as shipped
COST_TOLERANCE = 0.0005     # relative; the replay is deterministic, this only absorbs rounding in the file


def entries() -> list[tuple[str, str, int, str, str]]:
    """Every expected failure as ``(table, scenario, window, check, engine)``."""
    out = [("scenario_64k", scn, replay.CONTEXT_LENGTH, check, engine)
           for scn, check, engine in test_scenario_replay.KNOWN_LOSSES]
    out += [("small_32k", scn, 32_000, check, engine) for scn, check, engine in test_small_window_replay.KNOWN_LOSSES]
    out += [("small_48k", scn, 48_000, check, "tameru_1_4_product") for scn, check in test_small_window_replay.KNOWN_LOSSES_48K]
    out += [("large", scn, window, check, "tameru_1_4_product") for scn, window, check in test_large_windows.KNOWN_LOSSES]
    return sorted(out)


def key(entry: tuple) -> str:
    table, scenario, window, check, engine = entry
    return f"{table}/{scenario}/{window}/{check}/{engine}"


_RUNS: dict[tuple[str, int, str], dict] = {}


def _metrics(scenario: str, window: int, engine: str) -> dict:
    cell = (scenario, window, engine)
    if cell not in _RUNS:
        _RUNS[cell] = replay.run_session(lambda: engines.make_engine(engine), scenario=scenario, context_length=window)["metrics"]
    return _RUNS[cell]


def value(m: dict, check: str):
    """The number the check compares: cost per answered turn, a count, or the list of gold facts kept."""
    if check == "cost":
        return round(m["total_simulated_input_cost"] / max(1, m["turns"] - m["failed_turns"]), 1)
    if check == "compactions":
        return m["compactions"]
    if check == "errors":
        return m["error_fingerprints"]["retained"]
    if check == "gold":
        return sorted(m["gold"]["retained_ids"] or [])
    if check.startswith("gold:"):
        return m["gold"]["by_category"][check.split(":", 1)[1]]["retained"]
    raise AssertionError(f"no number is defined for the check {check!r}; add it to value()")


def measured(entry: tuple) -> dict:
    _table, scenario, window, check, engine = entry
    return {"plugin": value(_metrics(scenario, window, engine), check),
            "reference": value(_metrics(scenario, window, REFERENCE[engine]), check)}


def test_the_file_lists_exactly_the_expected_failures():
    if WRITE:
        PATH.write_text(json.dumps({key(e): measured(e) for e in entries()}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    saved = json.loads(PATH.read_text(encoding="utf-8"))
    assert sorted(saved) == sorted(key(e) for e in entries()), "KNOWN_LOSSES and known_loss_numbers.json list different entries"


@pytest.mark.parametrize("entry", entries(), ids=key)
def test_an_expected_failure_still_has_its_recorded_numbers(entry):
    saved = json.loads(PATH.read_text(encoding="utf-8"))[key(entry)]
    now = measured(entry)
    for side in ("plugin", "reference"):
        if isinstance(now[side], float):
            assert now[side] == pytest.approx(saved[side], rel=COST_TOLERANCE), (side, saved, now)
        else:
            assert now[side] == saved[side], (side, saved, now)
