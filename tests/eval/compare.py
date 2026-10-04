"""Print a side-by-side metrics table for registered engines (used when tuning, and for the docs).

    HERMES_REPO_ROOT=<hermes checkout> python tests/eval/compare.py [engine ...] [--json]

Defaults to every engine in ``engines.BASELINE_ENGINES`` plus any extra registered ones named on the command
line. Not collected by pytest.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def _bootstrap() -> None:
    root = os.environ.get("HERMES_REPO_ROOT", "").strip()
    if not root:
        sys.exit("set HERMES_REPO_ROOT to a Hermes Agent checkout")
    os.environ.setdefault("HERMES_HOME", tempfile.mkdtemp(prefix="tameru-eval-home-"))
    for p in (str(ROOT), str(HERE), str(Path(root).expanduser().resolve())):
        if p not in sys.path:
            sys.path.insert(0, p)


def rows(result: dict) -> list[tuple[str, str]]:
    m = result["metrics"]
    gold = m["gold"]
    cats = " ".join(f"{c[:3]}={v['retained']}/{v['total']}" for c, v in gold["by_category"].items())
    return [
        ("requests", str(m["requests"])),
        ("input chars (total)", f"{m['input_chars_total']:,}"),
        ("cost, cache-priced (char-eq)", f"{m['cost_cache_priced']:,.0f}"),
        ("cost / uncached", f"{m['cost_ratio_vs_uncached']:.3f}"),
        ("compress calls / changed list / shrank", f"{m['compress_calls']} / {m['compactions']} / {m['compactions_shrinking']}"),
        ("summary LLM calls", str(m["summary_llm_calls"])),
        ("prune commits (rows)", f"{m['prune_commits']} ({m['prune_rows_demoted']})"),
        ("prefix invalidated (chars)", f"{m['prefix_invalidated_chars']:,}"),
        ("gold retained", f"{gold['retained']}/{gold['total']}  [{cats}]"),
        ("error fingerprints", f"{m['error_fingerprints']['retained']}/{m['error_fingerprints']['total']}"),
        ("re-fetch opportunities", f"{m['refetch']['refetch_opportunities']} calls ({m['refetch']['missing_needed_facts']} facts)"),
        ("pairing violations", str(m["pairing"]["violations"])),
        ("engine seconds", f"{result['timing']['engine_seconds']:.2f}"),
    ]


def main(argv: list[str]) -> int:
    _bootstrap()
    import engines
    import replay

    as_json = "--json" in argv
    names = [a for a in argv if not a.startswith("--")] or list(engines.BASELINE_ENGINES)
    results = {n: replay.run_session(lambda n=n: engines.make_engine(n)) for n in names}
    if as_json:
        print(json.dumps({n: {"metrics": r["metrics"], "timing": r["timing"]} for n, r in results.items()}, indent=1))
        return 0
    table = {n: dict(rows(r)) for n, r in results.items()}
    labels = [k for k, _ in rows(next(iter(results.values())))]
    width = max(len(label) for label in labels)
    cols = {n: max(len(n), *(len(v) for v in table[n].values())) for n in names}
    print(f"{'metric':<{width}}  " + "  ".join(f"{n:<{cols[n]}}" for n in names))
    for label in labels:
        print(f"{label:<{width}}  " + "  ".join(f"{table[n][label]:<{cols[n]}}" for n in names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
