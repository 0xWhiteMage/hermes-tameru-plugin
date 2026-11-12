"""Run a replay matrix and write its rows; summarise them with the committed statistics.

    python tests/eval/matrix.py run  --out tests/eval/results/dev-v0.23.1.json [--arms A,B] [--scenarios S,T] [--windows 64000,...] [--seeds 0,1,...]
    python tests/eval/matrix.py show tests/eval/results/dev-v0.23.1.json [--base stock_defaults]

Rows are ``protocol3.measure`` rows (structural seeds, Hermes' measured 39,993-character tool block). They are deterministic: the same code,
Hermes commit and arguments give byte-identical output, so the rows can be committed and the headline numbers regenerated from them.
Timing is not recorded here (see ``measure(..., timing=True)``) because it would break that identity.

Needs a real Hermes checkout (``HERMES_REPO_ROOT``). The default matrix is the development set; the locked holdouts are run once per
product hash through ``holdout_register`` and never as part of a default run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

TOOL_BLOCK_CHARS = 39_993          # Hermes' tool block, measured (docs/COST-TUNING-2026-11-10.md)
DEVELOPMENT = ("default", "oncall", "refactor", "smallrows", "i18n", "bursty")
DEPENDENT = ("dependent",)         # round 2: the next requests need the large output just shown (run with --scenarios; not in the default set)
ARMS = ("stock_defaults", "tameru_1_4_product", "tameru_lazy", "tameru_autotune_off")
WINDOWS = (64_000, 128_000, 200_000)
SEEDS = (0, 1, 2, 3, 4, 5)


def _csv(text: str, cast=str):
    return [cast(part) for part in text.split(",") if part]


def run(arms, scenarios, windows, seeds, *, write: float | None = None, read: float | None = None) -> dict:
    import protocol3
    import replay
    import scenarios_blind
    import scenarios_dependent
    from holdout_register import product_hash

    scenarios_blind.register_blind()
    scenarios_dependent.register_dependent()
    saved = (replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS)
    replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS = TOOL_BLOCK_CHARS, TOOL_BLOCK_CHARS // 4
    try:
        with protocol3.pricing(write=write, read=read):
            rows = [protocol3.measure(arm, scenario, window, seed, structural=True)
                    for scenario in scenarios for window in windows for arm in arms for seed in seeds]
    finally:
        replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS = saved
        scenarios_blind.unregister_blind()
        scenarios_dependent.unregister_dependent()
    meta = {"product": product_hash(), "tool_block_chars": TOOL_BLOCK_CHARS, "structural": True,
            "write": replay.CACHE_WRITE if write is None else write, "read": replay.CACHE_READ if read is None else read,
            "arms": list(arms), "scenarios": list(scenarios), "windows": list(windows), "seeds": list(seeds)}
    return {"meta": meta, "rows": rows}


def dump(result: dict) -> str:
    return json.dumps(result, indent=1, sort_keys=True) + "\n"


def scorecard(rows, *, arms=None, windows=None) -> list[dict]:
    """Per arm and window, the means of every scorecard line (cost, facts, runway, requests, cache breaks, prompt size)."""
    from collections import defaultdict

    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        if (arms is None or row["arm"] in arms) and (windows is None or row["window"] in windows):
            groups[(row["arm"], row["window"])].append(row)
    out = []
    for (arm, window), group in sorted(groups.items()):
        n = len(group)
        mean = lambda key: round(sum(r[key] for r in group) / n, 3)
        first = [r["first_compaction_request"] for r in group if r["first_compaction_request"] is not None]
        out.append({"arm": arm, "window": window, "runs": n, "effective": mean("effective"), "base": mean("base"),
                    "needed": mean("needed"), "kept": mean("kept"), "recoverable": mean("recoverable"), "lost": mean("lost"),
                    "host_compactions": mean("compactions"), "requests": mean("requests"), "prune_commits": mean("prune_commits"),
                    "prefix_breaks": mean("prefix_breaks"), "mean_request_chars": mean("mean_request_chars"),
                    "first_compaction_request": round(sum(first) / len(first), 2) if first else None,
                    "sessions_without_compaction": n - len(first)})
    return out


def show(result: dict, base: str = "stock_defaults") -> str:
    import stats

    rows = result["rows"]
    lines = [f"cost per answered turn, effective, against {base} (mean relative change; cluster 95% interval over shapes)"]
    arms = [a for a in result["meta"]["arms"] if a != base]
    for window in result["meta"]["windows"]:
        for arm in arms:
            c = stats.compare(rows, base, arm, window=window, resamples=4_000)
            cl = c["cluster"]
            lines.append(f"  {window // 1000:>3}K  {arm:<22} {cl['mean'] * 100:+6.1f}%  [{cl['lo'] * 100:+6.1f}, {cl['hi'] * 100:+6.1f}]"
                         f"  cells {c['cells']}  {'excludes 0' if c['excludes_zero'] else 'includes 0'}")
    return "\n".join(lines)


# ---- the promotion gate (fixed before the first rule matrix was looked at; see the 1.10.0 plan) ---------------------------
PROMOTION = {
    "primary": "the paired interval on the mean change of effective cost excludes zero (cheaper) at every window",
    "no_cell_worse_than": 0.02,        # no scenario and window with a mean change above +2% whose interval excludes zero
    "guards": ("held", "errors_kept"),  # needed facts held (kept + recoverable) and error templates kept must not be lower
    "windows": (64_000, 128_000, 200_000),
    # Round 2 (fixed before any round-2 result was read; panel review of the round-1 gate):
    "price_fields": ("effective", "effective_real"),   # the old price and the one that charges a read-back as the extra request it is
    "facts_margin_window": 0.3,        # needed facts kept outright (and kept + recoverable) may fall by at most this many per session on the mean...
    "facts_margin_shape": 1.0,         # ...and by at most this many in any one shape and window (a margin, not an interval test)
    "dependent_ceiling": 0.5,          # needed detail missing in the 1-3 requests after a shortened output, per session, at most
    "rise_margin": {"prefix_breaks": 0.10, "rewritten_chars": 0.10, "requests": 0.05},   # relative rise of the mean allowed
    "primary_idea": "frontier",        # the one idea named in advance; every other arm is secondary and reported with the number tried
    "blind_looks": 1,                  # one look at the blind shapes per idea; a code change after it makes those shapes development for that idea
}


def _paired(rows, base: str, arm: str, field: str, window: int):
    """Per (scenario, seed): arm minus base on ``field`` for rows at ``window``; rows without the field are skipped."""
    index = {(r["scenario"], r["window"], r["seed"]): r for r in rows if r["arm"] == base and field in r}
    out: dict[str, list[float]] = {}
    for r in rows:
        if r["arm"] != arm or r["window"] != window or field not in r:
            continue
        other = index.get((r["scenario"], window, r["seed"]))
        if other is not None:
            out.setdefault(r["scenario"], []).append(r[field] - other[field])
    return out


def margin_failures(rows, base: str, arm: str) -> list[str]:
    """The round-2 guards that are margins on means: facts kept outright, facts held, dependent-use misses, cache breaks, rewrites, requests."""
    failures: list[str] = []
    for window in PROMOTION["windows"]:
        tag = f"{window // 1000}K"
        for field in ("kept", "held"):
            work = [dict(r, held=r["kept"] + r["recoverable"]) for r in rows]
            by_shape = _paired(work, base, arm, field, window)
            if not by_shape:
                continue
            every = [d for ds in by_shape.values() for d in ds]
            mean = sum(every) / len(every)
            if mean < -PROMOTION["facts_margin_window"]:
                failures.append(f"{tag}: {field} facts fall by {-mean:.2f} per session (margin {PROMOTION['facts_margin_window']})")
            for shape, ds in sorted(by_shape.items()):
                if sum(ds) / len(ds) < -PROMOTION["facts_margin_shape"]:
                    failures.append(f"{tag} {shape}: {field} facts fall by {-sum(ds) / len(ds):.2f} per session (margin {PROMOTION['facts_margin_shape']})")
        arm_missing = [r["dependent_missing"] for r in rows if r["arm"] == arm and r["window"] == window and "dependent_missing" in r]
        if arm_missing and sum(arm_missing) / len(arm_missing) > PROMOTION["dependent_ceiling"]:      # an absolute ceiling: the base scores 0 by construction
            failures.append(f"{tag}: needed detail missing after a shortened output {sum(arm_missing) / len(arm_missing):.2f} per session (ceiling {PROMOTION['dependent_ceiling']})")
        for field, rise in PROMOTION["rise_margin"].items():
            base_rows = [r[field] for r in rows if r["arm"] == base and r["window"] == window and field in r]
            arm_rows = [r[field] for r in rows if r["arm"] == arm and r["window"] == window and field in r]
            if base_rows and arm_rows:
                b, a = sum(base_rows) / len(base_rows), sum(arm_rows) / len(arm_rows)
                if b > 0 and a > b * (1 + rise):
                    failures.append(f"{tag}: {field} rises {(a / b - 1) * 100:+.1f}% (margin {rise * 100:.0f}%)")
    return failures


def promote(rows, base: str, arm: str, *, resamples: int = 4_000) -> dict:
    """Judge ``arm`` against ``base`` (the shipped product) by the fixed gate. Every number is paired by scenario, window and seed."""
    import stats

    rows = [dict(r, held=r["kept"] + r["recoverable"]) for r in rows]
    verdict = {"arm": arm, "base": base, "windows": {}, "failures": []}
    for window in PROMOTION["windows"]:
        cost = stats.compare(rows, base, arm, field="effective", window=window, resamples=resamples)
        entry = {"cost": {k: cost[k] for k in ("cells", "cluster", "excludes_zero")}}
        if not (cost["excludes_zero"] and cost["cluster"]["mean"] < 0):
            verdict["failures"].append(f"{window // 1000}K: cost change {cost['cluster']['mean'] * 100:+.1f}% does not exclude zero on the cheaper side")
        if any("effective_real" in r for r in rows):      # rows written before round 2 have no realistic price
            real = stats.compare(rows, base, arm, field="effective_real", window=window, resamples=resamples)
            entry["cost_real"] = {k: real[k] for k in ("cells", "cluster", "excludes_zero")}
            if not (real["excludes_zero"] and real["cluster"]["mean"] < 0):
                verdict["failures"].append(f"{window // 1000}K: cost change at the realistic read-back price {real['cluster']['mean'] * 100:+.1f}% does not exclude zero on the cheaper side")
        for field in PROMOTION["guards"]:
            g = stats.compare(rows, base, arm, field=field, window=window, resamples=resamples)
            entry[field] = {k: g[k] for k in ("cells", "cluster", "excludes_zero")}
            if g["cluster"]["clusters"] > 1 and g["cluster"]["hi"] < 0:
                verdict["failures"].append(f"{window // 1000}K: {field} is lower ({g['cluster']['mean'] * 100:+.1f}%, interval below zero)")
        worst = [c for c in stats.per_cell(rows, base, arm, "effective") if c["window"] == window
                 and c["excludes_zero"] and c["mean"] > PROMOTION["no_cell_worse_than"]]
        entry["worse_cells"] = [{"scenario": c["scenario"], "mean": c["mean"]} for c in worst]
        for c in worst:
            verdict["failures"].append(f"{window // 1000}K {c['scenario']}: {c['mean'] * 100:+.1f}% dearer, interval excludes zero")
        verdict["windows"][str(window)] = entry
    verdict["failures"].extend(margin_failures(rows, base, arm))
    verdict["promote"] = not verdict["failures"]
    return verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--arms", default=",".join(ARMS))
    r.add_argument("--scenarios", default=",".join(DEVELOPMENT))
    r.add_argument("--windows", default=",".join(map(str, WINDOWS)))
    r.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    r.add_argument("--write", type=float)
    r.add_argument("--read", type=float)
    s = sub.add_parser("show")
    s.add_argument("path")
    s.add_argument("--base", default="stock_defaults")
    q = sub.add_parser("promote")
    q.add_argument("path")
    q.add_argument("--base", default="tameru_1_4_product")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        result = run(_csv(args.arms), _csv(args.scenarios), _csv(args.windows, int), _csv(args.seeds, int), write=args.write, read=args.read)
        Path(args.out).write_text(dump(result), encoding="utf-8")
        print(f"wrote {args.out}: {len(result['rows'])} rows")
        print(show(result))
    elif args.cmd == "promote":
        result = json.loads(Path(args.path).read_text(encoding="utf-8"))
        for arm in result["meta"]["arms"]:
            if arm in (args.base, "stock_defaults"):
                continue
            v = promote(result["rows"], args.base, arm)
            print(f"{arm}: {'PROMOTE' if v['promote'] else 'does not pass'}")
            for line in v["failures"]:
                print("   -", line)
    else:
        print(show(json.loads(Path(args.path).read_text(encoding="utf-8")), args.base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
