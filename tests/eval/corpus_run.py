"""Replay the round-2 session corpus (dev / validation only) and write one row per arm, scenario and window (P2c; replay only).

    python tests/eval/corpus_run.py run --root build/session_corpus --splits dev,validation --windows 128000,252000 \
        --arms stock_defaults,tameru_1_4_product --label tameru_1_4_product=engine_1_10_0 --out rows.jsonl [--ids-file F] [--workers 12]

Needs a real Hermes checkout (``HERMES_REPO_ROOT``) and runs against a throwaway ``HERMES_HOME``. Rows are appended to ``--out`` as
JSON lines as they finish (a rerun skips the (arm, scenario, window) rows already there). Every row carries the pricing ledger
(``repricing.price_row``) and ``wall_s`` (wall-clock seconds of that run: informative, never part of an identity check).

Scenarios are mapped by ``corpus_adapter``; a fact is scored by ADAPTERS.md's line check. The closing request is scored per fact:
``kept`` (held in the final prompt), ``recoverable`` (not held, but ``tameru_read_original`` on a ref in the prompt returns the line),
``lost`` otherwise (priced as a re-run of the step, as ``protocol3.measure`` does).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TOOL_BLOCK_CHARS = 39_993          # Hermes' tool block, measured (docs/COST-TUNING-2026-11-10.md), as matrix.py
_SETUP_DONE = False


def setup() -> None:
    """Paths and a throwaway HERMES_HOME (a replay writes Hermes state; never the user's)."""
    global _SETUP_DONE
    if _SETUP_DONE:
        return
    root = os.environ.get("HERMES_REPO_ROOT", "").strip()
    if not root or not (Path(root) / "agent" / "context_compressor.py").is_file():
        raise SystemExit("set HERMES_REPO_ROOT to a Hermes Agent checkout")
    home = tempfile.mkdtemp(prefix="tameru-corpus-home-")
    os.environ["HERMES_HOME"] = home
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    import atexit
    atexit.register(shutil.rmtree, home, ignore_errors=True)
    for p in (str(HERE), str(HERE.parent), str(ROOT), root):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)
    _SETUP_DONE = True


def _call_id(seed: int, step: int) -> str:
    return f"call_{seed:02d}{step + 1:06d}"     # replay._play: one id per step, in order


def row_level(row_text: str) -> str:
    """The ladder rung a changed row was cut to; ``hermes`` when the row carries no Tameru header
    (e.g. Hermes' own one-line summary)."""
    from hermes_tameru_plugin.render import parse_header
    h = parse_header(row_text)
    return h["rung"] if h else "hermes"


