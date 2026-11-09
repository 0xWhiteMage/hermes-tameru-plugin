"""Freeze the 60-cell product replay and its provenance before writing release prose.

This is a deterministic simulation with a lossy fake summarizer, fixed schema
overhead and character-based cache pricing. It does not measure model accuracy,
actual token bills, provider cache behaviour or live Desktop operation.

Run with HERMES_REPO_ROOT set: python tests/eval/release_matrix.py OUTPUT_DIRECTORY
The output directory must not exist. Each paired cell is saved once.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ("default", "oncall", "refactor", "smallrows", "i18n")
WINDOWS = (32_000, 48_000, 64_000, 128_000, 200_000, 256_000)
SEEDS = (0, 1)
ENGINES = ("stock_defaults", "tameru_1_4_product")


def source_manifest() -> dict[str, str]:
    files = list((ROOT / "hermes_tameru_plugin").rglob("*.py"))
    files += list((ROOT / "tests" / "eval").glob("*.py"))
    files += list((ROOT / "tests" / "fixtures").glob("*.py"))
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }


def summarize(cells: list[dict]) -> dict:
    """Summarize captured cells only; never rerun an engine to render a report."""
    if not cells:
        raise ValueError("No captured cells to summarize")
    if any(cell["results"][name]["meta"].get("protocol_version") != 2
           for cell in cells for name in ENGINES):
        raise ValueError("Protocol-2 aggregation requires protocol-2 captures; historical results stay unchanged")
    result = {}
    for name in ENGINES:
        rows = [cell["results"][name]["metrics"] for cell in cells]
        observed = [row for row in rows if row["closing_probe"]["sent"]]
        result[name] = {
            "sessions": len(rows),
            "closing_probes_sent": len(observed),
            "closing_probes_unsent": len(rows) - len(observed),
            "failed_turns": sum(row["failed_turns"] for row in rows),
            "overflow_rejections": sum(row["overflow_errors"] for row in rows),
            "summary_calls": sum(row["summary_llm_calls"] for row in rows),
            "mean_simulated_cost_char_equivalents": statistics.mean(row["total_simulated_input_cost"] for row in rows),
            "mean_main_input_cost": statistics.mean(row["cost_cache_priced"] for row in rows),
            "mean_auxiliary_input_cost": statistics.mean(row["cost_auxiliary_input"] for row in rows),
            "gold_retained_observed": sum(row["gold"]["retained"] for row in observed) if observed else None,
            "gold_total_observed": sum(row["gold"]["total"] for row in observed) if observed else None,
            "error_templates_retained_observed": (
                sum(row["error_fingerprints"]["retained"] for row in observed) if observed else None),
            "error_templates_total_observed": (
                sum(row["error_fingerprints"]["total"] for row in observed) if observed else None),
            "pairing_violations": sum(row["pairing"]["violations"] for row in rows),
            "rewrites_outside_commits": sum(row["byte_stability"]["rewrites_outside_commits"] for row in rows),
            "caller_input_mutations": sum(row["byte_stability"]["input_mutations"] for row in rows),
            "unparsed_summary_prompts": sum(row["summary_unparsed_prompts"] for row in rows),
        }
    pairs = [
        (cell["results"][ENGINES[0]]["metrics"], cell["results"][ENGINES[1]]["metrics"])
        for cell in cells
    ]
    cost_key = "total_simulated_input_cost"
    positive_denominators = all(stock[cost_key] > 0 for stock, _ in pairs)
    baseline_total = sum(stock[cost_key] for stock, _ in pairs)
    observed_pairs = [
        (cell, stock, plugin) for cell, (stock, plugin) in zip(cells, pairs)
        if stock["closing_probe"]["sent"] and plugin["closing_probe"]["sent"]
    ]
    losses = []
    for cell, stock, plugin in observed_pairs:
        lost_facts = sorted(set(stock["gold"]["retained_ids"]) - set(plugin["gold"]["retained_ids"]))
        lost_errors = sorted(set(stock["error_fingerprints"]["retained_ids"])
                             - set(plugin["error_fingerprints"]["retained_ids"]))
        if lost_facts or lost_errors:
            losses.append({
                "scenario": cell["scenario"], "window": cell["window"], "seed": cell["seed"],
                "lost_fact_ids": lost_facts, "lost_error_fingerprints": lost_errors,
            })
    result["paired_comparison"] = {
        "cells": len(cells),
        "positive_baseline_cost_in_every_cell": positive_denominators,
        "mean_relative_simulated_cost_change_pct": (
            100 * statistics.mean(plugin[cost_key] / stock[cost_key] - 1 for stock, plugin in pairs)
            if positive_denominators else None),
        "aggregate_simulated_cost_change_pct": (
            100 * (sum(plugin[cost_key] for _, plugin in pairs) / baseline_total - 1)
            if baseline_total else None),
        "cells_with_higher_plugin_cost": sum(
            plugin[cost_key] > stock[cost_key] for stock, plugin in pairs
        ),
        "pairs_with_both_closing_probes": len(observed_pairs),
        "literal_probe_comparison_complete": len(observed_pairs) == len(pairs),
        "cells_with_lower_plugin_gold_retention": sum(
            plugin["gold"]["retained"] < stock["gold"]["retained"] for _, stock, plugin in observed_pairs
        ) if observed_pairs else None,
        "cells_with_lost_fact_ids": sum(bool(loss["lost_fact_ids"]) for loss in losses) if observed_pairs else None,
        "identity_losses_in_observed_pairs": losses,
    }
    return result


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: release_matrix.py OUTPUT_DIRECTORY")
    destination = Path(sys.argv[1]).resolve()
    if not destination.parent.is_dir():
        raise SystemExit("output parent must already exist")
    # Do not let launch-profile options tune the evaluated product implicitly.
    hermes_root = os.environ.get("HERMES_REPO_ROOT", "")
    if not hermes_root:
        raise SystemExit("HERMES_REPO_ROOT must name the real Hermes checkout")
    for key in list(os.environ):
        if key.startswith(("TAMERU_", "HERMES_")):
            del os.environ[key]
    os.environ["HERMES_REPO_ROOT"] = hermes_root
    import compare

    compare._bootstrap()
    import engines
    import replay

    # The module is imported lazily by the factories; establish its actual origin first.
    import agent.context_compressor  # noqa: F401

    hermes_source = replay._hermes_source()
    if hermes_source["hermes_pin"] is None or hermes_source["hermes_source_dirty"]:
        raise SystemExit("release replay requires an identified, clean Hermes checkout")
    destination.mkdir()
    manifest = source_manifest()
    cells = []
    metadata = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "system": platform.system(),
        "plugin_engine_version": engines.plugin_engine_version(),
        **hermes_source,
        "source_files": manifest,
        "protocol": {
            "version": replay.PROTOCOL_VERSION,
            "scenarios": SCENARIOS, "windows": WINDOWS, "seeds": SEEDS, "engines": ENGINES,
            "realistic": True, "seconds_per_request": 20.0,
            "limits": [
                "Synthetic sessions and a lossy fake summarizer; no model answer accuracy measured.",
                "Character-based simulated prefix-cache pricing; no actual billed token savings.",
                "Fixed baseline schemas assume 3000 tokens/12000 characters; added engine schemas are serialized.",
                "Auxiliary message inputs cost an assumed uncached 1x; output costs remain unmeasured.",
                "Cost comparisons include incomplete turns when an engine cannot recover overflow.",
                "Unsent closing probes have no quality observation and cannot satisfy a comparison gate.",
                "Gold and error totals count opportunities across sessions, not globally unique facts.",
                "Wall-clock timing is observational and machine load is uncontrolled.",
            ],
        },
    }
    (destination / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    for scenario in SCENARIOS:
        for window in WINDOWS:
            for seed in SEEDS:
                cell = {"scenario": scenario, "window": window, "seed": seed, "results": {}}
                for name in ENGINES:
                    result = replay.run_session(
                        lambda name=name: engines.make_engine(name),
                        scenario=scenario, context_length=window, seed=seed,
                        realistic=True, seconds_per_request=20.0,
                        keep_final_request=True,
                    )
                    if any(result["meta"][key] != hermes_source[key] for key in hermes_source):
                        raise RuntimeError("Hermes source changed during the matrix")
                    cell["results"][name] = result
                cells.append(cell)
                filename = f"{scenario}-{window}-{seed}.json"
                (destination / filename).write_text(json.dumps(cell, indent=2), encoding="utf-8")
                print(f"{len(cells)}/60 {filename}", flush=True)
    report = {
        **metadata, "source_unchanged_during_run": source_manifest() == manifest,
        "summary": summarize(cells),
        "by_scenario": {name: summarize([c for c in cells if c["scenario"] == name]) for name in SCENARIOS},
    }
    (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(destination / "report.json")
    return 0 if report["source_unchanged_during_run"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
