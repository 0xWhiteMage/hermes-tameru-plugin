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
    meta = {"product": product_hash(), "tool_block_chars": TOOL_BLOCK_CHARS, "structural": True,
            "write": replay.CACHE_WRITE if write is None else write, "read": replay.CACHE_READ if read is None else read,
            "arms": list(arms), "scenarios": list(scenarios), "windows": list(windows), "seeds": list(seeds)}
    return {"meta": meta, "rows": rows}


def dump(result: dict) -> str:
    return json.dumps(result, indent=1, sort_keys=True) + "\n"


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
    args = parser.parse_args(argv)
    if args.cmd == "run":
        result = run(_csv(args.arms), _csv(args.scenarios), _csv(args.windows, int), _csv(args.seeds, int), write=args.write, read=args.read)
        Path(args.out).write_text(dump(result), encoding="utf-8")
        print(f"wrote {args.out}: {len(result['rows'])} rows")
        print(show(result))
    else:
        print(show(json.loads(Path(args.path).read_text(encoding="utf-8")), args.base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
