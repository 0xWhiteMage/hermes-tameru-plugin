"""The plugin as shipped against Hermes as shipped on 128K, 200K and 256K windows, over every session shape.

``test_scenario_replay.py`` holds the 64K window (several compactions per session). On a big window a 30-turn
session reaches Hermes' compaction threshold once or never, and the plugin's proactive prune decides the outcome:
what it prunes and when, and whether the next prune follows. A check the plugin loses is a strict ``xfail`` in
``KNOWN_LOSSES`` with the measured numbers (seed 0; seed 1 differs by under 0.2 points); strict means the day it wins
again the entry has to be deleted.

Needs real Hermes (``HERMES_REPO_ROOT``).
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
import scenario_registry as registry

pytestmark = pytest.mark.real_hermes

WINDOWS = (128_000, 200_000, 256_000)
SCENARIOS = tuple(registry.scenario_names())
CELLS = [(s, w) for s in SCENARIOS for w in WINDOWS]

#: ``(scenario, window, check)`` -> why the plugin loses it.
KNOWN_LOSSES: dict[tuple[str, int, str], str] = {
    ("default", 200_000, "gold"): (
        "LOSS 14/17 vs 17/17 gold facts (recall R1 ORD-90417, R5 deploy-9d41, R6 ORD-77031: needles inside 300-400 line "
        "logs), for -9.9% cost. Hermes as shipped never compacts this session (peak 109.7K estimated tokens against a "
        "150K threshold); the plugin's trigger (0.70 x 150K = 105K) is crossed once, at request 53 of 62, and the prune "
        "cuts 13 rows from 412K to 100K chars. Any prune of a session that would not have compacted trades such facts "
        "for cost; a trigger above 0.731 of the threshold (this session's peak) would not prune it at all."
    ),
}

_CACHE: dict[tuple[str, int, str], dict] = {}


def metrics(scenario: str, window: int, name: str) -> dict:
    key = (scenario, window, name)
    if key not in _CACHE:
        _CACHE[key] = replay.run_session(
            lambda: engines.make_engine(name), scenario=scenario, context_length=window,
        )["metrics"]
    return _CACHE[key]


def cells(check: str):
    out = []
    for scenario, window in CELLS:
        reason = KNOWN_LOSSES.get((scenario, window, check))
        marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        out.append(pytest.param(scenario, window, id=f"{scenario}-{window // 1000}K", marks=marks))
    return out


@pytest.mark.parametrize(("scenario", "window"), cells("cost"))
def test_the_shipped_plugin_costs_no_more_than_hermes_as_shipped(scenario, window):
    ours, theirs = metrics(scenario, window, "tameru_1_4_product"), metrics(scenario, window, "stock_defaults")
    assert ours["cost_cache_priced"] <= theirs["cost_cache_priced"]


@pytest.mark.parametrize(("scenario", "window"), cells("gold"))
def test_the_shipped_plugin_keeps_at_least_as_many_gold_facts(scenario, window):
    ours, theirs = metrics(scenario, window, "tameru_1_4_product"), metrics(scenario, window, "stock_defaults")
    assert ours["gold"]["retained"] >= theirs["gold"]["retained"], (ours["gold"], theirs["gold"])


@pytest.mark.parametrize(("scenario", "window"), cells("errors"))
def test_the_shipped_plugin_keeps_at_least_as_many_error_templates(scenario, window):
    ours, theirs = metrics(scenario, window, "tameru_1_4_product"), metrics(scenario, window, "stock_defaults")
    assert ours["error_fingerprints"]["retained"] >= theirs["error_fingerprints"]["retained"]


@pytest.mark.parametrize(("scenario", "window"), cells("compactions"))
def test_the_shipped_plugin_compacts_no_more_often(scenario, window):
    ours, theirs = metrics(scenario, window, "tameru_1_4_product"), metrics(scenario, window, "stock_defaults")
    assert ours["compactions"] <= theirs["compactions"]


@pytest.mark.parametrize(("scenario", "window"), CELLS)
def test_the_shipped_plugin_keeps_pairs_intact_and_rows_stable(scenario, window):
    ours = metrics(scenario, window, "tameru_1_4_product")
    assert ours["pairing"]["violations"] == 0 and ours["byte_stability"]["rewrites_outside_commits"] == 0


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_a_session_that_never_reaches_the_trigger_is_replayed_exactly_as_stock_replays_it(scenario):
    """On the 256K window every shape but ``oncall`` stays under the plugin's trigger: no prune, no compaction, the
    same requests, byte for byte (cost equal). ``oncall`` alone reaches the threshold (peak ~211K estimated tokens
    against 192K); there the plugin prunes where stock compacts."""
    ours, theirs = metrics(scenario, 256_000, "tameru_1_4_product"), metrics(scenario, 256_000, "stock_defaults")
    if scenario == "oncall":
        assert ours["prune_commits"] >= 1 and theirs["compactions"] == 1
        return
    assert ours["prune_commits"] == ours["compactions"] == theirs["compactions"] == 0
    assert ours["cost_cache_priced"] == theirs["cost_cache_priced"]
    assert ours["input_chars_total"] == theirs["input_chars_total"]
    assert ours["gold"]["retained"] == theirs["gold"]["retained"]
