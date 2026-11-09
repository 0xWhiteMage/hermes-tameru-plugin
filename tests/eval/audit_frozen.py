"""Audit protocol-1 captures without rerunning any context engine.

Usage: python tests/eval/audit_frozen.py CAPTURE_DIRECTORY NEW_OUTPUT_JSON
Fixture hashes must still match the capture. Existing artifacts are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def audit(capture: Path) -> dict:
    hashes = {}

    def read(name):
        raw = (capture / name).read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    manifest = read("manifest.json")
    fixture_hashes = {
        name: digest for name, digest in manifest["source_files"].items()
        if name.startswith("tests/fixtures/") or (
            name.startswith(("tests/eval/scenario", "tests/eval/payloads"))
            and name != "tests/eval/scenario_baselines.py")
    }
    for name, digest in fixture_hashes.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Fixture differs from the frozen capture: {name}")
    sys.path.insert(0, str(ROOT / "tests"))
    from scenario_registry import get_spec

    @lru_cache(maxsize=None)
    def facts(scenario, seed, turns):
        return tuple(get_spec(scenario).build(seed, turns).gold)

    protocol = manifest["protocol"]
    engines = protocol["engines"]
    if engines != ["stock_defaults", "tameru_1_4_product"]:
        raise ValueError("This historical audit requires the recorded stock/product pair")
    cells = []
    for scenario in protocol["scenarios"]:
        for window in protocol["windows"]:
            for seed in protocol["seeds"]:
                cell = read(f"{scenario}-{window}-{seed}.json")
                if (cell["scenario"], cell["window"], cell["seed"]) != (scenario, window, seed):
                    raise ValueError("Capture identity does not match its filename")
                arms = {}
                for engine in engines:
                    run = cell["results"][engine]
                    metrics = run["metrics"]
                    gold = facts(scenario, seed, run["meta"]["turns"])
                    retained = set()
                    for category, recorded in metrics["gold"]["by_category"].items():
                        ids = {fact.id for fact in gold if fact.category == category}
                        missing = set(recorded["missing"])
                        if (len(ids) != recorded["total"] or not missing <= ids
                                or len(ids - missing) != recorded["retained"]):
                            raise ValueError(f"Gold identity/count mismatch: {scenario}/{seed}/{engine}/{category}")
                        retained.update(ids - missing)
                    if len(retained) != metrics["gold"]["retained"]:
                        raise ValueError("Aggregate retained count does not match reconstructed identities")
                    needed = {fact.id for fact in gold if fact.needed}
                    if (len(needed) != metrics["refetch"]["needed_facts"]
                            or needed - retained != set(metrics["refetch"]["missing_ids"])):
                        raise ValueError("Required-fact reconstruction disagrees with the recorded refetch metric")
                    # In v1, a failure event records the number of requests already sent.
                    # If that count never increases, no subsequent request (including
                    # the closing question) was sent. This is inference, not a send receipt.
                    closing_unsent = any(
                        event["kind"] == "turn_failed" and event["request"] == metrics["requests"]
                        for event in run["series"]["events"]
                    )
                    arms[engine] = {
                        "main_message_cost": metrics["cost_cache_priced"],
                        "raw_auxiliary_chars": metrics["summary_prompt_chars"],
                        "raw_auxiliary_cost_sensitivity": metrics["cost_cache_priced"] + metrics["summary_prompt_chars"],
                        "failed_turns": metrics["failed_turns"],
                        "closing_unsent_inferred": closing_unsent,
                        "retained_ids_under_v1_observation": sorted(retained),
                        "retained_required_ids_under_v1_observation": sorted(retained & needed),
                        "required_total": len(needed),
                    }
                stock, plugin = (arms[name] for name in engines)
                cells.append({
                    "scenario": scenario, "window": window, "seed": seed, "arms": arms,
                    "lost_ids_under_v1_observation": sorted(
                        set(stock["retained_ids_under_v1_observation"])
                        - set(plugin["retained_ids_under_v1_observation"])),
                    "lost_required_ids_under_v1_observation": sorted(
                        set(stock["retained_required_ids_under_v1_observation"])
                        - set(plugin["retained_required_ids_under_v1_observation"])),
                })

    def prices(key):
        pairs = [(cell["arms"][engines[0]][key], cell["arms"][engines[1]][key]) for cell in cells]
        if any(stock <= 0 for stock, _ in pairs):
            raise ValueError("A nonpositive baseline prevents the stated paired-cost comparison")
        return {
            "mean_stock": statistics.mean(stock for stock, _ in pairs),
            "mean_plugin": statistics.mean(plugin for _, plugin in pairs),
            "mean_paired_change_pct": 100 * statistics.mean(plugin / stock - 1 for stock, plugin in pairs),
            "aggregate_change_pct": 100 * (sum(plugin for _, plugin in pairs) / sum(stock for stock, _ in pairs) - 1),
            "pairs_with_higher_plugin_cost": sum(plugin > stock for stock, plugin in pairs),
        }

    return {
        "historical_only": True,
        "limits": [
            "No engine execution, model answer scoring, provider billing or new closing-send witness.",
            "Raw auxiliary sensitivity adds raw summary prompt chars at uncached 1x to old main-message cost.",
            "This sensitivity excludes auxiliary serialization and all schema costs; it is not protocol 2.",
            "Identity reconstruction uses v1 missing-ID lists and hash-verified original fixture definitions.",
            "An inferred unsent closing probe cannot substantiate final-question retention.",
        ],
        "input_sha256": hashes, "fixture_sha256": fixture_hashes,
        "cells": cells,
        "summary": {
            "paired_cells": len(cells),
            "old_main_message_pricing": prices("main_message_cost"),
            "raw_auxiliary_sensitivity": prices("raw_auxiliary_cost_sensitivity"),
            "closing_unsent_inferred": {
                name: sum(cell["arms"][name]["closing_unsent_inferred"] for cell in cells) for name in engines},
            "pairs_losing_any_fact_id": sum(bool(cell["lost_ids_under_v1_observation"]) for cell in cells),
            "pairs_losing_required_fact_id": sum(bool(cell["lost_required_ids_under_v1_observation"]) for cell in cells),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        raise SystemExit("Output must be a new file in an existing directory")
    result = audit(args.capture)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
