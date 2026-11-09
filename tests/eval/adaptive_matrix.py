"""Lead-authored protocol-2 comparisons; captures are immutable and reports read captures only.

Each arm runs in a separate interpreter with the SAME evaluator and host. Frozen
means the original plugin checkout, not current code with a legacy flag. Costs are
character-priced simulations; final literal presence is not task accuracy.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ARMS = ("frozen", "stock", "legacy_current", "adaptive_balanced", "fixed_balanced",
        "adaptive_save_space", "fixed_save_space", "adaptive_preserve_detail", "fixed_preserve_detail")
SCENARIOS = ("default", "oncall", "refactor", "smallrows", "i18n")
WINDOWS = (32_000, 48_000, 64_000, 128_000, 200_000, 256_000)
SEEDS = (0, 1)
FROZEN = "2ce219abfb937207068087a4fbcd0457846abbf7"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".js", ".yaml"}}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def write_new(path, value):
    data = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if path.name.endswith(".gz"):
        with path.open("xb") as output:
            output.write(gzip.compress(data.encode("utf-8"), mtime=0))
    else:
        with path.open("x", encoding="utf-8") as output:
            output.write(data)


def read(path):
    data = gzip.decompress(path.read_bytes()) if path.name.endswith(".gz") else path.read_bytes()
    return json.loads(data)


def freeze(args):
    if not args.output.parent.is_dir() or args.output.exists():
        raise ValueError("Choose a new output directory under an existing parent")
    assert git(args.frozen, "rev-parse", "HEAD") == FROZEN
    assert not git(args.frozen, "status", "--porcelain")
    assert not git(args.hermes, "status", "--porcelain", "--untracked-files=no")
    manifest = {
        "protocol": 2, "candidate_root": str(ROOT), "frozen_root": str(args.frozen),
        "hermes_root": str(args.hermes), "hermes_commit": git(args.hermes, "rev-parse", "HEAD"),
        "frozen_commit": FROZEN,
        "product": tree(ROOT / "hermes_tameru_plugin"),
        "frozen_product": tree(args.frozen / "hermes_tameru_plugin"),
        "evaluator": {str(p.relative_to(ROOT)): digest(p) for folder in (HERE, ROOT / "tests/fixtures")
                      for p in sorted(folder.glob("*.py"))},
        "arms": args.arms or ARMS, "scenarios": args.scenarios or SCENARIOS,
        "windows": args.windows or WINDOWS, "seeds": args.seeds or SEEDS,
        "budget": "Same host window; unspecified output headroom in all replay arms",
        "scope": "Scripted requests; fake summary model; no provider billing or task-accuracy claim",
    }
    args.output.mkdir()
    write_new(args.output / "manifest.json", manifest)


def verify(manifest):
    assert tree(ROOT / "hermes_tameru_plugin") == manifest["product"], "Candidate changed after freeze"
    frozen = Path(manifest["frozen_root"])
    assert tree(frozen / "hermes_tameru_plugin") == manifest["frozen_product"], "Frozen source changed"
    for name, sha in manifest["evaluator"].items():
        assert digest(ROOT / name) == sha, f"Evaluator changed: {name}"
    hermes = Path(manifest["hermes_root"])
    assert git(hermes, "rev-parse", "HEAD") == manifest["hermes_commit"]
    assert not git(hermes, "status", "--porcelain", "--untracked-files=no")


def cells(manifest, smoke=False):
    if smoke:
        return [("default", 64_000, 0)]
    return [(scenario, window, seed) for scenario in manifest["scenarios"]
            for window in manifest["windows"] for seed in manifest["seeds"]]


def capture_path(folder, arm, scenario, window, seed):
    return folder / f"{arm}--{scenario}--{window}--{seed}.json.gz"


def worker(args, manifest):
    # No user's model config, credentials or plugin env may affect a replay.
    for key in list(os.environ):
        if key.startswith(("TAMERU_", "HERMES_")):
            del os.environ[key]
    with tempfile.TemporaryDirectory(prefix="tameru-matrix-") as home:
        os.environ["HERMES_HOME"] = home
        os.environ["HERMES_REPO_ROOT"] = manifest["hermes_root"]
        plugin_root = Path(manifest["frozen_root"]) if args.arm == "frozen" else ROOT
        sys.path[:0] = [manifest["hermes_root"], str(plugin_root), str(HERE), str(ROOT / "tests")]
        import replay
        from scenario_registry import get_spec
        if args.arm == "stock":
            from agent.context_compressor import ContextCompressor
            factory = lambda: ContextCompressor(model="test-model", quiet_mode=True)
            settings = None
        else:
            from hermes_tameru_plugin.config import TameruSettings
            from hermes_tameru_plugin.engine import TameruContextEngine
            import hermes_tameru_plugin
            assert Path(hermes_tameru_plugin.__file__).resolve().parent.parent == plugin_root.resolve()
            if args.arm == "frozen":
                settings = TameruSettings()
            elif args.arm == "legacy_current":
                settings = TameruSettings(policy_mode="legacy")
            else:
                mode, preference = args.arm.split("_", 1)
                settings = TameruSettings(policy_mode=mode, preference=preference)
            factory = lambda: TameruContextEngine(settings=settings, quiet_mode=True)
        # Eager imports above mean every arm enters the same virtual-clock scope.
        for scenario, window, seed in cells(manifest, args.smoke):
            path = capture_path(args.output, args.arm, scenario, window, seed)
            if path.exists():
                continue
            spec = get_spec(scenario)
            script = spec.build(seed, spec.turns)
            result = replay.run_session(factory, seed=seed, context_length=window,
                                        scenario=scenario, keep_final_request=True)
            result["comparison"] = {
                "arm": args.arm, "scenario": scenario, "window": window, "seed": seed,
                "settings": asdict(settings) if settings is not None else None,
                "required_fact_ids": [g.id for g in script.gold if g.needed],
                "fixture_sha256": hashlib.sha256(
                    json.dumps(asdict(script), sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            }
            write_new(path, result)
            print(f"{args.arm} {scenario} {window} {seed}", flush=True)


def run(args, manifest):
    verify(manifest)
    def invoke(arm):
        command = [sys.executable, str(Path(__file__).resolve()), "worker", str(args.output), "--arm", arm]
        if args.smoke:
            command.append("--smoke")
        subprocess.run(command, check=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(invoke, manifest["arms"]))
    verify(manifest)


def summarize(args, manifest):
    """Pair every cell by identity; never replace missing closing probes with old requests."""
    wanted = cells(manifest, args.smoke)
    captured = {}
    for arm in manifest["arms"]:
        rows = []
        for scenario, window, seed in wanted:
            result = read(capture_path(args.output, arm, scenario, window, seed))
            assert result["meta"]["protocol_version"] == 2
            identity = result["comparison"]
            assert (identity["arm"], identity["scenario"], identity["window"], identity["seed"]) == (
                arm, scenario, window, seed)
            rows.append(result)
        captured[arm] = rows
    pairs = {}
    for arm in manifest["arms"]:
        if arm == "frozen":
            continue
        comparisons = {}
        baselines = ["frozen"]
        if arm.startswith("adaptive_"):
            baselines.append(arm.replace("adaptive_", "fixed_", 1))
        for baseline in baselines:
            deltas, findings, families = [], [], {}
            for cell, before, after in zip(wanted, captured[baseline], captured[arm]):
                assert before["comparison"]["fixture_sha256"] == after["comparison"]["fixture_sha256"]
                b, a = before["metrics"], after["metrics"]
                assert b["total_simulated_input_cost"] > 0
                delta = a["total_simulated_input_cost"] / b["total_simulated_input_cost"] - 1
                deltas.append(delta)
                family = families.setdefault(cell[0], {"cost_changes": [], "before_rewrites": 0,
                                                       "after_rewrites": 0})
                family["cost_changes"].append(delta)
                family["before_rewrites"] += b["prune_commits"] + b["compactions"]
                family["after_rewrites"] += a["prune_commits"] + a["compactions"]
                required = set(before["comparison"]["required_fact_ids"])
                b_ids, a_ids = b["gold"]["retained_ids"], a["gold"]["retained_ids"]
                lost = sorted(required & set(b_ids or []) - set(a_ids or []))
                issue = {
                    "cell": cell, "relative_cost_change": delta,
                    "baseline_closing_sent": b["closing_probe"]["sent"],
                    "candidate_closing_sent": a["closing_probe"]["sent"],
                    "lost_baseline_required_ids": lost if b_ids is not None else None,
                    "error_count_delta": (a["error_fingerprints"]["retained"] -
                                          b["error_fingerprints"]["retained"]) if (
                                              a_ids is not None and b_ids is not None) else None,
                    "overflow_delta": a["overflow_errors"] - b["overflow_errors"],
                    "failed_turn_delta": a["failed_turns"] - b["failed_turns"],
                }
                if (delta > 0 or lost or not a["closing_probe"]["sent"]
                        or issue["overflow_delta"] > 0 or issue["failed_turn_delta"] > 0
                        or (issue["error_count_delta"] is not None and issue["error_count_delta"] < 0)):
                    findings.append(issue)
            for family in families.values():
                family["mean_paired_relative_cost_change"] = statistics.mean(family.pop("cost_changes"))
                family["relative_rewrite_change"] = (
                    family["after_rewrites"] / family["before_rewrites"] - 1
                    if family["before_rewrites"] else None)
            comparisons[baseline] = {
                "mean_paired_relative_cost_change": statistics.mean(deltas),
                "aggregate_cost_change": sum(r["metrics"]["total_simulated_input_cost"]
                                             for r in captured[arm]) / sum(
                                                 r["metrics"]["total_simulated_input_cost"]
                                                 for r in captured[baseline]) - 1,
                "families": families, "unfavorable_cells": findings,
            }
        pairs[arm] = comparisons
    totals = {}
    for arm, results in captured.items():
        metrics = [r["metrics"] for r in results]
        totals[arm] = {
            "cells": len(metrics),
            "closing_unsent": sum(not m["closing_probe"]["sent"] for m in metrics),
            **{key: sum(m[key] for m in metrics) for key in (
                "total_simulated_input_cost", "cost_cache_priced", "cost_auxiliary_input",
                "failed_turns", "overflow_errors", "summary_llm_calls", "prune_commits", "compactions")},
            "pairing_violations": sum(m["pairing"]["violations"] for m in metrics),
            "unauthorized_rewrites": sum(m["byte_stability"]["rewrites_outside_commits"] for m in metrics),
            "input_mutations": sum(m["byte_stability"]["input_mutations"] for m in metrics),
            "summary_unparsed_prompts": sum(m["summary_unparsed_prompts"] for m in metrics),
        }
    report = {"scope": "smoke only" if args.smoke else "complete development matrix",
              "totals": totals, "comparisons": pairs}
    write_new(args.output / ("smoke-summary.json" if args.smoke else "summary.json"), report)
    print(json.dumps(totals, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "worker", "report"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--hermes", type=Path)
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--arms", type=lambda s: tuple(s.split(",")))
    parser.add_argument("--scenarios", type=lambda s: tuple(s.split(",")))
    parser.add_argument("--windows", type=lambda s: tuple(int(w) for w in s.split(",")))
    parser.add_argument("--seeds", type=lambda s: tuple(int(w) for w in s.split(",")))
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.action == "freeze":
        freeze(args)
    else:
        manifest = read(args.output / "manifest.json")
        {"run": run, "worker": worker, "report": summarize}[args.action](args, manifest)


if __name__ == "__main__":
    main()