def measure(arm: str, mapped, *, label: str | None = None, split: str = "", seed: int = 0) -> dict:
    """One replay of ``arm`` on a mapped corpus scenario; the row (protocol3-shaped fields plus the corpus columns)."""
    import corpus_adapter as ca
    import engines
    import protocol3
    import replay
    import scenario_registry as registry
    from scenario_kit import ScenarioSpec

    name = f"corpus/{mapped.id}@{mapped.window}"
    script = mapped.scenario
    registry.register_scenario(ScenarioSpec(name, lambda _seed, _turns: script, len(script.turns) + 1, 1, "round-2 corpus"))
    made: list = []

    def factory():
        made.append(engines.make_engine(arm))
        return made[-1]

    saved = (replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS)
    replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS = TOOL_BLOCK_CHARS, TOOL_BLOCK_CHARS // 4
    started = time.perf_counter()
    try:
        result = replay.run_session(factory, scenario=name, context_length=mapped.window, seed=seed, keep_final_request=True,
                                    price_log=True)
    finally:
        replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS = saved
        registry.SCENARIOS.pop(name, None)
    wall = time.perf_counter() - started
    metrics = result["metrics"]
    final = result.get("final_request") or []
    text = replay.visible_text(final)
    lines = ca.prompt_lines(text)
    refs = list(dict.fromkeys(protocol3._REF_RE.findall(text)))
    engine = _Memo(made[-1])
    _scoring_grep(True)
    tool_rows = {m.get("tool_call_id"): m for m in final if isinstance(m, dict) and m.get("role") == "tool"}
    shortened = sum(1 for i, res in enumerate(mapped.step_results)
                    if _call_id(seed, i) in tool_rows and replay._content_text(tool_rows[_call_id(seed, i)].get("content")) != res)

    row_prompt = metrics["input_chars_total"] / max(1, metrics["requests"])
    per_request = replay.CACHE_READ * row_prompt
    question = script.final_question
    W = replay.CACHE_WRITE
    over = protocol3.CALL_OVERHEAD_CHARS
    extra = extra_real = extra_finder = 0.0
    terms: dict[str, list] = {"effective": [], "effective_real": [], "effective_finder": []}
    held: dict[str, bool] = {}
    fact_rows = []
    kept = recoverable = lost = finder_found = 0
    for f in mapped.facts:
        size = len(mapped.step_results[f.step])
        is_held = ca.fact_held(f, text, lines)
        held[f.id] = is_held
        row = tool_rows.get(_call_id(seed, f.step))
        row_text = replay._content_text(row.get("content")) if row is not None else None
        row_short = row_text is not None and row_text != mapped.step_results[f.step]
        in_row = ca.fact_held(f, row_text) if row_short else None
        status = "kept"
        if is_held:
            kept += 1
        else:
            returned = None
            for needle in dict.fromkeys((f.line, ca.escaped(f.line))):
                returned = protocol3._read_back(engine, refs, needle)
                if returned is not None:
                    break
            if returned is not None:
                status = "recoverable"
                recoverable += 1
                extra += W * (returned + over)
                extra_real += per_request + W * (returned + over)
                terms["effective"].append([0, 0, 0, 0, returned + over])
                terms["effective_real"].append([0, 0, 0, row_prompt, returned + over])
                tool = f.call_key.split(":", 1)[0]
                attempts, got = protocol3._weak_find(engine, text, refs, tool, question, f.line)
                if got is not None:
                    finder_found += 1
                    extra_finder += (attempts - 1) * (per_request + W * over) + per_request + W * (got + over)
                    terms["effective_finder"].append([attempts - 1, row_prompt, over, row_prompt, got + over])
                else:
                    extra_finder += attempts * (per_request + W * over) + per_request + W * (size + over)
                    terms["effective_finder"].append([attempts, row_prompt, over, row_prompt, size + over])
            else:
                status = "lost"
                lost += 1
                rerun = per_request + W * (size + over)
                extra += W * (size + over)
                extra_real += rerun
                extra_finder += rerun
                for k in ("effective", "effective_real", "effective_finder"):
                    terms[k].append([0, 0, 0, 0 if k == "effective" else row_prompt, size + over])
        fact_rows.append({"id": f.id, "kind": f.kind, "position": f.position, "status": status,
                          "value_present": ca.value_present(f, text), "row_present": row is not None,
                          "row_shortened": row_short, "held_in_shortened_row": in_row,
                          "row_rung": row_level(row_text) if row_short else None})
    questions = [{"id": q["id"], "stratum": q["stratum"], "held": ca.question_held(q, held)} for q in mapped.questions]
    answered = max(1, metrics["turns"] - metrics["failed_turns"])
    base = metrics["total_simulated_input_cost"]
    compress_at = [e["request"] for e in result["series"]["events"] if e.get("kind") == "compress"]
    return {
        "arm": label or arm, "engine_arm": arm, "scenario": mapped.id, "family": mapped.family, "language": mapped.language,
        "style": mapped.style, "split": split, "window": mapped.window, "seed": seed, "steps": mapped.steps,
        "real_share": round(mapped.real_chars / max(1, mapped.total_chars), 4), "slice_chars": mapped.total_chars,
        "compactions": metrics["compactions"], "failed_turns": metrics["failed_turns"], "requests": metrics["requests"],
        "prune_commits": metrics["prune_commits"], "prefix_breaks": metrics["prefix_break_events"],
        "rewritten_chars": metrics["prefix_invalidated_chars"], "threshold_tokens": metrics["threshold_tokens"],
        "first_compaction_request": compress_at[0] if compress_at else None,
        "mean_request_chars": round(row_prompt, 1), "final_observed": bool(final),
        "base": round(base / answered, 1), "effective": round((base + extra) / answered, 1),
        "effective_real": round((base + extra_real) / answered, 1), "effective_finder": round((base + extra_finder) / answered, 1),
        "needed": len(mapped.facts), "kept": kept, "recoverable": recoverable, "lost": lost, "finder_found": finder_found,
        "shortened_rows": shortened, "tool_rows_in_final": sum(1 for i in range(mapped.steps) if _call_id(seed, i) in tool_rows),
        "facts": fact_rows, "questions": questions,
        "pricing": {"answered": answered, "summary_input_chars": metrics["cost_auxiliary_input"],
                    "summary_output_chars": sum(o for _, o in result["price_log"]["summaries"]),
                    "requests": result["price_log"]["requests"], "summaries": result["price_log"]["summaries"],
                    "readback_terms": terms, "compress_events": len(compress_at), "threshold_tokens": metrics["threshold_tokens"]},
        "wall_s": round(wall, 2), "engine_seconds": round(result["timing"]["engine_seconds"], 2),
        "score_s": round(time.perf_counter() - started - wall, 2),
    }


