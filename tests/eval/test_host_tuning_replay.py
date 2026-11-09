"""Host-settings tuning against the replay: moving Hermes' summary threshold and protected tail must not cost facts.

Two arms of the plugin as shipped, identical except ``adaptive_host`` (the replay runs the per-turn step for both):
``tameru_1_9_host_off`` keeps the configured values, ``tameru_1_9_host_on`` lets Tameru nudge them within bounds.
Gate: tuning on keeps at least as many gold facts in every probe category, breaks no tool-call pairing, and does not
raise the cost after cache pricing by more than 3%. Needs real Hermes (``HERMES_REPO_ROOT``).
"""
from __future__ import annotations

import os

import pytest

if os.environ.get("HERMES_REPO_ROOT", "").strip():
    import agent.context_compressor  # noqa: F401  (a broken checkout must fail the run, not skip it)
else:
    pytest.importorskip("agent.context_compressor")

import engines
import replay
from scenario import CATEGORIES

pytestmark = pytest.mark.real_hermes

SCENARIOS = ("oncall", "refactor", "smallrows", "i18n")
_CACHE: dict = {}


def run(scenario: str, arm: str) -> dict:
    key = (scenario, arm)
    if key not in _CACHE:
        _CACHE[key] = replay.run_session(lambda: engines.make_engine(arm), scenario=scenario)["metrics"]
    return _CACHE[key]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_tuning_breaks_no_tool_call_pairing(scenario):
    for arm in ("tameru_1_9_host_off", "tameru_1_9_host_on"):
        assert run(scenario, arm)["pairing"]["violations"] == 0


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_tuning_keeps_at_least_as_many_gold_facts(scenario, category):
    off = run(scenario, "tameru_1_9_host_off")["gold"]["by_category"][category]
    on = run(scenario, "tameru_1_9_host_on")["gold"]["by_category"][category]
    assert on["retained"] >= off["retained"], f"{scenario}/{category}: off kept {off['retained']}, on kept {on['retained']}"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_tuning_does_not_raise_the_cost(scenario):
    off = run(scenario, "tameru_1_9_host_off")["cost_cache_priced"]
    on = run(scenario, "tameru_1_9_host_on")["cost_cache_priced"]
    assert on <= off * 1.03, f"{scenario}: off {off:,.0f} vs on {on:,.0f} char-eq"
