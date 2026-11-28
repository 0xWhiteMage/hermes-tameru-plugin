"""Shadow-select Tameru policy postures from paired end-to-end replay rows.

Candidates never touch a live transcript: each arm is replayed as a separate branch of the same
scripted session, and this module only reports which bounded posture would have won after safety
checks. The shipped policy is ``adaptive_balanced``; a candidate is recommended only when it is at
least 1% cheaper on ``effective_real`` without losing facts, error evidence, or completion reliability.

    python tests/eval/shadow_select.py run --rows shadow-rows.json --report shadow-report.json
    python tests/eval/shadow_select.py select tests/eval/results/dev-matrix.json --out shadow-report.json
    python tests/eval/shadow_select.py show shadow-report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

BASELINE = "adaptive_balanced"
CANDIDATES = ("adaptive_balanced", "adaptive_save", "adaptive_detail", "fixed_balanced", "fixed_save", "fixed_detail")
COST_FIELD = "effective_real"
MIN_IMPROVEMENT = 0.01
FACTS_MEAN_MARGIN = 0.3
FACTS_SHAPE_MARGIN = 1.0
RISE_MARGIN = {"prefix_breaks": 0.10, "rewritten_chars": 0.10, "requests": 0.05}


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["rows"] if isinstance(data, dict) else data


def dump_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=1, sort_keys=True) + "\n"


def _mean(rows: list[dict[str, Any]], field: str) -> float:
    return sum(float(row.get(field, 0.0)) for row in rows) / max(1, len(rows))


def _held(row: dict[str, Any]) -> float:
    return float(row.get("kept", 0) + row.get("recoverable", 0))


def _paired_safety(base_rows: list[dict[str, Any]], arm_rows: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Conservative cell gates, paired by seed. No statistics: a shadow recommendation must not regress evidence."""
    base_by_seed = {row["seed"]: row for row in base_rows}
    reasons: list[str] = []
    for row in arm_rows:
        base = base_by_seed.get(row["seed"])
        if base is None:
            reasons.append("unpaired seed")
            continue
        if _held(base) - _held(row) > FACTS_SHAPE_MARGIN:
            reasons.append(f"seed {row['seed']} loses more than {FACTS_SHAPE_MARGIN:g} held fact(s)")
        for field in ("lost_unrecoverable", "failed_turns", "lost"):
            if float(row.get(field, 0)) > float(base.get(field, 0)):
                reasons.append(f"seed {row['seed']} raises {field}")
        if float(row.get("errors_kept", 0)) < float(base.get("errors_kept", 0)):
            reasons.append(f"seed {row['seed']} keeps fewer error exemplars")
    if _mean(base_rows, "kept") + _mean(base_rows, "recoverable") - _mean(arm_rows, "kept") - _mean(arm_rows, "recoverable") > FACTS_MEAN_MARGIN:
        reasons.append(f"mean held facts fall by more than {FACTS_MEAN_MARGIN:g}")
    if sum(float(r.get("failed_turns", 0)) for r in arm_rows) > sum(float(r.get("failed_turns", 0)) for r in base_rows):
        reasons.append("failed turns increase")
    for field, margin in RISE_MARGIN.items():
        base = _mean(base_rows, field)
        arm = _mean(arm_rows, field)
        if base > 0 and arm / base - 1.0 > margin:
            reasons.append(f"mean {field} rises by more than {margin:.0%}")
    return not reasons, sorted(set(reasons))


def select(rows: list[dict[str, Any]], *, baseline: str = BASELINE, candidates: tuple[str, ...] = CANDIDATES,
           cost_field: str = COST_FIELD, min_improvement: float = MIN_IMPROVEMENT) -> dict[str, Any]:
    """Select the cheapest safe posture independently for every scenario/window cell."""
    cells: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("arm") in candidates and cost_field in row:
            cells.setdefault((row["scenario"], int(row["window"])), []).append(row)
    out = []
    for (scenario, window), group in sorted(cells.items()):
        base_rows = [row for row in group if row["arm"] == baseline]
        if not base_rows:
            out.append({"scenario": scenario, "window": window, "action": "missing_baseline", "recommended_arm": baseline})
            continue
        base_cost = _mean(base_rows, cost_field)
        details = {}
        winner = baseline
        winner_cost = base_cost
        for arm in candidates:
            arm_rows = [row for row in group if row["arm"] == arm]
            if not arm_rows:
                details[arm] = {"runs": 0, "safe": False, "rejected": ["missing rows"]}
                continue
            safe, rejected = _paired_safety(base_rows, arm_rows)
            cost = _mean(arm_rows, cost_field)
            improvement = 1.0 - cost / base_cost if base_cost else 0.0
            details[arm] = {"runs": len(arm_rows), "cost": round(cost, 3), "improvement": round(improvement, 6),
                            "safe": safe, "rejected": rejected}
            if safe and cost < winner_cost * (1.0 - min_improvement):
                winner, winner_cost = arm, cost
        improved = winner != baseline
        out.append({
            "scenario": scenario, "window": window, "runs": len(base_rows),
            "baseline_cost": round(base_cost, 3), "recommended_arm": winner,
            "recommended_cost": round(winner_cost, 3),
            "improvement": round(1.0 - winner_cost / base_cost, 6) if base_cost else 0.0,
            "action": "candidate_wins" if improved else "hold_baseline",
            "apply_automatically": False,
            "candidates": details,
        })
    return {"version": 1, "baseline": baseline, "cost_field": cost_field,
            "min_improvement": min_improvement, "candidates": list(candidates),
            "production_mutation": False, "cells": out}


def show(report: dict[str, Any]) -> str:
    lines = ["shadow posture selection (recommendation only; production is unchanged)",
             "  cell                          winner                 cost      improvement  action"]
    for cell in report["cells"]:
        name = f"{cell['scenario']}@{cell['window'] // 1000}K"
        cost = cell.get("recommended_cost")
        cost_text = f"{cost:.1f}" if isinstance(cost, (int, float)) else "-"
        lines.append(f"  {name:<29} {cell['recommended_arm']:<22} {cost_text:>8}  {cell.get('improvement', 0.0) * 100:>10.1f}%  {cell['action']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    runp = sub.add_parser("run", help="run the candidate matrix and write rows plus a shadow report")
    runp.add_argument("--rows", type=Path, required=True)
    runp.add_argument("--report", type=Path, required=True)
    runp.add_argument("--scenarios", default="default,oncall,refactor,smallrows,i18n,bursty")
    runp.add_argument("--windows", default="64000,128000,200000")
    runp.add_argument("--seeds", default="0,1,2,3,4,5")
    selp = sub.add_parser("select", help="select postures from an existing matrix rows file")
    selp.add_argument("rows", type=Path)
    selp.add_argument("--out", type=Path)
    showp = sub.add_parser("show", help="print a shadow report")
    showp.add_argument("report", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "run":
        import matrix

        csv = lambda text, cast=str: [cast(part) for part in text.split(",") if part]
        result = matrix.run(CANDIDATES, tuple(csv(args.scenarios)), tuple(csv(args.windows, int)), tuple(csv(args.seeds, int)))
        args.rows.write_text(matrix.dump(result), encoding="utf-8")
        report = select(result["rows"])
        args.report.write_text(dump_report(report), encoding="utf-8")
        print(show(report))
        return 0
    if args.cmd == "select":
        text = dump_report(select(load_rows(args.rows)))
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0
    print(show(json.loads(args.report.read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