class _Memo:
    """The finished session's engine, for scoring only: an identical read-back call (same ref, same grep) is answered once."""

    def __init__(self, engine):
        self._engine = engine
        self._cache: dict[str, str] = {}

    def get_tool_schemas(self):
        return self._engine.get_tool_schemas()

    def handle_tool_call(self, name, args):
        key = name + "\0" + json.dumps(args, sort_keys=True, ensure_ascii=False)
        if key not in self._cache:
            self._cache[key] = self._engine.handle_tool_call(name, args)
        return self._cache[key]


_ORIGINAL_GREP: list = []


def _scoring_grep(on: bool) -> None:
    """While the closing request is scored (after the session, never during it), the recovery tool's grep runs in-process with
    exactly the child's code (``re.compile(p, re.IGNORECASE)``, ``search`` per text) instead of one timed child process per call:
    the same answer whenever the child finishes, and the child's wall-clock timeout (which a loaded machine can hit) cannot make a
    row depend on machine load. The harness's own patterns are escaped lines and alternations of escaped words: nothing backtracks."""
    import re

    try:
        from hermes_tameru_plugin import recovery
    except Exception:
        return
    if not hasattr(recovery, "_grep_hits"):
        return
    if not _ORIGINAL_GREP:
        _ORIGINAL_GREP.append(recovery._grep_hits)
    if not on:
        recovery._grep_hits = _ORIGINAL_GREP[0]
        return

    def in_process(pattern, texts, timeout):
        p = re.compile(pattern.pattern, re.IGNORECASE)
        return [i for i, t in enumerate(texts) if p.search(t)], ""

    recovery._grep_hits = in_process


# ------------------------------------------------------------------------------------------------ worker pool
def _work(job):
    setup()
    import corpus_adapter as ca
    root, split, sid, window, arm, label = job
    try:
        raw = ca.load_scenario(Path(root), split, sid)
        mapped = ca.map_scenario(raw, window)
        del raw
        return measure(arm, mapped, label=label, split=split)
    except ca.HoldoutRefused:
        raise
    except Exception as e:      # recorded, never silently dropped: the report counts error rows
        import traceback
        return {"arm": label, "scenario": sid, "window": window, "split": split, "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:]}
    finally:
        _scoring_grep(False)


def _key(row) -> tuple:
    return (row["arm"], row["scenario"], row["window"])


def run(root: Path, splits, windows, arms, labels: dict, out: Path, ids: set | None, workers: int) -> int:
    import corpus_adapter as ca

    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(_key(json.loads(line)))
    jobs = []
    for window in windows:
        for split in splits:
            for row in ca.load_index(root, split):
                if ids is not None and row["id"] not in ids:
                    continue
                for arm in arms:
                    label = labels.get(arm, arm)
                    if (label, row["id"], window) not in done:
                        jobs.append((str(root), split, row["id"], window, arm, label))
    # longest first (more steps), so the pool finishes evenly
    jobs.sort(key=lambda j: -j[3])
    print(f"{len(jobs)} runs to do ({len(done)} already in {out})", flush=True)
    started = time.perf_counter()
    if workers <= 1:
        results = map(_work, jobs)
        pool = None
    else:
        import multiprocessing as mp
        pool = mp.get_context("spawn").Pool(workers, maxtasksperchild=8)
        results = pool.imap_unordered(_work, jobs)
    n = 0
    with out.open("a", encoding="utf-8", newline="\n") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            n += 1
            if n % 20 == 0 or n == len(jobs):
                print(f"  {n}/{len(jobs)}  {time.perf_counter() - started:.0f}s", flush=True)
    if pool is not None:
        pool.close()
        pool.join()
    print(f"done: {n} rows in {time.perf_counter() - started:.0f}s wall", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", type=Path, default=ROOT / "build" / "session_corpus")
    r.add_argument("--splits", default="dev,validation")
    r.add_argument("--windows", default="128000,252000")
    r.add_argument("--arms", default="stock_defaults,tameru_1_4_product")
    r.add_argument("--label", action="append", default=[], help="ARM=LABEL (the name written in the rows)")
    r.add_argument("--ids-file", type=Path, help="one scenario id per line (a logged subsample)")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = ap.parse_args(argv)
    setup()
    import corpus_adapter as ca
    splits = [s for s in args.splits.split(",") if s]
    for s in splits:
        ca.split_dir(args.root, s)          # refuses anything but dev / validation
    labels = dict(item.split("=", 1) for item in args.label)
    ids = None
    if args.ids_file:
        ids = {line.strip() for line in args.ids_file.read_text(encoding="utf-8").splitlines() if line.strip()}
    return run(args.root, splits, [int(w) for w in args.windows.split(",") if w], [a for a in args.arms.split(",") if a],
               labels, args.out, ids, args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
