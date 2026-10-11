"""Per-result comparison: each tool output demoted once, through stock Hermes and through the plugin.

    python tests/eval/per_result.py [--json] [--verbose]          # stub or real Hermes (HERMES_REPO_ROOT)

Two sets, the same ones ``docs/REPORT-1.4.0.md`` sections 5.2/5.3 describe (re-built here because the
1.4.0 report's scratch script was not checked in, so the numbers are close to, not identical with, the
report's):

* **agent outputs**: 13 outputs built with ``tests/fixtures/hermes_payloads.py`` (14 answer strings,
  error templates counted with ``replay.error_fingerprints``). The 650 KB log of the report needs upstream's
  generator and is replaced by a 650 KB ``server_log`` slice.
* **engine corpus**: ``tests/fixtures/engine_benchmark_cases.json`` (upstream's head-to-head corpus): plugin
  savings against the engine alone (``compress_context(ctx, q, ccr=False, citations=False)``).

Savings are characters of the row removed. Not collected by pytest.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(ROOT / "tests"), str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
if os.environ.get("HERMES_REPO_ROOT"):
    sys.path.insert(0, str(Path(os.environ["HERMES_REPO_ROOT"]).expanduser().resolve()))
    if os.environ.get("TAMERU_EVAL_KEEP_HERMES_HOME") != "1":
        os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="tameru-eval-home-")
else:
    sys.path.insert(0, str(ROOT / "tests" / "hermes_stub"))

from engine_support import Chat, demote, make_engine  # noqa: E402
from fixtures import hermes_payloads as hp  # noqa: E402
from replay import error_fingerprints  # noqa: E402

from hermes_tameru_plugin.engine import summarize_tool_result  # noqa: E402
from hermes_tameru_plugin.render import parse_header  # noqa: E402
from hermes_tameru_plugin.tameru import compress_context  # noqa: E402


def _commas_source(rng: random.Random) -> str:
    rows = [f"    ({i}, \"{rng.choice(hp._NOUNS)}\", {rng.randint(1, 99)}, {rng.random():.3f}, \"{hp._hex(rng, 6)}\")," for i in range(140)]
    body = "RATES = [\n" + "\n".join(rows[:70]) + "\n]\n\n"
    body += "def apply_discount(order, coupon):\n    \"\"\"Apply ``coupon`` to ``order``; caps at MAX_DISCOUNT_PCT.\"\"\"\n"
    body += "    pct = min(coupon.pct, MAX_DISCOUNT_PCT)  # MAX_DISCOUNT_PCT = 35\n    return order.total * (1 - pct / 100)\n\n"
    body += "TIERS = [\n" + "\n".join(rows[70:]) + "\n]\n"
    return body


def _saturated_grep(rng: random.Random) -> str:
    """``rg -n`` where every line matches the searched name and one line defines it."""
    uses = [
        "        raise StockReservationConflict(sku, expected=expected, actual=actual)",
        "    except StockReservationConflict as exc:",
        "        log.error(\"StockReservationConflict: %s\", exc)",
        "from shop.inventory.errors import StockReservationConflict",
        "        with pytest.raises(StockReservationConflict):",
        "    # TODO: surface StockReservationConflict to the caller instead of swallowing it",
    ]
    paths = [f"src/{pkg}/{mod}.py" for pkg in ("orders", "inventory", "ledger", "payments", "shared")
             for mod in ("service", "api", "client", "models")]
    paths += [f"tests/test_{x}.py" for x in ("orders", "inventory", "ledger", "payments", "api")]
    rows = [(p, rng.randint(5, 480), rng.choice(uses)) for p in paths for _ in range(rng.randint(4, 7))]
    rows.append(("src/inventory/errors.py", 12, "class StockReservationConflict(RuntimeError):"))
    rows.sort(key=lambda r: (r[0], r[1]))
    return "\n".join(f"{p}:{n}:{t}" for p, n, t in rows) + "\n"


def agent_outputs() -> list[dict]:
    """``{name, tool, args, content, q, answers}``; ``content`` is the stored Hermes result."""
    r = lambda s: random.Random(s)  # noqa: E731
    log, _ = hp.server_log(r(1), n_lines=450)
    big, _ = hp.server_log(r(2), n_lines=5600, error_extra=60)
    svc_old = hp.orders_service_source(r(3), patched=False)
    svc_new = hp.orders_service_source(r(3), patched=True)
    src = hp.orders_service_source(r(4), patched=False) + "\n\n" + hp.python_functions(r(4), 18)
    grep = _saturated_grep(r(5))
    yaml = hp.settings_yaml(r(6), plant={0.62: "  max_inflight_reservations: 48"})
    cjk = hp.cjk_log(r(7), plant={0.55: "2025-03-14 02:31:07 [错误] 网关超时: 订单 ORD-77031 (重试=3)"})
    gitlog = hp.git_log_stat(r(8), 30)
    gl = gitlog.splitlines()
    gitlog = "\n".join(gl[:61] + ["commit 5be1a7c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4",
                                  "Author: Lena Vogel <dev@shop.example>", "Date:   Thu Mar 13 18:02:00 2025 +0000", "",
                                  "    ledger: make writer idempotent on retry (fixes double capture)", ""] + gl[61:])
    runs = hp.gh_api_runs(r(13), 300, plant_index=211)
    run_id = json.loads(runs)[211]["id"]
    T = hp.terminal_result
    return [
        dict(name="app log, real question", tool="terminal", args={"command": "tail -n 450 /var/log/shop/app.log"},
             content=T(log), q="why did the payment gateway time out?", answers=[]),
        dict(name="app log, continue", tool="terminal", args={"command": "tail -n 450 /var/log/shop/app.log"},
             content=T(log), q="continue", answers=[]),
        dict(name="650 KB log", tool="terminal", args={"command": "cat /var/log/shop/app.log"},
             content=T(big), q="what errors happened overnight?", answers=[]),
        dict(name="cat -n source", tool="terminal", args={"command": "cat -n src/orders/service.py"},
             content=T(hp.cat_n(src)), q="where does reserve_stock release the holds on a ledger failure?",
             answers=["def reserve_stock(order, inventory, ledger):", 'ledger.write_entry(order.id, "reserve"']),
        dict(name="source with commas", tool="read_file", args={"path": "src/orders/pricing.py"},
             content=hp.read_file_result(_commas_source(r(9))), q="what caps the coupon discount?",
             answers=["def apply_discount(order, coupon):", "MAX_DISCOUNT_PCT = 35"]),
        dict(name="git diff", tool="terminal", args={"command": "git diff"},
             content=T(hp.git_diff(r(10), service_old=svc_old, service_new=svc_new)),
             q="what changed in reserve_stock?", answers=["3f9a1c2..8be41d7", "src/orders/service.py"]),
        dict(name="rg -n", tool="terminal", args={"command": "rg -n StockReservationConflict src tests"},
             content=T(grep), q="where is StockReservationConflict defined?",
             answers=[("src/inventory/errors.py", "12", "class StockReservationConflict(RuntimeError):")]),
        dict(name="pytest failure", tool="terminal", args={"command": "pytest -q tests/"},
             content=T(hp.pytest_output(r(11), failed=["test_reserve_stock_rollback"], n_passed=140), exit_code=1),
             q="why does test_reserve_stock_rollback fail?",
             answers=["tests/test_orders.py::test_reserve_stock_rollback", "E       AssertionError: assert [Hold(id='h-1'"]),
        dict(name="pytest -v, all green", tool="terminal", args={"command": "pytest -v"},
             content=T(hp.pytest_verbose_output(r(12), 400)), q="did the test suite pass?", answers=["400 passed"]),
        dict(name="JSON API dump", tool="terminal", args={"command": "gh api repos/shop/orders/actions/runs"},
             content=T(runs), q=f"what was workflow run {run_id} called, and did it pass?", answers=["deploy-9d41"]),
        dict(name="YAML config", tool="read_file", args={"path": "config/settings.yaml"},
             content=hp.read_file_result(yaml), q="what is max_inflight_reservations set to?", answers=["max_inflight_reservations: 48"]),
        dict(name="CJK log", tool="terminal", args={"command": "cat support.log"},
             content=T(cjk), q="网关超时 which order?", answers=["ORD-77031"]),
        dict(name="git log --stat", tool="terminal", args={"command": "git log --stat -n 31"},
             content=T(gitlog), q="which commit made the ledger writer idempotent?",
             answers=["ledger: make writer idempotent on retry"]),
    ]


def _has(row: str, answer: str | tuple[str, ...]) -> bool:
    """An answer is a string, or a tuple of parts that must all be kept (a grep hit regrouped by file)."""
    return all(part in row for part in ((answer,) if isinstance(answer, str) else answer))


def _inner(content: str) -> str:
    try:
        obj = json.loads(content)
    except ValueError:
        return content
    return obj.get("output") or obj.get("content") or content if isinstance(obj, dict) else content


def _through(tool: str, args: dict, content: str, q: str) -> tuple[str, str, str]:
    """(plugin row, rung, stock row)."""
    chat = Chat(task=q)
    idx = chat.tool(tool, args, content)
    msgs = chat.finish()
    _, out = demote(make_engine(), msgs, idx)
    row = out[idx]["content"]
    stock = summarize_tool_result(tool, json.dumps(args), content)
    return row, (parse_header(row) or {}).get("rung", "none"), stock


def run(verbose: bool = False) -> dict:
    res: dict = {"agent": [], "corpus": []}
    for case in agent_outputs():
        row, rung, stock = _through(case["tool"], case["args"], case["content"], case["q"])
        fp = error_fingerprints(_inner(case["content"]))
        kept_fp = error_fingerprints(row)
        res["agent"].append({
            "name": case["name"], "size": len(case["content"]), "rung": rung,
            "saved": round(100 * (1 - len(row) / len(case["content"])), 1),
            "answers": sum(_has(row, a) for a in case["answers"]), "answers_total": len(case["answers"]),
            "lost": [a for a in case["answers"] if not _has(row, a)],
            "errs": len(fp.keys() & kept_fp.keys()), "errs_total": len(fp),
            "stock_saved": round(100 * (1 - len(stock) / len(case["content"])), 1),
        })
        if verbose:
            print(f"==== {case['name']} ({rung})\n{row}\n")
    cases = json.loads((ROOT / "tests" / "fixtures" / "engine_benchmark_cases.json").read_text("utf-8"))
    from hermes_tameru_plugin.config import TameruSettings
    for c in cases:
        if len(c["ctx"]) < TameruSettings().min_tool_chars:
            continue
        chat = Chat(task=c["q"])
        idx = chat.tool("terminal", {"command": "cat report.txt"}, c["ctx"])
        _, out = demote(make_engine(), chat.finish(), idx)
        row = out[idx]["content"]
        alone = compress_context(c["ctx"], c["q"], ccr=False, citations=False).compressed_text
        res["corpus"].append({
            "name": c["name"], "rung": (parse_header(row) or {}).get("rung", "none"),
            "plugin_saved": round(100 * (1 - len(row) / len(c["ctx"])), 1),
            "engine_saved": round(100 * (1 - len(alone) / len(c["ctx"])), 1),
            "gold": sum(g in row for g in c["gold"]), "gold_total": len(c["gold"]),
            "leaked": sum(f in row for f in c["forbid"]),
        })
    return res


def summary(res: dict) -> str:
    a, c = res["agent"], res["corpus"]
    out = ["agent outputs (plugin, one demotion each)",
           f"{'output':<24}{'size':>9}  {'rung':<8}{'saved':>7}  answers  errs"]
    for x in a:
        out.append(f"{x['name']:<24}{x['size']:>9,}  {x['rung']:<8}{x['saved']:>6}%  {x['answers']}/{x['answers_total']:<6} "
                   f"{x['errs']}/{x['errs_total']}" + (f"  lost {x['lost']}" if x["lost"] else ""))
    out.append(f"{'TOTAL':<24}{'':>9}  {'':<8}{sum(x['saved'] for x in a) / len(a):>6.1f}%  "
               f"{sum(x['answers'] for x in a)}/{sum(x['answers_total'] for x in a):<6} "
               f"{sum(x['errs'] for x in a)}/{sum(x['errs_total'] for x in a)}")
    out += ["", "engine corpus (plugin vs engine alone)", f"{'case':<22}{'rung':<9}{'plugin':>8}{'engine':>8}  gold  leaked"]
    for x in c:
        out.append(f"{x['name']:<22}{x['rung']:<9}{x['plugin_saved']:>7}%{x['engine_saved']:>7}%  {x['gold']}/{x['gold_total']}   {x['leaked']}")
    out.append(f"{'MEAN':<22}{'':<9}{sum(x['plugin_saved'] for x in c) / len(c):>7.1f}%{sum(x['engine_saved'] for x in c) / len(c):>7.1f}%  "
               f"{sum(x['gold'] for x in c)}/{sum(x['gold_total'] for x in c)}   {sum(x['leaked'] for x in c)}")
    return "\n".join(out)


if __name__ == "__main__":
    result = run(verbose="--verbose" in sys.argv)
    print(json.dumps(result, indent=1) if "--json" in sys.argv else summary(result))
