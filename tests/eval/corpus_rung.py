"""Per-level retention for round-2 corpus rows that carry ``row_rung`` (P2c rung split; replay only).

    python tests/eval/corpus_rung.py [rows.jsonl] [--root build/session_corpus] [--json docs/results/replay-corpus-round2-rung.json]

For every window: facts whose tool row is still in the final prompt but shortened, by the ladder level the row was cut to
(``row_rung``; ``hermes`` = a changed row with no Tameru header): held/n, %, and the level's share of all shortened fact
rows — overall and split by fact kind, task style and language. Also: for every fact in a ``superseded`` row, whether a
later step of the same slice still holds the fact (recomputed from the corpus with ``corpus_adapter``): a superseded row
drops a unique fact only when no later step repeats it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

LEVELS = ["fold", "extract", "errors", "brief", "header", "superseded", "hermes"]
KINDS = ["natural", "error", "buried", "real"]
STYLES = ["specific", "vague"]
LANGS = ["en", "zh", "mixed"]


def _level_order(levels) -> list:
    return [l for l in LEVELS if l in levels] + sorted(set(levels) - set(LEVELS))


def _acc():
    return {"n": 0, "held": 0, "status": defaultdict(int)}


def rung_tables(rows, window: int) -> dict:
    """held/n per level, overall and by fact kind / task style / language, for one window."""
    facts = [(r, f) for r in rows for f in r.get("facts", []) if f.get("row_shortened")]
    overall: dict = defaultdict(_acc)
    by: dict = {"kind": defaultdict(lambda: defaultdict(_acc)),
                "style": defaultdict(lambda: defaultdict(_acc)),
                "language": defaultdict(lambda: defaultdict(_acc))}
    for r, f in facts:
        lvl = f.get("row_rung") or "hermes"
        overall[lvl]["n"] += 1
        overall[lvl]["held"] += bool(f["held_in_shortened_row"])
        overall[lvl]["status"][f["status"]] += 1
        for group, key in (("kind", f["kind"]), ("style", r["style"]), ("language", r["language"])):
            by[group][key][lvl]["n"] += 1
            by[group][key][lvl]["held"] += bool(f["held_in_shortened_row"])
            by[group][key][lvl]["status"][f["status"]] += 1
    total = len(facts)
    total_not_held = sum(1 for r, f in facts if not f["held_in_shortened_row"])
    levels = _level_order(overall)
    def cell(c):
        not_held = c["n"] - c["held"]
        return {"n": c["n"], "held": c["held"], "pct": round(100 * c["held"] / c["n"], 1) if c["n"] else None,
                "share": round(100 * c["n"] / total, 1) if total else None,
                "not_held": not_held,
                "not_held_share": round(100 * not_held / total_not_held, 1) if total_not_held else None,
                "status": {s: c["status"].get(s, 0) for s in ("kept", "recoverable", "lost")}}
    return {
        "shortened_fact_rows": total,
        "levels": {l: cell(overall[l]) for l in levels},
        "by_kind": {k: {l: cell(by["kind"][k][l]) for l in _level_order(by["kind"][k])} for k in KINDS if k in by["kind"]},
        "by_style": {s: {l: cell(by["style"][s][l]) for l in _level_order(by["style"][s])} for s in STYLES if s in by["style"]},
        "by_language": {g: {l: cell(by["language"][g][l]) for l in _level_order(by["language"][g])} for g in LANGS if g in by["language"]},
    }


def superseded_later(rows, window: int, corpus_root: Path, split: str) -> dict:
    """For every fact in a ``superseded`` row, does a later step of the same slice still hold it? yes/no counts."""
    import corpus_adapter as ca
    out = {"yes": 0, "no": 0}
    by_id = {}
    for r in rows:
        if r["split"] != split:
            continue
        ids = [f["id"] for f in r.get("facts", []) if f.get("row_rung") == "superseded"]
        if ids:
            by_id[r["scenario"]] = ids
    for sid, ids in sorted(by_id.items()):
        mapped = ca.map_scenario(ca.load_scenario(corpus_root, split, sid), window)
        facts = {f.id: f for f in mapped.facts}
        for fid in ids:
            f = facts[fid]
            later = any(ca.fact_held(f, mapped.step_results[i]) for i in range(f.step + 1, len(mapped.step_results)))
            out["yes" if later else "no"] += 1
    return out


def _pct(c):
    return f"{c['pct']:.1f}" if c and c["pct"] is not None else "-"


def markdown(out: dict, windows: list[int]) -> str:
    def k(w):
        return f"{w // 1_000_000}M" if w >= 1_000_000 else f"{w // 1_000}K"
    L = []
    for w in windows:
        t = out["windows"][str(w)]
        not_held = sum(c["not_held"] for c in t["levels"].values())
        L.append(f"\n### {k(w)} — shortened fact rows by ladder level (held/n, %, share of all {t['shortened_fact_rows']}, "
                 f"share of the {not_held} not held, status split)\n")
        L.append("| level | held | n | % | share | not-held share | kept | recoverable | lost |")
        L.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
        for lvl, c in t["levels"].items():
            s = c["status"]
            L.append(f"| {lvl} | {c['held']} | {c['n']} | {_pct(c)} | {c['share']:.1f} | {c['not_held_share']:.1f} | "
                     f"{s['kept']} | {s['recoverable']} | {s['lost']} |")
        for title, group, keys in (("by fact kind", "by_kind", KINDS), ("by task style", "by_style", STYLES),
                                   ("by language", "by_language", LANGS)):
            cols = [x for x in keys if x in t[group]]
            if not cols:
                continue
            L.append(f"\n{title} — held/n per level:\n")
            L.append("| level | " + " | ".join(cols) + " |")
            L.append("|---|" + "--:|" * len(cols))
            lvls = []
            for x in cols:
                lvls += [l for l in t[group][x] if l not in lvls]
            for lvl in _level_order(lvls):
                L.append(f"| {lvl} | " + " | ".join(
                    f"{t[group][x][lvl]['held']}/{t[group][x][lvl]['n']}" if lvl in t[group][x] else "-" for x in cols) + " |")
        s = t["superseded_later"]
        L.append(f"\nFacts in a `superseded` row whose line a later step still holds: **yes {s['yes']} / no {s['no']}** "
                 f"({s['yes'] + s['no']} facts over dev+validation).")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rows", type=Path, nargs="?", default=ROOT / "build" / "replay-corpus-round2-rung" / "rows.jsonl")
    ap.add_argument("--root", type=Path, default=ROOT / "build" / "session_corpus")
    ap.add_argument("--json", type=Path, default=ROOT / "docs" / "results" / "replay-corpus-round2-rung.json")
    args = ap.parse_args(argv)

    rows, errors = [], 0
    for line in args.rows.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if "error" in r:
            errors += 1
        else:
            rows.append(r)
    arms = sorted({r["arm"] for r in rows})
    windows = sorted({r["window"] for r in rows})
    out = {"arms": arms, "errors": errors, "windows": {}}
    for w in windows:
        wrows = [r for r in rows if r["window"] == w]
        t = rung_tables(wrows, w)
        t["superseded_later"] = {"yes": 0, "no": 0}
        for split in ("dev", "validation"):
            s = superseded_later(wrows, w, args.root, split)
            t["superseded_later"]["yes"] += s["yes"]
            t["superseded_later"]["no"] += s["no"]
        out["windows"][str(w)] = t
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"rows: {len(rows)} ({errors} error rows skipped); arms: {', '.join(arms)}; windows: {', '.join(map(str, windows))}")
    print(markdown(out, windows))
    print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
