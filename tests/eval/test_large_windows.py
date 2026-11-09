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

#: ``(scenario, window, check)`` -> why the plugin loses it (measured numbers). Empty: it loses nothing here.
#: The identical delta at every window of a session means the session never reached the plugin's trigger: the
#: shipped arm then pays only its declared recovery schemas (tameru_read_original plus the activity surface) with no
#: rewrite to offset them. ``smallrows`` and the held-out recipes are the under-trigger cells; on 1.5M+ sessions the
#: fixed schema cost is ~1%, on the smallest held-out sessions up to ~10% relative but ~15K characters absolute.
KNOWN_LOSSES: dict[tuple[str, int, str], str] = {
    ("default", 256_000, "cost"): "schema-only delta under trigger: 1,977,953 vs 1,958,777 (+0.98%)",
    ("refactor", 256_000, "cost"): "schema-only delta under trigger: 2,576,501 vs 2,551,324 (+0.99%)",
    ("smallrows", 128_000, "cost"): "schema-only delta under trigger: 2,740,386 vs 2,711,557 (+1.06%)",
    ("smallrows", 200_000, "cost"): "schema-only delta under trigger: 2,740,386 vs 2,711,557 (+1.06%)",
    ("smallrows", 256_000, "cost"): "schema-only delta under trigger: 2,740,386 vs 2,711,557 (+1.06%)",
    ("i18n", 256_000, "cost"): "schema-only delta under trigger: 1,545,836 vs 1,527,442 (+1.20%)",
    ("longform", 128_000, "cost"): "schema-only delta under trigger: 268,903 vs 252,597 (+6.5%)",
    ("longform", 200_000, "cost"): "schema-only delta under trigger: 268,903 vs 252,597 (+6.5%)",
    ("longform", 256_000, "cost"): "schema-only delta under trigger: 268,903 vs 252,597 (+6.5%)",
    ("mixedlang", 128_000, "cost"): "schema-only delta under trigger: 209,505 vs 193,198 (+8.4%)",
    ("mixedlang", 200_000, "cost"): "schema-only delta under trigger: 209,505 vs 193,198 (+8.4%)",
    ("mixedlang", 256_000, "cost"): "schema-only delta under trigger: 209,505 vs 193,198 (+8.4%)",
    ("diagnostics", 128_000, "cost"): "schema-only delta under trigger: 183,043 vs 167,780 (+9.1%)",
    ("diagnostics", 200_000, "cost"): "schema-only delta under trigger: 183,043 vs 167,780 (+9.1%)",
    ("diagnostics", 256_000, "cost"): "schema-only delta under trigger: 183,043 vs 167,780 (+9.1%)",
    ("rereads", 128_000, "cost"): "schema-only delta under trigger: 167,417 vs 152,154 (+10.0%)",
    ("rereads", 200_000, "cost"): "schema-only delta under trigger: 167,417 vs 152,154 (+10.0%)",
    ("rereads", 256_000, "cost"): "schema-only delta under trigger: 167,417 vs 152,154 (+10.0%)",
    ("dense", 128_000, "cost"): "schema-only delta under trigger: 165,297 vs 150,035 (+10.2%)",
    ("dense", 200_000, "cost"): "schema-only delta under trigger: 165,297 vs 150,035 (+10.2%)",
    ("dense", 256_000, "cost"): "schema-only delta under trigger: 165,297 vs 150,035 (+10.2%)",
    ("default", 200_000, "gold"): "trajectory divergence, not systematic loss: stock ids {R1,R5,R6} absent",
    ("refactor", 200_000, "gold"): "trajectory divergence, not systematic loss: stock id {R2} absent",
    ("i18n", 200_000, "gold"): "trajectory divergence, not systematic loss: stock ids {R3,R4} absent",
    ("i18n", 200_000, "errors"): "trajectory divergence: 16 error templates vs stock's 17",
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
    assert ours["total_simulated_input_cost"] <= theirs["total_simulated_input_cost"]


@pytest.mark.parametrize(("scenario", "window"), cells("gold"))
def test_the_shipped_plugin_keeps_at_least_as_many_gold_facts(scenario, window):
    ours, theirs = metrics(scenario, window, "tameru_1_4_product"), metrics(scenario, window, "stock_defaults")
    assert ours["gold"]["observed"] and theirs["gold"]["observed"], "Closing probe not observed"
    assert set(theirs["gold"]["retained_ids"]) <= set(ours["gold"]["retained_ids"])
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
    assert ours["byte_stability"]["input_mutations"] == 0


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_a_session_that_never_reaches_the_trigger_is_replayed_exactly_as_stock_replays_it(scenario):
    """On the 256K window every shape but ``oncall`` stays under the plugin's trigger: no prune, no compaction, the
    same message input; the plugin's declared recovery schemas still cost input. ``oncall`` alone reaches the threshold (peak ~211K estimated tokens
    against 192K); there the plugin prunes where stock compacts."""
    ours, theirs = metrics(scenario, 256_000, "tameru_1_4_product"), metrics(scenario, 256_000, "stock_defaults")
    if scenario == "oncall":
        assert ours["prune_commits"] >= 1 and theirs["compactions"] == 1
        return
    if scenario == "bursty":
        # ``bursty``'s 400-line traces reach the tuned activation ahead of the host trigger at 256K.
        assert ours["prune_commits"] >= 1 and theirs["compactions"] == 1
        return
    assert ours["prune_commits"] == ours["compactions"] == theirs["compactions"] == 0
    assert ours["requests"] == theirs["requests"]
    schema_delta = ours["schema_chars_per_request"] - theirs["schema_chars_per_request"]
    assert ours["input_chars_total"] - theirs["input_chars_total"] == schema_delta * ours["requests"]
    expected_schema_cost = schema_delta * (1.25 + 0.1 * (ours["requests"] - 1))
    assert ours["total_simulated_input_cost"] - theirs["total_simulated_input_cost"] == pytest.approx(expected_schema_cost)
    assert ours["gold"]["retained"] == theirs["gold"]["retained"]
    assert ours["gold"]["retained_ids"] == theirs["gold"]["retained_ids"]
