"""Print a side-by-side metrics table for registered engines (used when tuning, and for the docs).

    HERMES_REPO_ROOT=<hermes checkout> python tests/eval/compare.py [engine ...] [--json] [--scenario NAME]

Defaults to every engine in ``engines.BASELINE_ENGINES`` plus any extra registered ones named on the command
line. ``--old-harness`` runs the old replay (real clock, no context-overflow path; kept for comparison only) and
``--seconds-per-request N`` sets the simulated clock (default 20). ``--scenario NAME`` picks the session shape (``default``, ``oncall``, ``refactor``, ``smallrows``, ``i18n``;
see ``scenario_registry``); ``--seed N`` its seed. Not collected by pytest. Runs against a throwaway ``HERMES_HOME`` unless ``TAMERU_EVAL_HERMES_HOME=<dir>``
or ``TAMERU_EVAL_KEEP_HERMES_HOME=1`` (keep the one in the environment).
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def _bootstrap() -> None:
    root = os.environ.get("HERMES_REPO_ROOT", "").strip()
    if not root:
        sys.exit("set HERMES_REPO_ROOT to a Hermes Agent checkout")
    # Always a throwaway HERMES_HOME, even when one is set in the environment: replays write state (memory,
    # sessions, plugin store), and a developer's shell often exports their real home. Opt-outs:
    # TAMERU_EVAL_HERMES_HOME=<dir> picks the directory, TAMERU_EVAL_KEEP_HERMES_HOME=1 keeps $HERMES_HOME.
    chosen = os.environ.get("TAMERU_EVAL_HERMES_HOME", "").strip()
    if chosen:
        os.environ["HERMES_HOME"] = chosen
    elif os.environ.get("TAMERU_EVAL_KEEP_HERMES_HOME", "").strip() == "1" and os.environ.get("HERMES_HOME"):
        pass
    else:
        home = tempfile.mkdtemp(prefix="tameru-eval-home-")
        os.environ["HERMES_HOME"] = home
        atexit.register(shutil.rmtree, home, ignore_errors=True)
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
        ("overflow errors / failed turns", f"{m['overflow_errors']} / {m['failed_turns']}" if "overflow_errors" in m else "n/a"),
        ("max request / window", f"{m['max_request_window_ratio']:.2f}" if "max_request_window_ratio" in m else "n/a"),
        ("prune commits (rows)", f"{m['prune_commits']} ({m['prune_rows_demoted']})"),
        ("prefix invalidated (chars)", f"{m['prefix_invalidated_chars']:,}"),
        ("gold retained", f"{gold['retained']}/{gold['total']}  [{cats}]"),
        ("error fingerprints", f"{m['error_fingerprints']['retained']}/{m['error_fingerprints']['total']}"),
        ("re-fetch opportunities", f"{m['refetch']['refetch_opportunities']} calls ({m['refetch']['missing_needed_facts']} facts)"),
        ("pairing violations", str(m["pairing"]["violations"])),
        ("rows rewritten between commits", str(m["byte_stability"]["rewrites_outside_commits"]) if "byte_stability" in m else "n/a"),
        ("input chars (final request)", f"{m['input_chars_final']:,}"),
        ("engine seconds", f"{result['timing']['engine_seconds']:.2f}"),
    ]


def table(results: dict[str, dict]) -> str:
    """The side-by-side metrics table for ``{engine name: run_session result}``."""
    names = list(results)
    cells = {n: dict(rows(r)) for n, r in results.items()}
    labels = [k for k, _ in rows(next(iter(results.values())))]
    width = max(len(label) for label in labels)
    cols = {n: max(len(n), *(len(v) for v in cells[n].values())) for n in names}
    lines = [f"{'metric':<{width}}  " + "  ".join(f"{n:<{cols[n]}}" for n in names)]
    for label in labels:
        lines.append(f"{label:<{width}}  " + "  ".join(f"{cells[n][label]:<{cols[n]}}" for n in names))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    _bootstrap()
    import engines
    import replay

    import scenario_registry

    as_json = "--json" in argv
    options, rest = parse_options(argv, ("--scenario", "--seed", "--seconds-per-request", "--window"))
    run_opts = {"realistic": "--old-harness" not in argv}
    if "--seconds-per-request" in options:
        run_opts["seconds_per_request"] = float(options["--seconds-per-request"])
    if "--window" in options:
        run_opts["context_length"] = int(options["--window"])
    scenario = options.get("--scenario", scenario_registry.DEFAULT_SCENARIO)
    seed = int(options.get("--seed", "0"))
    try:
        scenario_registry.get_spec(scenario)  # fail early, listing the registered names
    except KeyError as exc:
        raise SystemExit(str(exc.args[0])) from None
    names = [a for a in rest if not a.startswith("--")] or list(engines.BASELINE_ENGINES)
    results = {n: replay.run_session(lambda n=n: engines.make_engine(n), scenario=scenario, seed=seed, **run_opts)
               for n in names}
    if as_json:
        print(json.dumps({n: {"metrics": r["metrics"], "timing": r["timing"]} for n, r in results.items()}, indent=1))
        return 0
    if scenario != scenario_registry.DEFAULT_SCENARIO or seed or len(run_opts) > 1 or not run_opts["realistic"]:
        print(f"scenario: {scenario}  seed: {seed}  {run_opts}")
    print(table(results))
    return 0


def parse_options(argv: list[str], valued: tuple[str, ...]) -> tuple[dict[str, str], list[str]]:
    """Split ``--opt value`` / ``--opt=value`` pairs for the options in ``valued`` from the other arguments."""
    options: dict[str, str] = {}
    rest: list[str] = []
    it = iter(argv)
    for arg in it:
        key, eq, val = arg.partition("=")
        if key in valued:
            if not eq:
                val = next(it, "")
            if not val:
                raise SystemExit(f"{key} needs a value")
            options[key] = val
        else:
            rest.append(arg)
    return options, rest


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
