"""Reprice the P1c development matrix under every dated price profile and print the tables of docs/REPRICED-1.10.0.md.

    python tests/eval/repriced_report.py tests/eval/results/repriced-1.10.0-rows.json [--json docs/results/repriced-1.10.0.json]

The rows file holds three arms run once each with the pricing ledger (``matrix.run(..., price_log=True)``): ``stock_defaults``,
``engine_1_9_0`` (the plugin at 836c5d3, vendored engine 1.9.0) and ``engine_1_10_0`` (vendored engine 1.10.0). Costs are replay-only.
No Hermes needed: everything here is a function of the committed rows and the committed price copy.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

import repricing  # noqa: E402
import stats  # noqa: E402

STOCK, OLD, NEW = "stock_defaults", "engine_1_9_0", "engine_1_10_0"
FIELDS = ("effective_real", "effective", "effective_finder")
COMPARISONS = ((NEW, OLD), (NEW, STOCK), (OLD, STOCK))
RESAMPLES = 10_000
# TTL sensitivities: (label, ttl, idle). The primary is no expiry; with the replay's own clock (20 s per request, no think time) no
# TTL of 5 min or more ever lapses, so each TTL is also read with the "mixed" think time (10 min before every third user turn).
TTLS = (("none", None, "none"), ("profile+mixed", "profile", "mixed"), ("300s+mixed", 300, "mixed"), ("3600s+mixed", 3600, "mixed"),
        ("profile+slow", "profile", "slow"))


def pricings(prices: dict) -> dict[str, repricing.Pricing]:
    out = {"original": repricing.ORIGINAL}
    for name in prices["profiles"]:
        out[name] = repricing.profile_pricing(name, prices)
        out[name + "@exact"] = repricing.profile_pricing(name, prices, granularity="exact")
    return out


def crossing(rows) -> dict:
    out = {}
    for (arm, window), group in sorted(_group(rows, ("arm", "window")).items()):
        n = len(group)
        out[f"{arm}@{window}"] = {"n": n, "ge1": sum(r["compactions"] >= 1 for r in group) / n,
                                  "ge2": sum(r["compactions"] >= 2 for r in group) / n}
    return out


def _group(rows, keys):
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(r[k] for k in keys)].append(r)
    return groups


def pooled(rows, arm, base, field, window, scenario=None) -> float:
    pick = lambda a: sum(r[field] for r in rows if r["arm"] == a and r["window"] == window and (scenario is None or r["scenario"] == scenario))
    b = pick(base)
    return 100 * (pick(arm) - b) / b if b else 0.0


def compare(rows, arm, base, field, window, resamples=RESAMPLES) -> dict:
    c = stats.compare(rows, base, arm, field=field, window=window, resamples=resamples)
    cl = c["cluster"]
    return {"pooled": round(pooled(rows, arm, base, field, window), 2), "mean": round(100 * cl["mean"], 2),
            "lo": round(100 * cl["lo"], 2), "hi": round(100 * cl["hi"], 2), "clusters": cl["clusters"], "cells": c["cells"]}


def weighted(rows_by_profile: dict, weights: dict, arm, base, field, window) -> dict:
    """Σ_p weight_p × change_p per cell, cluster bootstrap by scenario (the same resamples as every profile)."""
    per_cell = defaultdict(float)
    pooled_d = 0.0
    for name, wgt in weights.items():
        rows = rows_by_profile[name]
        pooled_d += wgt * pooled(rows, arm, base, field, window)
        for p in stats.paired(rows, base, arm, field):
            if p["cell"]["window"] == window:
                per_cell[(p["cell"]["scenario"], p["cell"]["seed"])] += wgt * p["rel"]
    clusters = defaultdict(list)
    for (scenario, _seed), v in sorted(per_cell.items()):
        clusters[scenario].append(v)
    ci = stats.cluster_bootstrap_ci(clusters, resamples=RESAMPLES)
    return {"pooled": round(pooled_d, 2), "mean": round(100 * ci["mean"], 2), "lo": round(100 * ci["lo"], 2),
            "hi": round(100 * ci["hi"], 2), "clusters": ci["clusters"], "cells": ci["n"]}


def sign(x: dict) -> str:
    if x["clusters"] > 1 and x["lo"] > 0:
        return "+"
    if x["clusters"] > 1 and x["hi"] < 0:
        return "-"
    return "0"


FLIP_POINTS = 0.5      # a point estimate that changes sign counts only when one side is at least this far from zero


def changed(ref: dict, value: dict) -> str | None:
    """How the repriced result differs in sign from the original price: ``decided`` (the cluster interval's verdict -, 0, + moved)
    or ``point`` (the point estimates have opposite signs and one is at least ``FLIP_POINTS`` from zero); None otherwise."""
    if sign(value) != sign(ref):
        return "decided"
    if ref["mean"] * value["mean"] < 0 and max(abs(ref["mean"]), abs(value["mean"])) >= FLIP_POINTS:
        return "point"
    return None


def means(rows, arm, window, keys) -> dict:
    group = [r for r in rows if r["arm"] == arm and r["window"] == window]
    return {k: round(sum(r[k] for r in group) / len(group), 3) for k in keys}


def self_check(rows_by_profile: dict) -> int:
    """Every arm against itself, every profile, window and field: exactly zero (asserted)."""
    checks = 0
    for rows in rows_by_profile.values():
        for arm in {r["arm"] for r in rows}:
            for window in {r["window"] for r in rows}:
                for field in FIELDS:
                    c = stats.compare(rows, arm, arm, field=field, window=window, resamples=50)
                    cl = c["cluster"]
                    assert c["cells"] > 0 and cl["mean"] == 0.0 and cl["lo"] == 0.0 and cl["hi"] == 0.0, (arm, window, field)
                    checks += 1
    return checks


def analyse(rows: list[dict], prices: dict) -> dict:
    windows = sorted({r["window"] for r in rows})
    for r in rows:      # the ledger reproduces every committed price exactly at the replay's own price
        assert repricing.original_fields(r) == {k: r[k] for k in ("base", "effective", "effective_real", "effective_finder")}
    variants: dict[str, dict] = {}
    for label, ttl, idle in TTLS:
        for name, p in pricings(prices).items():
            if label != "none" and (name == "original" or name.endswith("@exact")):
                continue
            if label != "none":
                p = repricing.profile_pricing(name, prices, ttl=ttl, idle=idle)
            variants[f"{name}|{label}"] = repricing.repriced_rows(rows, p)
    checks = self_check(variants)
    out: dict = {"windows": windows, "self_checks": checks, "crossing": crossing(rows), "profiles": {}, "weighted": {},
                 "split": {}, "scorecard": {}, "shapes": {}, "sign_changes": []}
    primary = {f"{n}|none" for n in ("original", *repricing.HERMES_WEIGHTS)}
    for key, prow in variants.items():
        resamples = RESAMPLES if key in primary else 2_000      # sensitivities: fewer resamples (stated in the doc)
        entry = out["profiles"][key] = {}
        for window in windows:
            for arm, base in COMPARISONS:
                for field in FIELDS:
                    entry[f"{window}|{arm}-{base}|{field}"] = compare(prow, arm, base, field, window, resamples)
    weights = repricing.HERMES_WEIGHTS
    for label, _ttl, _idle in TTLS:
        by = {name: variants[f"{name}|{label}"] for name in weights}
        for window in windows:
            for arm, base in COMPARISONS:
                for field in FIELDS:
                    out["weighted"][f"{label}|{window}|{arm}-{base}|{field}"] = weighted(by, weights, arm, base, field, window)
    split_keys = ("read", "write", "summary_input", "output", "effective_real", "expiries", "tier_requests")
    for name in ("original", *weights):
        prow = variants[f"{name}|none"]
        for window in windows:
            for arm in (STOCK, OLD, NEW):
                out["split"][f"{name}|{window}|{arm}"] = means(prow, arm, window, split_keys)
    for window in windows:
        for arm in (STOCK, OLD, NEW):
            group = [dict(r, held=r["kept"] + r["recoverable"]) for r in rows if r["arm"] == arm and r["window"] == window]
            out["scorecard"][f"{window}|{arm}"] = {k: round(sum(r[k] for r in group) / len(group), 3)
                                                   for k in ("compactions", "prefix_breaks", "prune_commits", "kept", "held", "lost",
                                                             "needed", "requests", "failed_turns")}
    # per shape: the weighted primary (no expiry) and the original price, per comparison and window
    scenarios = sorted({r["scenario"] for r in rows})
    for window in windows:
        for scenario in scenarios:
            for arm, base in COMPARISONS:
                cell = {"original": round(pooled(variants["original|none"], arm, base, "effective_real", window, scenario), 2),
                        "weighted": round(sum(w * pooled(variants[f"{n}|none"], arm, base, "effective_real", window, scenario)
                                              for n, w in weights.items()), 2)}
                for n in weights:
                    cell[n] = round(pooled(variants[f"{n}|none"], arm, base, "effective_real", window, scenario), 2)
                out["shapes"][f"{window}|{scenario}|{arm}-{base}"] = cell
    # sign changes against the original price (same window, comparison and field)
    for key, entry in out["profiles"].items():
        if key == "original|none":
            continue
        for sub, value in entry.items():
            ref = out["profiles"]["original|none"][sub]
            if changed(ref, value):
                out["sign_changes"].append({"profile": key, "cell": sub, "kind": changed(ref, value), "original": ref, "repriced": value})
    for key, value in out["weighted"].items():
        label, window, comp, field = key.split("|")
        ref = out["profiles"]["original|none"][f"{window}|{comp}|{field}"]
        if changed(ref, value):
            out["sign_changes"].append({"profile": f"weighted|{label}", "cell": f"{window}|{comp}|{field}", "kind": changed(ref, value),
                                        "original": ref, "repriced": value})
    return out


def fmt(x: dict) -> str:
    return f"{x['mean']:+.1f} [{x['lo']:+.1f}, {x['hi']:+.1f}]"


def tables(out: dict) -> str:
    lines = []
    windows = out["windows"]
    names = ["original", *repricing.HERMES_WEIGHTS, "deepseek-flash", "anthropic-opus-5.5-1h", "anthropic-sonnet-5.5-1h",
             "anthropic-haiku-5.5-1h", "openai-generic-12h"]
    for arm, base in COMPARISONS:
        lines.append(f"\n### `effective_real`, {arm} vs {base} (mean change %, cluster 95% interval; pooled D in brackets after)\n")
        lines.append("| Profile | " + " | ".join(f"{w // 1000}K" for w in windows) + " |")
        lines.append("|---|" + "---|" * len(windows))
        for name in names:
            e = out["profiles"][f"{name}|none"]
            lines.append(f"| {name} | " + " | ".join(f"{fmt(e[f'{w}|{arm}-{base}|effective_real'])} ({e[f'{w}|{arm}-{base}|effective_real']['pooled']:+.1f})"
                                                       for w in windows) + " |")
        for label, *_ in TTLS:
            e = out["weighted"]
            lines.append(f"| **weighted (Hermes, 1/5 each), {label}** | " + " | ".join(
                f"{fmt(e[f'{label}|{w}|{arm}-{base}|effective_real'])} ({e[f'{label}|{w}|{arm}-{base}|effective_real']['pooled']:+.1f})" for w in windows) + " |")
    lines.append("\n### All three prices, weighted, no expiry\n")
    lines.append("| Comparison | Field | " + " | ".join(f"{w // 1000}K" for w in windows) + " |")
    lines.append("|---|---|" + "---|" * len(windows))
    for arm, base in COMPARISONS:
        for field in FIELDS:
            lines.append(f"| {arm} vs {base} | {field} | " + " | ".join(fmt(out["weighted"][f"none|{w}|{arm}-{base}|{field}"]) for w in windows) + " |")
            lines.append(f"| (original price) | {field} | " + " | ".join(fmt(out["profiles"]["original|none"][f"{w}|{arm}-{base}|{field}"]) for w in windows) + " |")
    lines.append("\n### Exact prefix instead of the provider rule (`effective_real`, 1.10.0 vs 1.9.0)\n")
    lines.append("| Profile | " + " | ".join(f"{w // 1000}K" for w in windows) + " |")
    lines.append("|---|" + "---|" * len(windows))
    for name in repricing.HERMES_WEIGHTS:
        e = out["profiles"][f"{name}@exact|none"]
        lines.append(f"| {name} (exact) | " + " | ".join(fmt(e[f"{w}|{NEW}-{OLD}|effective_real"]) for w in windows) + " |")
    lines.append("\n### Cost split per answered turn (chars in miss units, no expiry): read / write / summary input / output\n")
    lines.append("| Profile | Window | stock | 1.9.0 | 1.10.0 |")
    lines.append("|---|---|---|---|---|")
    for name in ("original", *repricing.HERMES_WEIGHTS):
        for w in windows:
            cells = []
            for arm in (STOCK, OLD, NEW):
                s = out["split"][f"{name}|{w}|{arm}"]
                cells.append(f"{s['read'] / 1000:.0f}K / {s['write'] / 1000:.0f}K / {s['summary_input'] / 1000:.0f}K / {s['output'] / 1000:.1f}K"
                             + (f" (tier {s['tier_requests']:.0f})" if s["tier_requests"] else ""))
            lines.append(f"| {name} | {w // 1000}K | " + " | ".join(cells) + " |")
    lines.append("\n### Scorecard per session (price-independent)\n")
    lines.append("| Window | Arm | compactions | cache breaks | prune commits | facts kept | held | lost | needed | failed turns | sessions with ≥1 / ≥2 compactions |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for w in windows:
        for arm in (STOCK, OLD, NEW):
            s = out["scorecard"][f"{w}|{arm}"]
            c = out["crossing"][f"{arm}@{w}"]
            lines.append(f"| {w // 1000}K | {arm} | {s['compactions']:.2f} | {s['prefix_breaks']:.2f} | {s['prune_commits']:.2f} | {s['kept']:.2f} | "
                         f"{s['held']:.2f} | {s['lost']:.2f} | {s['needed']:.2f} | {s['failed_turns']:.2f} | {c['ge1'] * 100:.0f}% / {c['ge2'] * 100:.0f}% |")
    lines.append("\n### Per shape, pooled `effective_real` change % (original price / registered weighted, no expiry)\n")
    scen = sorted({k.split("|")[1] for k in out["shapes"]})
    lines.append("| Window | Shape | 1.10.0 vs 1.9.0 | 1.10.0 vs stock | 1.9.0 vs stock |")
    lines.append("|---|---|---|---|---|")
    for w in windows:
        for s in scen:
            cells = [out["shapes"][f"{w}|{s}|{a}-{b}"] for a, b in COMPARISONS]
            lines.append(f"| {w // 1000}K | {s} | " + " | ".join(f"{c['original']:+.1f} / {c['weighted']:+.1f}" for c in cells) + " |")
    return "\n".join(lines)


def merge(stock: dict, old: dict, new: dict) -> dict:
    """One rows file from the three runs; the product arm of each engine run is renamed after its engine."""
    rows = [r for r in stock["rows"] if r["arm"] == STOCK]
    rows += [dict(r, arm=OLD) for r in old["rows"] if r["arm"] == "tameru_1_4_product"]
    rows += [dict(r, arm=NEW) for r in new["rows"] if r["arm"] == "tameru_1_4_product"]
    meta = {"arms": [STOCK, OLD, NEW], "runs": {STOCK: stock["meta"], OLD: old["meta"], NEW: new["meta"]},
            "note": "engine_1_9_0: the plugin at 836c5d3 (vendored engine 1.9.0) with this commit's tests/eval harness; replay only"}
    return {"meta": meta, "rows": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rows")
    parser.add_argument("--json")
    parser.add_argument("--merge", nargs=3, metavar=("STOCK", "OLD", "NEW"), help="write ROWS from three matrix files first")
    args = parser.parse_args(argv)
    if args.merge:
        parts = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.merge]
        Path(args.rows).write_text(json.dumps(merge(*parts), indent=None, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    data = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    out = analyse(data["rows"], repricing.load_prices())
    print(f"self-checks (arm vs itself, exactly zero): {out['self_checks']}")
    print(tables(out))
    print(f"\nsign changes against the original price: {len(out['sign_changes'])}")
    for s in out["sign_changes"]:
        print(f"  {s['kind']:<8} {s['profile']:<36} {s['cell']:<55} original {fmt(s['original'])}  repriced {fmt(s['repriced'])}")
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
