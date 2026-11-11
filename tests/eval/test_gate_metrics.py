"""The metrics the gate stores in its report: a reduced replay matrix with intervals, its scorecard, and the instruction probe.

Real Hermes only, and only when ``TAMERU_GATE_METRICS_OUT=<file>`` is set (``gate.py`` sets it); ``gate.py`` then copies the file into the
gate report, so a report holds numbers as well as pass marks. Everything here is deterministic apart from nothing: no timing is stored.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("agent.context_compressor")

import engines
import matrix
import replay
import scenario_registry as registry
import stats
from scenarios_probes import DIRECTIVES_SPEC

pytestmark = pytest.mark.real_hermes
ARMS = ("stock_defaults", "tameru_1_4_product")
SCENARIOS = ("default", "oncall", "refactor")
WINDOWS = (64_000, 128_000)
SEEDS = (0, 1)
PROBE_ARMS = ("stock_defaults", "tameru_1_4_product", "tameru_legacy")
PROBE_WINDOWS = (32_000, 48_000)


def probe_summary() -> list[dict]:
    """The ``directives`` probe: which standing rules (I), rejected theories (R) and superseded settings (S) survive, per arm."""
    registry.register_scenario(DIRECTIVES_SPEC)
    try:
        out = []
        for window in PROBE_WINDOWS:
            for arm in PROBE_ARMS:
                kept = {"I": 0, "R": 0, "S": 0}
                total = {"I": 0, "R": 0, "S": 0}
                for seed in SEEDS:
                    metrics = replay.run_session(lambda a=arm: engines.make_engine(a), scenario="directives", context_length=window, seed=seed)["metrics"]
                    retained = set(metrics["gold"]["retained_ids"] or [])
                    spec = registry.get_spec("directives")
                    for fact in spec.build(seed, spec.turns).gold:
                        kind = fact.id[0]
                        if kind in total:
                            total[kind] += 1
                            kept[kind] += fact.id in retained
                out.append({"arm": arm, "window": window, "seeds": list(SEEDS), "kept": kept, "total": total,
                            "compactions": metrics["compactions"]})
        return out
    finally:
        registry.SCENARIOS.pop("directives", None)


def gate_metrics() -> dict:
    result = matrix.run(ARMS, SCENARIOS, WINDOWS, SEEDS)
    comparisons = [stats.compare(result["rows"], "stock_defaults", "tameru_1_4_product", window=w, resamples=4_000) for w in WINDOWS]
    return {
        "matrix": {"meta": result["meta"], "rows_sha256": hashlib.sha256(matrix.dump(result).encode()).hexdigest(),
                   "comparisons": [{k: c[k] for k in ("window", "cells", "cluster", "naive", "excludes_zero")} for c in comparisons],
                   "scorecard": matrix.scorecard(result["rows"])},
        "probes": {"directives": probe_summary()},
    }


@pytest.mark.skipif(not os.environ.get("TAMERU_GATE_METRICS_OUT"), reason="gate.py sets TAMERU_GATE_METRICS_OUT")
def test_write_gate_metrics():
    Path(os.environ["TAMERU_GATE_METRICS_OUT"]).write_text(json.dumps(gate_metrics(), indent=1, sort_keys=True) + "\n", encoding="utf-8")


def test_the_gate_metrics_are_deterministic_and_complete():
    first = gate_metrics()
    assert first == gate_metrics()
    assert len(first["matrix"]["comparisons"]) == len(WINDOWS) and first["matrix"]["scorecard"]
    assert {p["arm"] for p in first["probes"]["directives"]} == set(PROBE_ARMS)
