"""The ``directives`` scenario: standing rules, rejected theories and superseded settings must survive compaction.

Adaptive and legacy must keep every probe fact that stock Hermes keeps, per probe type (id prefix I, R, S).
The scenario is registered for the test only: the saved baselines cover the registered scenarios.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
if not os.environ.get("HERMES_REPO_ROOT"):
    pytest.skip("needs real Hermes (set HERMES_REPO_ROOT)", allow_module_level=True)

import engines  # noqa: E402
import replay  # noqa: E402
import scenario_registry as registry  # noqa: E402
from scenarios_probes import DIRECTIVES_SPEC  # noqa: E402

pytestmark = pytest.mark.real_hermes
ARMS = ("stock_defaults", "tameru_1_4_product", "tameru_legacy")
WINDOWS = (32_000, 48_000)
SEEDS = (0, 1)


@pytest.fixture(scope="module")
def results():
    registry.register_scenario(DIRECTIVES_SPEC)
    try:
        out = {}
        for window in WINDOWS:
            for seed in SEEDS:
                for arm in ARMS:
                    m = replay.run_session(lambda a=arm: engines.make_engine(a), scenario="directives", context_length=window, seed=seed)["metrics"]
                    out[(window, seed, arm)] = m
        return out
    finally:
        registry.SCENARIOS.pop("directives", None)


def _kept(m, prefix):
    ids = m["gold"]["retained_ids"] or []
    return {i for i in ids if i.startswith(prefix)}


def test_scenario_compacts_under_pressure(results):
    assert any(m["compactions"] for k, m in results.items() if k[2] == "stock_defaults"), "scenario never fills a 32K/48K window"


@pytest.mark.parametrize("arm", ["tameru_1_4_product", "tameru_legacy"])
@pytest.mark.parametrize("prefix", ["I", "R", "S"])
def test_probe_type_kept_at_least_as_well_as_stock(results, arm, prefix):
    for window in WINDOWS:
        for seed in SEEDS:
            assert _kept(results[(window, seed, arm)], prefix) >= _kept(results[(window, seed, "stock_defaults")], prefix), (arm, prefix, window, seed)


@pytest.mark.parametrize("arm", ["tameru_1_4_product", "tameru_legacy"])
def test_every_probe_fact_survives(results, arm):
    for window in WINDOWS:
        for seed in SEEDS:
            assert _kept(results[(window, seed, arm)], "") == {"I1", "I2", "R1", "R2", "S1", "S2"}, (arm, window, seed)


def test_stock_loses_some_so_the_probe_discriminates(results):
    assert any(len(_kept(m, "")) < 6 for k, m in results.items() if k[2] == "stock_defaults")


def test_every_ref_in_the_final_request_resolves():
    """The fourth probe type: after several compactions, each ``ref=`` Tameru left in the request can be read back."""
    import json
    import re

    engines_made = []

    def factory():
        engine = engines.make_engine("tameru_1_4_product")
        engines_made.append(engine)
        return engine

    registry.register_scenario(DIRECTIVES_SPEC)
    try:
        out = replay.run_session(factory, scenario="directives", context_length=32_000, seed=0, keep_final_request=True)
    finally:
        registry.SCENARIOS.pop("directives", None)
    text = json.dumps(out["final_request"], ensure_ascii=False)
    refs = sorted(set(re.findall(r"ref=([0-9a-f]{8,16})", text)))
    assert refs, "the final request carries no ref: the probe would be vacuous"
    engine = engines_made[-1]
    for ref in refs:
        result = json.loads(engine.handle_tool_call("tameru_read_original", {"ref": ref}))
        assert "error" not in result and result.get("content"), (ref, str(result)[:200])
