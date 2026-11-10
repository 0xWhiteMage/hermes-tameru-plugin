"""A cost table for tuning work: every scenario x window x arm, cost per answered turn against Hermes as shipped.

Skipped unless ``TAMERU_COST_MATRIX`` names an output file (a JSON path). Run it on its own, with real Hermes:
``TAMERU_COST_MATRIX=out.json python -m pytest tests/eval/test_cost_matrix.py``; ``TAMERU_COST_SEEDS=0,1,2`` repeats every cell per seed. It asserts nothing: it is a measuring tool.

Each row also says where the cost came from and what the arm did to the cached prefix, so a difference between two arms
can be explained and not only observed: the main and summarizer shares of the cost, summarizer calls, prune commits,
prefix breaks and the characters they invalidated, the engine's schema size, the size of the Artifact Trail in the
final request, which gold facts survived (by id) and how many needed facts are missing.
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


def trail_chars(final_request) -> int:
    """Characters of Artifact Trail sections in the final request (0 when there is none, or the request never went out)."""
    from hermes_tameru_plugin.ledger import HEADING

    total = 0
    for row in final_request or ():
        content = row.get("content") if isinstance(row, dict) else None
        if not isinstance(content, str) or HEADING not in content:
            continue
        start = content.index(HEADING)
        following = content.find("\n## ", start + len(HEADING))
        total += (len(content) if following < 0 else following) - start
    return total


def matrix_row(scenario: str, window: int, arm: str, seed: int) -> dict:
    """One measured cell: the replay of ``scenario`` at ``window`` for ``arm`` and ``seed``."""
    result = replay.run_session(lambda: engines.make_engine(arm), scenario=scenario, context_length=window, seed=seed,
                                keep_final_request=True)
    m = result["metrics"]
    answered = max(1, m["turns"] - m["failed_turns"])
    return {
        "scenario": scenario, "window": window, "arm": arm, "seed": seed,
        "cost": round(m["total_simulated_input_cost"] / answered, 1), "failed": m["failed_turns"],
        "compactions": m.get("compactions"), "requests": m["requests"],
        "gold": sum(c["retained"] for c in m["gold"]["by_category"].values()),
        "errors": m["error_fingerprints"]["retained"],
        "main_cost": round(m["cost_cache_priced"] / answered, 1),
        "summary_cost": round(m["cost_auxiliary_input"] / answered, 1),
        "summary_calls": m["summary_llm_calls"],
        "prune_commits": m["prune_commits"], "prefix_breaks": m["prefix_break_events"],
        "invalidated_chars": m["prefix_invalidated_chars"],
        "schema_chars": m["schema_chars_per_request"],
        "trail_chars": trail_chars(result.get("final_request")),
        "gold_ids": m["gold"]["retained_ids"] or [],
        "needed_missing": m["refetch"]["missing_needed_facts"],
    }


def test_cost_matrix():
    rows = [matrix_row(scenario, window, arm, seed)
            for scenario in SCENARIOS or registry.scenario_names()
            for window in WINDOWS
            for arm in ARMS
            for seed in SEEDS]
    with open(os.environ["TAMERU_COST_MATRIX"], "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
