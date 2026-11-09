"""A cost table for tuning work: every scenario x window x arm, cost per answered turn against Hermes as shipped.

Skipped unless ``TAMERU_COST_MATRIX`` names an output file (a JSON path). Run it on its own, with real Hermes:
``TAMERU_COST_MATRIX=out.json python -m pytest tests/eval/test_cost_matrix.py``; ``TAMERU_COST_SEEDS=0,1,2`` repeats every cell per seed. It asserts nothing: it is a measuring tool.
"""
from __future__ import annotations

import json
import os

import pytest

if os.environ.get("HERMES_REPO_ROOT", "").strip():
    import agent.context_compressor  # noqa: F401
else:
    pytest.importorskip("agent.context_compressor")

import engines
import replay
import scenario_registry as registry

pytestmark = [pytest.mark.real_hermes,
              pytest.mark.skipif(not os.environ.get("TAMERU_COST_MATRIX"), reason="set TAMERU_COST_MATRIX=<output.json> to measure")]

ARMS = os.environ.get("TAMERU_COST_ARMS", "stock_defaults,tameru_1_4_product,tameru_legacy").split(",")
SEEDS = [int(s) for s in os.environ.get("TAMERU_COST_SEEDS", "0").split(",")]
SCENARIOS = [s for s in os.environ.get("TAMERU_COST_SCENARIOS", "").split(",") if s]
WINDOWS = [int(w) for w in os.environ.get("TAMERU_COST_WINDOWS", "32000,48000,64000,128000").split(",")]


def test_cost_matrix():
    rows = []
    for scenario in SCENARIOS or registry.scenario_names():
        for window in WINDOWS:
            for arm, seed in ((a, s) for a in ARMS for s in SEEDS):
                m = replay.run_session(lambda arm=arm: engines.make_engine(arm), scenario=scenario, context_length=window, seed=seed)["metrics"]
                answered = max(1, m["turns"] - m["failed_turns"])
                gold = m["gold"]["by_category"]
                rows.append({
                    "scenario": scenario, "window": window, "arm": arm, "seed": seed,
                    "cost": round(m["total_simulated_input_cost"] / answered, 1), "failed": m["failed_turns"],
                    "compactions": m.get("compactions"), "requests": m["requests"],
                    "gold": sum(c["retained"] for c in gold.values()), "errors": m["error_fingerprints"]["retained"],
                })
    with open(os.environ["TAMERU_COST_MATRIX"], "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
