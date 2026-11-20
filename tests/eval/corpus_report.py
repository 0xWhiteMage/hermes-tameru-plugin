"""Tables of docs/REPLAY-CORPUS-ROUND2.md from the corpus rows (``corpus_run.py``) and the 64K development-matrix rows (P2c; replay only).

    python tests/eval/corpus_report.py ROWS.jsonl [ROWS.jsonl ...] [--matrix64 tests/eval/results/repriced-1.10.0-rows.json] \
        [--elapsed elapsed.txt] [--json out.json] [--md out.md]

No Hermes needed. Arms: ``stock_defaults``, ``engine_1_9_0`` (the plugin at 836c5d3), ``engine_1_10_0`` (this tree). Every scenario
is one cluster (one seed per scenario), so the cluster bootstrap is by scenario. Prices: ``repricing`` under the registered Hermes
profiles (provider prefix rules, no expiry) and the replay's own price; TTL sensitivities are not run here.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

import repriced_report as rr  # noqa: E402
import repricing  # noqa: E402
import stats  # noqa: E402

STOCK, OLD, NEW = rr.STOCK, rr.OLD, rr.NEW
ARMS = (STOCK, OLD, NEW)
COMPARISONS = rr.COMPARISONS
FIELDS = rr.FIELDS
PROFILES = ("original", *repricing.HERMES_WEIGHTS)
GATE_GE1, GATE_GE2 = 0.70, 0.30          # round-2 plan section 2: trigger crossing at a gated window


def load(paths) -> tuple[list[dict], list[dict]]:
    rows, errors = [], []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                (errors if "error" in r else rows).append(r)
    return rows, errors


def _g(rows, **kv):
    return [r for r in rows if all(r[k] == v for k, v in kv.items())]


def crossing(rows) -> dict:
    out = {}
    for arm in ARMS:
        for w in sorted({r["window"] for r in rows}):
            for split in ("dev", "validation", "all"):
                g = [r for r in rows if r["arm"] == arm and r["window"] == w and (split == "all" or r["split"] == split)]
                if g:
                    n = len(g)
                    out[f"{arm}|{w}|{split}"] = {"n": n, "ge1": sum(r["compactions"] >= 1 for r in g) / n,
                                                 "ge2": sum(r["compactions"] >= 2 for r in g) / n}
    return out


def timing(rows) -> dict:
    out = {}
    for arm in ARMS:
        for w in sorted({r["window"] for r in rows}):
            t = sorted(r["wall_s"] for r in _g(rows, arm=arm, window=w))
            if t:
                out[f"{arm}|{w}"] = {"n": len(t), "mean": round(statistics.fmean(t), 2), "median": round(t[len(t) // 2], 2),
                                     "p95": round(t[int(0.95 * (len(t) - 1))], 2), "max": round(t[-1], 2), "sum": round(sum(t), 1)}
    return out


def _facts(rows):
    for r in rows:
        for f in r["facts"]:
            yield r, f


def fact_rates(rows, key) -> dict:
    """Facts kept (held in the final prompt) and kept + recoverable, per arm, window and ``key(row, fact)``."""
    acc = defaultdict(lambda: [0, 0, 0])
    for r, f in _facts(rows):
        k = (r["arm"], r["window"], key(r, f))
        acc[k][0] += 1
        acc[k][1] += f["status"] == "kept"
        acc[k][2] += f["status"] != "lost"
    return {"|".join(map(str, k)): {"n": n, "kept": round(100 * a / n, 1), "held": round(100 * b / n, 1)} for k, (n, a, b) in acc.items()}


def question_rates(rows) -> dict:
    acc = defaultdict(lambda: [0, 0])
    for r in rows:
        for q in r["questions"]:
            if q["held"] is None:
                continue
            k = (r["arm"], r["window"], q["stratum"])
            acc[k][0] += 1
            acc[k][1] += bool(q["held"])
    return {"|".join(map(str, k)): {"n": n, "held": round(100 * h / n, 1)} for k, (n, h) in acc.items()}


def facts_delta(rows, arm, base, window) -> dict:
    """Per scenario: arm minus base in facts kept (points of that scenario's needed facts); cluster bootstrap by scenario."""
    a = {r["scenario"]: r for r in _g(rows, arm=arm, window=window)}
    b = {r["scenario"]: r for r in _g(rows, arm=base, window=window)}
    clusters = {s: [100 * (a[s]["kept"] - b[s]["kept"]) / max(1, a[s]["needed"])] for s in sorted(a) if s in b}
    ci = stats.cluster_bootstrap_ci(clusters, resamples=10_000)
    ci1 = stats.cluster_bootstrap_ci(clusters, resamples=10_000, alpha=2 * 0.0125)     # one-sided 0.0125 lower bound
    return {"mean": round(ci["mean"], 2), "lo": round(ci["lo"], 2), "hi": round(ci["hi"], 2), "lo_0125": round(ci1["lo"], 2),
            "clusters": ci["clusters"]}


def retention_8a(rows) -> dict:
    """Facts whose tool row is still in the final prompt but shortened: share whose line the shortened row still holds."""
    acc = defaultdict(lambda: [0, 0])
    for r, f in _facts(rows):
        if f["row_shortened"]:
            for k in ((r["arm"], r["window"], "all"), (r["arm"], r["window"], r["style"]), (r["arm"], r["window"], r["language"]),
                      (r["arm"], r["window"], "kind:" + f["kind"])):
                acc[k][0] += 1
                acc[k][1] += bool(f["held_in_shortened_row"])
    return {"|".join(map(str, k)): {"n": n, "held": round(100 * h / n, 1)} for k, (n, h) in acc.items()}


def scorecard(rows) -> dict:
    out = {}
    for arm in ARMS:
        for w in sorted({r["window"] for r in rows}):
            g = _g(rows, arm=arm, window=w)
            if g:
                out[f"{arm}|{w}"] = {k: round(statistics.fmean(r[k] for r in g), 3) for k in
                                     ("compactions", "prefix_breaks", "prune_commits", "shortened_rows", "failed_turns", "requests",
                                      "kept", "recoverable", "lost", "needed", "real_share", "steps")}
    return out


def family_table(priced_w, rows, window) -> dict:
    """Per family: weighted pooled effective_real change (1.10.0 vs 1.9.0, 1.10.0 vs stock) and facts kept per arm."""
    out = {}
    for fam in sorted({r["family"] for r in rows}):
        ids = {r["scenario"] for r in rows if r["family"] == fam}
        cell = {}
        for arm, base in COMPARISONS:
            d = 0.0
            for name, wgt in repricing.HERMES_WEIGHTS.items():
                pr = [r for r in priced_w[name] if r["scenario"] in ids]
                d += wgt * rr.pooled(pr, arm, base, "effective_real", window)
            cell[f"{arm}-{base}"] = round(d, 2)
        for arm in ARMS:
            g = [r for r in rows if r["family"] == fam and r["arm"] == arm and r["window"] == window]
            cell[f"kept|{arm}"] = round(100 * sum(r["kept"] for r in g) / max(1, sum(r["needed"] for r in g)), 1)
        out[fam] = cell
    return out


def analyse(rows, prices) -> dict:
    windows = sorted({r["window"] for r in rows})
    for r in rows:          # the ledger reproduces every row's prices at the replay's own price
        assert repricing.original_fields(r) == {k: r[k] for k in ("base", "effective", "effective_real", "effective_finder")}, r["scenario"]
    priced = {name: repricing.repriced_rows(rows, repricing.ORIGINAL if name == "original" else repricing.profile_pricing(name, prices))
              for name in PROFILES}
    # attach the strata to the priced rows (repriced_rows keeps only the stats keys)
    meta = {(r["arm"], r["scenario"], r["window"]): r for r in rows}
    for pr in priced.values():
        for p in pr:
            src = meta[(p["arm"], p["scenario"], p["window"])]
            p.update(family=src["family"], language=src["language"], style=src["style"], split=src["split"])
    out = {"windows": windows, "self_checks": rr.self_check(priced), "crossing": crossing(rows), "timing": timing(rows),
           "profiles": {}, "weighted": {}, "split": {}, "scorecard": scorecard(rows), "facts_delta": {}, "families": {},
           "strata_cost": {}}
    for name, pr in priced.items():
        for w in windows:
            for arm, base in COMPARISONS:
                for field in FIELDS:
                    out["profiles"][f"{name}|{w}|{arm}-{base}|{field}"] = rr.compare(pr, arm, base, field, w,
                                                                                    10_000 if field == "effective_real" else 2_000)
            for arm in ARMS:
                out["split"][f"{name}|{w}|{arm}"] = rr.means(pr, arm, w, ("read", "write", "summary_input", "output", "effective_real",
                                                                          "tier_requests"))
    by = {n: priced[n] for n in repricing.HERMES_WEIGHTS}
    for w in windows:
        for arm, base in COMPARISONS:
            for field in FIELDS:
                out["weighted"][f"{w}|{arm}-{base}|{field}"] = rr.weighted(by, repricing.HERMES_WEIGHTS, arm, base, field, w)
            out["facts_delta"][f"{w}|{arm}-{base}"] = facts_delta(rows, arm, base, w)
            for label, pick in (("lang", "language"), ("style", "style"), ("split", "split")):
                for value in sorted({r[pick] for r in rows}):
                    d = sum(wgt * rr.pooled([p for p in by[n] if p[pick] == value], arm, base, "effective_real", w)
                            for n, wgt in repricing.HERMES_WEIGHTS.items())
                    out["strata_cost"][f"{w}|{arm}-{base}|{label}={value}"] = round(d, 2)
        out["families"][str(w)] = family_table(by, rows, w)
    out["facts_by"] = {
        "all": fact_rates(rows, lambda r, f: "all"), "language": fact_rates(rows, lambda r, f: r["language"]),
        "style": fact_rates(rows, lambda r, f: r["style"]), "kind": fact_rates(rows, lambda r, f: f["kind"]),
        "position": fact_rates(rows, lambda r, f: f["position"]), "split": fact_rates(rows, lambda r, f: r["split"]),
        "value_present": {f"{a}|{w}": round(100 * statistics.fmean(f["value_present"] for r, f in _facts(_g(rows, arm=a, window=w))), 1)
                          for a in ARMS for w in windows if _g(rows, arm=a, window=w)},
    }
    out["questions"] = question_rates(rows)
    out["retention_8a"] = retention_8a(rows)
    return out


def matrix64(path) -> dict:
    """64K cache-break check on the development matrix's refactor and smallrows shapes (committed P1c rows; reproduced exactly)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [r for r in data["rows"] if r["window"] == 64000 and r["scenario"] in ("refactor", "smallrows")]
    out = {}
    for shape in ("refactor", "smallrows"):
        for arm in ARMS:
            g = [r for r in rows if r["scenario"] == shape and r["arm"] == arm]
            out[f"{shape}|{arm}"] = {k: round(statistics.fmean(r[k] for r in g), 2) for k in ("compactions", "prefix_breaks", "prune_commits", "kept", "needed")}
            out[f"{shape}|{arm}"]["n"] = len(g)
    priced = {n: repricing.repriced_rows(rows, repricing.profile_pricing(n, repricing.load_prices())) for n in repricing.HERMES_WEIGHTS}
    for arm, base in COMPARISONS:
        out[f"weighted|{arm}-{base}"] = rr.weighted(priced, repricing.HERMES_WEIGHTS, arm, base, "effective_real", 64000)
    return out


# ------------------------------------------------------------------------------------------------ markdown
def f(x) -> str:
    return f"{x['mean']:+.1f} [{x['lo']:+.1f}, {x['hi']:+.1f}]"


def k(w) -> str:
    return "1M" if w == 1_000_000 else f"{w // 1000}K"


def markdown(out, m64=None, elapsed=None) -> str:
    W = out["windows"]
    L = []
    hdr = "| " + " | ".join(k(w) for w in W) + " |"
    sep = "|---" * len(W) + "|"
    L.append("### Trigger crossing, stock arm (sessions with ≥1 / ≥2 compactions)\n")
    L.append("| Split |" + hdr[1:])
    L.append("|---" + sep)
    for split in ("dev", "validation", "all"):
        if any(f"{STOCK}|{w}|{split}" not in out["crossing"] for w in W):
            continue
        L.append(f"| {split} | " + " | ".join(
            f"{out['crossing'][f'{STOCK}|{w}|{split}']['ge1'] * 100:.0f}% / {out['crossing'][f'{STOCK}|{w}|{split}']['ge2'] * 100:.0f}%"
            f" (n {out['crossing'][f'{STOCK}|{w}|{split}']['n']})" for w in W) + " |")
    L.append("\n### Wall time per run (seconds: mean / median / p95 / max)\n")
    L.append("| Arm |" + hdr[1:])
    L.append("|---" + sep)
    for arm in ARMS:
        L.append(f"| {arm} | " + " | ".join(
            "{mean:.1f} / {median:.1f} / {p95:.1f} / {max:.1f}".format(**out["timing"][f"{arm}|{w}"]) for w in W) + " |")
    if elapsed:
        L.append("| elapsed per window, all 660 runs (15 workers) | " + " | ".join(f"{elapsed.get(w, '?')} s" for w in W) + " |")
    L.append("\n### Primary: registered weighted `effective_real` change % (Hermes 1/5 each, no expiry), cluster 95% interval by scenario\n")
    L.append("| Comparison | Field |" + hdr[1:])
    L.append("|---|---" + sep)
    for arm, base in COMPARISONS:
        for field in FIELDS:
            L.append(f"| {arm} vs {base} | {field} | " + " | ".join(
                f"{f(out['weighted'][f'{w}|{arm}-{base}|{field}'])} ({out['weighted'][f'{w}|{arm}-{base}|{field}']['pooled']:+.1f})"
                for w in W) + " |")
    for arm, base in COMPARISONS:
        L.append(f"\n### `effective_real`, {arm} vs {base}, per profile (mean change %, 95% interval; pooled D)\n")
        L.append("| Profile |" + hdr[1:])
        L.append("|---" + sep)
        for name in PROFILES:
            L.append(f"| {name} | " + " | ".join(
                f"{f(out['profiles'][f'{name}|{w}|{arm}-{base}|effective_real'])} ({out['profiles'][f'{name}|{w}|{arm}-{base}|effective_real']['pooled']:+.1f})"
                for w in W) + " |")
    L.append("\n### Cost split per answered turn (K chars in miss units): read / write / summary input / output\n")
    L.append("| Profile | Window | stock | 1.9.0 | 1.10.0 |")
    L.append("|---|---|---|---|---|")
    for name in PROFILES:
        for w in W:
            cells = []
            for arm in ARMS:
                s = out["split"][f"{name}|{w}|{arm}"]
                cells.append(f"{s['read'] / 1000:.0f} / {s['write'] / 1000:.0f} / {s['summary_input'] / 1000:.0f} / {s['output'] / 1000:.1f}"
                             + (f" (tier {s['tier_requests']:.0f})" if s["tier_requests"] else ""))
            L.append(f"| {name} | {k(w)} | " + " | ".join(cells) + " |")
    L.append("\n### Scorecard per session\n")
    L.append("| Window | Arm | steps | compactions | cache breaks | prune commits | shortened rows | failed turns | facts kept % | kept+recoverable % | value present % |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for w in W:
        for arm in ARMS:
            s = out["scorecard"][f"{arm}|{w}"]
            fa = out["facts_by"]["all"][f"{arm}|{w}|all"]
            L.append(f"| {k(w)} | {arm} | {s['steps']:.0f} | {s['compactions']:.2f} | {s['prefix_breaks']:.2f} | {s['prune_commits']:.2f} | "
                     f"{s['shortened_rows']:.1f} | {s['failed_turns']:.2f} | {fa['kept']:.1f} | {fa['held']:.1f} | {out['facts_by']['value_present'][f'{arm}|{w}']:.1f} |")
    L.append("\n### Facts kept, change in points per scenario (95% interval; one-sided 0.0125 lower bound)\n")
    L.append("| Comparison |" + hdr[1:])
    L.append("|---" + sep)
    for arm, base in COMPARISONS:
        L.append(f"| {arm} vs {base} | " + " | ".join(
            f"{f(out['facts_delta'][f'{w}|{arm}-{base}'])}; lb {out['facts_delta'][f'{w}|{arm}-{base}']['lo_0125']:+.1f}" for w in W) + " |")
    for title, key, values in (("language", "language", ("en", "zh", "mixed")), ("task style", "style", ("specific", "vague")),
                               ("fact kind", "kind", ("natural", "error", "buried", "real")), ("position", "position", ("early", "middle", "late")),
                               ("split", "split", ("dev", "validation"))):
        L.append(f"\n### Facts kept % by {title} (stock / 1.9.0 / 1.10.0)\n")
        L.append("| " + title + " |" + hdr[1:])
        L.append("|---" + sep)
        for v in values:
            cells = []
            for w in W:
                got = [out["facts_by"][key].get(f"{a}|{w}|{v}") for a in ARMS]
                cells.append(" / ".join(f"{g['kept']:.0f}" if g else "-" for g in got) + (f" (n {got[0]['n']})" if got[0] else ""))
            L.append(f"| {v} | " + " | ".join(cells) + " |")
    L.append("\n### Questions held % by stratum (stock / 1.9.0 / 1.10.0)\n")
    L.append("| Stratum |" + hdr[1:])
    L.append("|---" + sep)
    for st in ("single", "buried", "superseded", "aggregate", "real"):
        cells = []
        for w in W:
            got = [out["questions"].get(f"{a}|{w}|{st}") for a in ARMS]
            cells.append(" / ".join(f"{g['held']:.0f}" if g else "-" for g in got))
        L.append(f"| {st} | " + " | ".join(cells) + " |")
    L.append("\n### Weighted pooled `effective_real` change % by stratum (1.10.0 vs 1.9.0 / 1.10.0 vs stock)\n")
    L.append("| Stratum |" + hdr[1:])
    L.append("|---" + sep)
    for s in ("lang=en", "lang=zh", "lang=mixed", "style=specific", "style=vague", "split=dev", "split=validation"):
        if any(f"{w}|{NEW}-{OLD}|{s}" not in out["strata_cost"] for w in W):
            continue
        L.append(f"| {s} | " + " | ".join(f"{out['strata_cost'][f'{w}|{NEW}-{OLD}|{s}']:+.1f} / {out['strata_cost'][f'{w}|{NEW}-{STOCK}|{s}']:+.1f}"
                                          for w in W) + " |")
    L.append("\n### S5 8(a)-style retention: facts whose row is still in the final prompt but shortened, % whose line that row holds (n)\n")
    L.append("| Arm | Stratum |" + hdr[1:])
    L.append("|---|---" + sep)
    for arm in (OLD, NEW, STOCK):
        for st in ("all", "specific", "vague", "en", "zh", "mixed", "kind:natural", "kind:error", "kind:buried", "kind:real"):
            cells = [out["retention_8a"].get(f"{arm}|{w}|{st}") for w in W]
            if any(cells):
                L.append(f"| {arm} | {st} | " + " | ".join(f"{c['held']:.0f} ({c['n']})" if c else "-" for c in cells) + " |")
    for w in W:
        fams = out["families"][str(w)]
        L.append(f"\n### Per family at {k(w)}: weighted pooled `effective_real` change % and facts kept %\n")
        L.append("| Family | 1.10.0 vs 1.9.0 | 1.10.0 vs stock | 1.9.0 vs stock | kept stock / 1.9.0 / 1.10.0 |")
        L.append("|---|---|---|---|---|")
        for fam, c in fams.items():
            L.append(f"| {fam} | {c[f'{NEW}-{OLD}']:+.1f} | {c[f'{NEW}-{STOCK}']:+.1f} | {c[f'{OLD}-{STOCK}']:+.1f} | "
                     f"{c[f'kept|{STOCK}']:.0f} / {c[f'kept|{OLD}']:.0f} / {c[f'kept|{NEW}']:.0f} |")
    if m64:
        L.append("\n### 64K cache-break check, development matrix shapes (seeds 0-5; committed P1c rows, reproduced exactly)\n")
        L.append("| Shape | Arm | compactions | cache breaks | prune commits | facts kept / needed |")
        L.append("|---|---|---|---|---|---|")
        for shape in ("refactor", "smallrows"):
            for arm in ARMS:
                s = m64[f"{shape}|{arm}"]
                L.append(f"| {shape} | {arm} | {s['compactions']:.2f} | {s['prefix_breaks']:.2f} | {s['prune_commits']:.2f} | {s['kept']:.2f} / {s['needed']:.2f} |")
        L.append("\n64K weighted `effective_real` (both shapes): " + "; ".join(f"{a} vs {b} {f(m64[f'weighted|{a}-{b}'])}" for a, b in COMPARISONS) + ".")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rows", nargs="+")
    ap.add_argument("--matrix64")
    ap.add_argument("--elapsed")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    rows, errors = load(a.rows)
    print(f"{len(rows)} rows, {len(errors)} error rows")
    for e in errors[:10]:
        print("  error:", e["arm"], e["scenario"], e["window"], e["error"])
    keys = [(r["arm"], r["scenario"], r["window"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate rows"
    out = analyse(rows, repricing.load_prices())
    out["errors"] = len(errors)
    m64 = matrix64(a.matrix64) if a.matrix64 else None
    if m64:
        out["matrix64"] = m64
    elapsed = {}
    if a.elapsed:
        for line in Path(a.elapsed).read_text().splitlines():
            parts = line.split()
            if len(parts) >= 4:
                elapsed[int(parts[1])] = int(parts[3])
        out["elapsed"] = elapsed
    md = markdown(out, m64, elapsed)
    print(f"self-checks (arm vs itself, exactly zero): {out['self_checks']}")
    print(md)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if a.md:
        Path(a.md).write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
