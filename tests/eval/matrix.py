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
ARMS = ("stock_defaults", "tameru_1_4_product", "tameru_lazy", "tameru_autotune_off")
RULE_ARMS = ("tameru_share_cached", "tameru_payback_early", "tameru_payback", "tameru_accrual")   # decision-rule arms (1.10)
WINDOWS = (64_000, 128_000, 200_000)
SEEDS = (0, 1, 2, 3, 4, 5)


def _csv(text: str, cast=str):
    return [cast(part) for part in text.split(",") if part]


def run(arms, scenarios, windows, seeds, *, write: float | None = None, read: float | None = None) -> dict:
    import protocol3
    import replay
    import scenarios_blind
    from holdout_register import product_hash

    scenarios_blind.register_blind()
    saved = (replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS)
    replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS = TOOL_BLOCK_CHARS, TOOL_BLOCK_CHARS // 4
    try:
        with protocol3.pricing(write=write, read=read):
            rows = [protocol3.measure(arm, scenario, window, seed, structural=True)
                    for scenario in scenarios for window in windows for arm in arms for seed in seeds]
    finally:
        replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS = saved
        scenarios_blind.unregister_blind()
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
}


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
