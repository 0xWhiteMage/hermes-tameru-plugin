"""Effective cost (``protocol3.measure``): unit checks on small cells, and an optional table over the whole matrix.

The unit tests pin the accounting, not the product: stock Hermes has no read-back so nothing it lost is recoverable;
the plugin's recoverable facts really are returned by the tool; the effective cost is never below the base cost.
``TAMERU_P3_MATRIX=out.json`` (with ``TAMERU_P3_ARMS``, ``_SCENARIOS``, ``_WINDOWS``, ``_SEEDS``) writes the table.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
if os.environ.get("HERMES_REPO_ROOT", "").strip():
    import agent.context_compressor  # noqa: F401
else:
    pytest.importorskip("agent.context_compressor")

import protocol3
import scenario_registry as registry

pytestmark = pytest.mark.real_hermes
CELL = ("oncall", 32_000)


def test_stock_hermes_has_no_read_back_so_its_losses_are_priced_as_reruns():
    m = protocol3.measure("stock_defaults", *CELL)
    assert m["recoverable"] == 0 and m["lost"] >= 1
    assert m["effective"] > m["base"]
    assert m["kept"] + m["recoverable"] + m["lost"] == m["needed"]


def test_the_plugins_recoverable_facts_are_really_returned_by_the_tool():
    m = protocol3.measure("tameru_1_4_product", *CELL)
    assert m["kept"] + m["recoverable"] + m["lost"] == m["needed"]
    assert m["effective"] >= m["base"]
    assert set(m["kept_ids"]).isdisjoint(m["recoverable_ids"]) and set(m["recoverable_ids"]).isdisjoint(m["lost_ids"])


def test_a_disabled_plugin_prices_exactly_like_stock():
    from hermes_tameru_plugin.config import TameruSettings
    from hermes_tameru_plugin.engine import TameruContextEngine
    import engines

    engines.register("tameru_disabled_p3", lambda: TameruContextEngine(settings=TameruSettings(enabled=False), quiet_mode=True))
    try:
        off, stock = protocol3.measure("tameru_disabled_p3", *CELL), protocol3.measure("stock_defaults", *CELL)
    finally:
        engines.ENGINES.pop("tameru_disabled_p3", None)
    for key in ("base", "effective", "kept", "recoverable", "lost"):
        assert off[key] == stock[key], key


def _idle(minutes: float):
    return lambda turn: minutes * 60.0


def test_without_the_new_options_the_replay_is_exactly_what_it_was():
    import engines
    import replay
    plain = replay.run_session(lambda: engines.make_engine("tameru_1_4_product"), scenario="oncall", context_length=64_000)
    explicit = replay.run_session(lambda: engines.make_engine("tameru_1_4_product"), scenario="oncall", context_length=64_000,
                                  cache_ttl_s=None, idle_seconds=None)
    assert replay.deterministic_part(plain) == replay.deterministic_part(explicit)
    assert "cache_expiries" not in plain["metrics"]


def test_a_cache_lifetime_with_no_idle_gaps_changes_nothing_because_requests_follow_each_other_closely():
    import engines
    import replay
    base = replay.run_session(lambda: engines.make_engine("stock_defaults"), scenario="oncall", context_length=64_000)
    with_ttl = replay.run_session(lambda: engines.make_engine("stock_defaults"), scenario="oncall", context_length=64_000, cache_ttl_s=300)
    assert with_ttl["metrics"]["cache_expiries"] == 0
    assert with_ttl["metrics"]["total_simulated_input_cost"] == base["metrics"]["total_simulated_input_cost"]


def test_idle_gaps_longer_than_the_lifetime_expire_the_cache_and_cost_more():
    import engines
    import replay
    run = lambda **kw: replay.run_session(lambda: engines.make_engine("stock_defaults"), scenario="oncall", context_length=64_000, **kw)["metrics"]
    base = run()
    short = run(cache_ttl_s=300, idle_seconds=_idle(2))        # two-minute pauses: the cache outlives them
    long = run(cache_ttl_s=300, idle_seconds=_idle(10))        # ten-minute pauses: it does not
    assert short["cache_expiries"] == 0 and short["total_simulated_input_cost"] == base["total_simulated_input_cost"]
    assert long["cache_expiries"] > 0 and long["total_simulated_input_cost"] > base["total_simulated_input_cost"]
    assert long["prefix_break_events"] == base["prefix_break_events"]     # an expiry is the provider's doing, not a rewrite by the engine


@pytest.mark.skipif(not os.environ.get("TAMERU_P3_MATRIX"), reason="set TAMERU_P3_MATRIX=<output.json> for the table")
def test_effective_cost_matrix():
    arms = os.environ.get("TAMERU_P3_ARMS", "stock_defaults,tameru_1_4_product,tameru_legacy").split(",")
    scenarios = [s for s in os.environ.get("TAMERU_P3_SCENARIOS", "").split(",") if s] or list(registry.scenario_names())
    windows = [int(w) for w in os.environ.get("TAMERU_P3_WINDOWS", "32000,48000,64000,128000,200000").split(",")]
    seeds = [int(s) for s in os.environ.get("TAMERU_P3_SEEDS", "0").split(",")]
    rows = [protocol3.measure(arm, scenario, window, seed)
            for scenario in scenarios for window in windows for arm in arms for seed in seeds]
    Path(os.environ["TAMERU_P3_MATRIX"]).write_text(json.dumps(rows), encoding="utf-8")
