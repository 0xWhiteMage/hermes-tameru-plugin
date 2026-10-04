"""The scripted 30-turn Hermes session replayed by ``replay.run_session``.

Story: checkout broke at 02:17 UTC; the user and agent trace it to a stock-reservation leak in
``src/orders/service.py``, fix it (read -> patch -> re-read), re-run tests, look at CI, logs, a CJK
console dump, an oversized journal (spilled to disk) and a skill, and finally ask a question that can
only be answered from facts planted in early tool output.

Everything is generated from ``seed`` with ``random.Random`` -> byte-identical sessions per seed.

Gold facts (``GoldFact``) are literal needles, tagged with the Factory probe categories:

* ``recall``       specific details buried in tool output (ids, counts, values);
* ``artifact``     files / functions / tests / paths the work touched;
* ``continuation`` what to do next;
* ``decision``     choices that were made and constraints that were set.

``needed=True`` marks the facts the final question depends on. Where a fact is stated matters: facts
in ``user`` messages survive Hermes' verbatim-user section, facts in ``assistant`` text only if a
summary keeps them, and ``tool`` facts only if the engine keeps or can recover that output.
"""
from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:  # ``fixtures`` lives next to ``eval``
    sys.path.insert(0, str(_TESTS_DIR))

from fixtures import hermes_payloads as hp

SYSTEM_PROMPT = (
    "You are Hermes, an autonomous software engineering agent working in the user's repository.\n"
    "Use the available tools (terminal, read_file, patch, search_files, skill_view) to inspect and change code.\n"
    "Prefer small, verifiable steps. Run tests after every change. Never invent file contents; read them first.\n"
    "When a tool result is large, summarise what matters for the task instead of repeating it.\n"
    "Ask the user before destructive operations. Keep answers short and concrete.\n"
) * 6  # ~2.7 KB, like a trimmed real prompt


@dataclass(frozen=True)
class ToolStep:
    name: str
    args: dict
    result: str
    key: str  # stable identity of the call (tool + args) for re-fetch accounting


@dataclass(frozen=True)
class Round:
    say: str
    steps: tuple[ToolStep, ...]


@dataclass(frozen=True)
class Turn:
    user: str
    rounds: tuple[Round, ...]
    final: str


@dataclass(frozen=True)
class GoldFact:
    id: str
    category: str  # recall | artifact | continuation | decision
    needle: str
    turn: int  # 1-based user turn in which the fact first appears
    source: str  # user | assistant | tool
    needed: bool  # required to answer the final question
    call_key: str | None = None  # the tool call that produced it (source == "tool")


@dataclass
class Scenario:
    turns: list[Turn]
    gold: list[GoldFact]
    error_lines: list[str]  # level-tagged ERROR/FATAL/CRITICAL lines of the large incident log
    log_turn: int
    final_question: str
    system_prompt: str = SYSTEM_PROMPT
    facts_by_id: dict[str, GoldFact] = field(default_factory=dict)


CATEGORIES = ("recall", "artifact", "continuation", "decision")
N_TURNS = 30

FINAL_QUESTION = (
    "Before I merge, remind me: which order failed first in the incident log, which test failed before the fix "
    "and how many tests failed, which deploy on main failed, what we decided about retries and about the lock, "
    "which of the two fix options we went with, which helper function the patch added, "
    "what the next steps are (branch name, where to run the integration suite), and where the journal dump was saved."
)


def build_scenario(seed: int = 0, turns: int = N_TURNS) -> Scenario:
    """Build the session script. ``turns`` (4..30) keeps the first ``turns - 1`` scripted turns and
    then the final-question turn; gold facts that first appear after the cut are dropped."""
    if not 4 <= turns <= N_TURNS:
        raise ValueError(f"turns must be between 4 and {N_TURNS}")
    rng = random.Random(seed)  # only for the spill file name; payloads use independent streams:
    sub = lambda tag: random.Random(f"{seed}:{tag}")

    scripted: list[Turn] = []
    gold: list[GoldFact] = []

    def step(name: str, args: dict, result: str, key: str | None = None) -> ToolStep:
        return ToolStep(name, args, result, key or f"{name}:{json.dumps(args, sort_keys=True)}")

    def rnd(say: str, *steps: ToolStep) -> Round:
        return Round(say, tuple(steps))

    def add_turn(user: str, rounds: list[Round], final: str) -> int:
        scripted.append(Turn(user, tuple(rounds), final))
        return len(scripted)

    def fact(fid: str, category: str, needle: str, turn: int, source: str, needed: bool, call_key: str | None = None):
        gold.append(GoldFact(fid, category, needle, turn, source, needed, call_key))

    # ---- shared payload texts ------------------------------------------------------------------
    service_old = hp.orders_service_source(sub("service"), patched=False)
    service_new = hp.orders_service_source(sub("service"), patched=True)
    models_src = hp.orders_models_source(sub("models"))
    tests_old = hp.orders_tests_source(sub("tests"), with_rollback_test=True)
    config_yaml = hp.settings_yaml(
        sub("config"),
        plant={0.30: "    max_retries: 7", 0.55: "  lease_expiry_skew_ms: 2300"},
    )
    config_cat = hp.cat_n(config_yaml)

    log1, error_lines = hp.server_log(
        sub("log1"),
        n_lines=400,
        plant={0.42: "2025-03-14T02:17:44.118Z INFO  orders.service first failed checkout order=ORD-90417 sku=SKU-48213 status=502"},
    )
    log2, _ = hp.server_log(sub("log2"), n_lines=450, start_hour=2, with_errors=False)

    cmd_config = "cat -n /srv/shop/config/settings.yaml"
    # ---- T1 -----------------------------------------------------------------------------------
    add_turn(
        "Checkout started failing around 02:17 UTC last night. The repo is at /srv/shop. "
        "First get oriented: show me the layout and where stock reservation lives.",
        [
            rnd("Let me look at the repository layout and recent history.",
                step("terminal", {"command": "ls -la /srv/shop && git -C /srv/shop log --oneline -5"},
                     hp.terminal_result(
                         "total 64\ndrwxr-xr-x 9 deploy deploy 4096 Mar 14 01:58 .\ndrwxr-xr-x 4 root root 4096 Mar  2 09:12 ..\n"
                         "-rw-r--r-- 1 deploy deploy  612 Mar 11 10:02 pyproject.toml\ndrwxr-xr-x 6 deploy deploy 4096 Mar 13 17:44 src\n"
                         "drwxr-xr-x 4 deploy deploy 4096 Mar 13 17:44 tests\ndrwxr-xr-x 2 deploy deploy 4096 Mar 12 08:30 config\n"
                         "e41b7a0 retry: add jitter to capture backoff\n9c02d11 inventory: bump client timeout\n"
                         "5aa31f9 orders: log reservation ids\n1d9c3b6 ledger: batch writes\n77e0a42 docs: runbook update\n"))),
            rnd("Now find where stock is reserved.",
                step("search_files", {"pattern": "def reserve_stock", "path": "/srv/shop"},
                     hp.search_files_result(hp.search_matches(sub("search1"), "reserve_stock", n=24)))),
        ],
        "Stock reservation lives in src/orders/service.py (reserve_stock). I'll read it next.",
    )
    # ---- T2 -----------------------------------------------------------------------------------
    add_turn(
        "Read the orders service.",
        [rnd("Reading the service module.",
             step("read_file", {"path": "src/orders/service.py"}, hp.read_file_result(service_old)))],
        "reserve_stock takes holds in a loop and only writes the ledger at the end; nothing is released if either step fails.",
    )
    # ---- T3 -----------------------------------------------------------------------------------
    t3_key = "terminal:sed -n '1,400p' /var/log/shop/app.log"
    t3 = add_turn(
        "Pull the app log for the incident window (02:10 onwards).",
        [rnd("Fetching the log slice.",
             step("terminal", {"command": "sed -n '1,400p' /var/log/shop/app.log"},
                  hp.terminal_result(log1), key=t3_key))],
        "The log shows an error burst starting around 02:17: reservation conflicts, ledger write errors and gateway timeouts. "
        "Working hypothesis H1: reserve_stock leaks holds when the ledger write fails.",
    )
    fact("R1", "recall", "ORD-90417", t3, "tool", True, t3_key)
    # ---- T4 -----------------------------------------------------------------------------------
    t4_key = f"terminal:{cmd_config}"
    t4 = add_turn(
        "What do our retry settings look like?",
        [rnd("Checking the service config.",
             step("terminal", {"command": cmd_config}, hp.terminal_result(config_cat), key=t4_key))],
        "Retries are jittered and capped; lease handling has its own skew setting.",
    )
    fact("R2", "recall", "lease_expiry_skew_ms: 2300", t4, "tool", False, t4_key)
    # ---- T5 -----------------------------------------------------------------------------------
    t5 = add_turn(
        "Does that explain the incident or is it the leak?",
        [],
        "Decision D-4: keep max_retries at 7. Raising it would only multiply the leaked holds; the leak in reserve_stock is the cause.",
    )
    fact("D1", "decision", "Decision D-4", t5, "assistant", True)
    # ---- T6 -----------------------------------------------------------------------------------
    t6 = add_turn(
        "Agreed. There are two fixes: option A is a distributed lock around reserve_stock, option B is a compensating "
        "release inside reserve_stock. Go with option B (compensating release) and keep the lock out of this PR. "
        "Once it's green, bump CHANGELOG.md and open the PR from branch fix/stock-rollback.",
        [rnd("I'll read the existing tests before changing anything.",
             step("read_file", {"path": "tests/test_orders.py"}, hp.read_file_result(tests_old)))],
        "I'll add a rollback test first and watch it fail.",
    )
    fact("D2", "decision", "compensating release", t6, "user", True)
    fact("D3", "decision", "keep the lock out of this PR", t6, "user", True)
    fact("C1", "continuation", "branch fix/stock-rollback", t6, "user", True)  # not the bare name: T22 repeats it in a command
    # ---- T7 -----------------------------------------------------------------------------------
    t7_key = "terminal:cd /srv/shop && pytest -q tests/"
    pytest_fail = hp.pytest_output(
        sub("pytest1"), failed=["test_reserve_stock_rollback", "test_place_order_releases_on_gateway_timeout"],
        n_passed=140, extra_log_lines=70,
    )
    t7 = add_turn(
        "Run the order tests.",
        [rnd("Running the suite.",
             step("terminal", {"command": "cd /srv/shop && pytest -q tests/"},
                  hp.terminal_result(pytest_fail, exit_code=1), key=t7_key))],
        "Two failures, both from holds that are never released.",
    )
    fact("R3", "recall", "2 failed, 140 passed", t7, "tool", True, t7_key)
    fact("A1", "artifact", "tests/test_orders.py::test_reserve_stock_rollback", t7, "tool", True, t7_key)
    # ---- T8 -----------------------------------------------------------------------------------
    patch_args = {"path": "src/orders/service.py", "old_string": hp.RESERVE_STOCK_BUGGY, "new_string": hp.RESERVE_STOCK_FIXED}
    diff_text = "\n".join(
        ["--- a/src/orders/service.py", "+++ b/src/orders/service.py"]
        + [f"-{l}" for l in hp.RESERVE_STOCK_BUGGY.splitlines()]
        + [f"+{l}" for l in hp.RESERVE_STOCK_FIXED.splitlines()]
    )
    t8 = add_turn(
        "Fix reserve_stock.",
        [rnd("Patching reserve_stock to release holds on failure.",
             step("patch", patch_args, hp.patch_result(diff_text, ["src/orders/service.py"])))],
        "Patched reserve_stock: any failure now rolls back the holds taken so far.",
    )
    fact("A2", "artifact", "release_holds", t8, "assistant", True)
    # ---- T9 -----------------------------------------------------------------------------------
    add_turn(
        "Re-read the file to confirm the patch.",
        [rnd("Re-reading the service module.",
             step("read_file", {"path": "src/orders/service.py"}, hp.read_file_result(service_new)))],
        "Confirmed: reserve_stock now wraps the reservation in try/except and releases holds before re-raising.",
    )
    # ---- T10 ----------------------------------------------------------------------------------
    add_turn(
        "Run the tests again.",
        [rnd("Re-running the suite.",
             step("terminal", {"command": "cd /srv/shop && pytest -q tests/"},
                  hp.terminal_result(hp.pytest_green_output(142), exit_code=0), key="terminal:cd /srv/shop && pytest -q tests/#2"))],
        "All green.",
    )
    # ---- T11 (identical repeat of T4) --------------------------------------------------------
    add_turn(
        "Show me the config again, I want to double check the retry numbers.",
        [rnd("Printing the config again.",
             step("terminal", {"command": cmd_config}, hp.terminal_result(config_cat), key=t4_key))],
        "Unchanged since the last look.",
    )
    # ---- T12 ----------------------------------------------------------------------------------
    t12_key = "terminal:git -C /srv/shop diff"
    t12 = add_turn(
        "Show the diff so far.",
        [rnd("Getting the working-tree diff.",
             step("terminal", {"command": "git -C /srv/shop diff"},
                  hp.terminal_result(hp.git_diff(sub("diff"), service_old=hp.RESERVE_STOCK_BUGGY, service_new=hp.RESERVE_STOCK_FIXED, n_extra_files=6)),
                  key=t12_key))],
        "The diff touches reserve_stock plus a few timeout tweaks in other modules.",
    )
    fact("R4", "recall", "3f9a1c2..8be41d7", t12, "tool", False, t12_key)
    # ---- T13 ----------------------------------------------------------------------------------
    t13_key = "terminal:gh api repos/shop/shop/actions/runs --paginate"
    t13 = add_turn(
        "Check CI: list the recent workflow runs and tell me which deploy failed.",
        [rnd("Querying the Actions API.",
             step("terminal", {"command": "gh api repos/shop/shop/actions/runs --paginate"},
                  hp.terminal_result(hp.gh_api_runs(sub("gh1"), 300, plant_index=212, plant_name="deploy-9d41")), key=t13_key))],
        "One deploy on main failed; I noted its run name.",
    )
    fact("R5", "recall", "deploy-9d41", t13, "tool", True, t13_key)
    # ---- T14 ----------------------------------------------------------------------------------
    add_turn(
        "Grep for every place that raises StockReservationConflict.",
        [rnd("Searching src and tests.",
             step("terminal", {"command": "rg -n StockReservationConflict src tests"},
                  hp.terminal_result(hp.grep_n_output(sub("grep"), "StockReservationConflict", n=300))))],
        "Found the raise sites and handlers; none of them release holds either.",
    )
    # ---- T15 ----------------------------------------------------------------------------------
    t15_key = "terminal:tail -n 400 /var/log/support/console.log"
    t15 = add_turn(
        "Support pasted a CJK log from the Osaka and Seoul consoles; scan it for the failing order.",
        [rnd("Tailing the console log.",
             step("terminal", {"command": "tail -n 400 /var/log/support/console.log"},
                  hp.terminal_result(hp.cjk_log(sub("cjk"), n_lines=400, plant={0.5: "2025-03-14 02:18:02 [错误] 库存预留失败: 订单 ORD-77031, SKU-51902 库存不足 (剩余=0, 需要=2)"}))
                  , key=t15_key))],
        "Same pattern in the console log: reservation failures followed by payment timeouts.",
    )
    fact("R6", "recall", "ORD-77031", t15, "tool", False, t15_key)
    # ---- T16 (persisted-output spill) --------------------------------------------------------
    spill_id = f"call_{rng.getrandbits(48):012x}"  # Hermes names the spill file after the tool_call id
    spill_path = f"/home/agent/.hermes/cache/spillover/{spill_id}.txt"
    t16_key = "terminal:journalctl -u 'shop-worker@*' --since '02:10' --until '02:40' --no-pager"
    t16 = add_turn(
        "Dump the journal for the worker units for the same window.",
        [rnd("Collecting the worker journal.",
             step("terminal", {"command": "journalctl -u 'shop-worker@*' --since '02:10' --until '02:40' --no-pager"},
                  hp.persisted_output(hp.journal_preview(sub("journal")), 187_402, spill_path), key=t16_key))],
        "The journal is large; Hermes saved it to disk and I'll page through it only if needed.",
    )
    fact("A3", "artifact", f"spillover/{spill_id}.txt", t16, "tool", True, t16_key)
    # ---- T17 ----------------------------------------------------------------------------------
    add_turn(
        "Load the debugging skill so we follow the house process.",
        [rnd("Loading the skill.",
             step("skill_view", {"name": "systematic-debugging"},
                  hp.skill_view_result("systematic-debugging", "Hypothesis-driven debugging workflow",
                                       hp.skill_body(sub("skill"), n_sections=18), tags=["debugging"])))],
        "Skill loaded; we're following hypothesis -> instrument -> fix -> regression test.",
    )
    # ---- T18 ----------------------------------------------------------------------------------
    add_turn(
        "Also read the models module and the HTTP layer on top of it.",
        [
            rnd("Reading models.py.",
                step("read_file", {"path": "src/orders/models.py"}, hp.read_file_result(models_src))),
            rnd("And the API module.",
                step("read_file", {"path": "src/orders/api.py"},
                     hp.read_file_result(hp.generic_module_source(sub("api"), "HTTP handlers for the orders API.", 24)))),
        ],
        "Order, Line and OrderStatus look fine and the handlers just delegate to the service; nothing to change there.",
    )
    # ---- T19 (read -> patch -> re-read on the test file) -------------------------------------
    new_test = (
        "\n\ndef test_capture_timeout_releases_holds(fake_inventory, fake_ledger):\n"
        "    order = make_order(lines=[(\"SKU-48213\", 1)])\n"
        "    with pytest.raises(PaymentGatewayTimeout):\n"
        "        place_order(order, fake_inventory, fake_ledger, FakeGateway(timeout=True))\n"
        "    assert fake_inventory.active_holds() == []\n"
    )
    anchor = (
        "    with pytest.raises(LedgerWriteError):\n"
        "        reserve_stock(order, fake_inventory, fake_ledger)\n"
        "    assert fake_inventory.active_holds() == []\n"
    )
    assert anchor in tests_old
    tests_new = tests_old.replace(anchor, anchor + new_test, 1)
    t19 = add_turn(
        "Add a regression test for the gateway-timeout path, then re-read the test file.",
        [
            rnd("Adding the test next to the rollback test.",
                step("patch", {"path": "tests/test_orders.py", "old_string": anchor, "new_string": anchor + new_test},
                     hp.patch_result("--- a/tests/test_orders.py\n+++ b/tests/test_orders.py\n" + "\n".join(f"+{l}" for l in new_test.strip("\n").splitlines()),
                                     ["tests/test_orders.py"]))),
            rnd("Re-reading the test file.",
                step("read_file", {"path": "tests/test_orders.py"}, hp.read_file_result(tests_new))),
        ],
        "Added the gateway-timeout regression test.",
    )
    fact("A4", "artifact", "test_capture_timeout_releases_holds", t19, "assistant", False)
    # ---- T20 ----------------------------------------------------------------------------------
    t20 = add_turn(
        "Run just the order tests.",
        [rnd("Running the order tests.",
             step("terminal", {"command": "cd /srv/shop && pytest -q tests/test_orders.py"},
                  hp.terminal_result(hp.pytest_green_output(18), exit_code=0)))],
        "Green. TODO(next): run the integration suite against staging-3 before opening the PR.",
    )
    fact("C2", "continuation", "suite against staging-3", t20, "assistant", True)  # "staging-3" alone shows up in gh api names
    # ---- T21 ----------------------------------------------------------------------------------
    add_turn(
        "Pull the second incident log window (02:40 onwards) so we can verify the burst stopped.",
        [rnd("Fetching the later slice.",
             step("terminal", {"command": "sed -n '401,850p' /var/log/shop/app.log"}, hp.terminal_result(log2)))],
        "The burst stopped by 02:41; the later window has no errors.",
    )
    # ---- T22 ----------------------------------------------------------------------------------
    add_turn(
        "List the CI runs for the PR branch too.",
        [rnd("Querying runs for the branch.",
             step("terminal", {"command": "gh api 'repos/shop/shop/actions/runs?branch=fix/stock-rollback&per_page=300'"},
                  hp.terminal_result(hp.gh_api_runs(sub("gh2"), 200))))],
        "The branch has runs but none for the new commit yet.",
    )
    # ---- T23 ----------------------------------------------------------------------------------
    add_turn(
        "Grep for timeout in the config directory, and read the inventory client since it owns the reserve call.",
        [
            rnd("Searching the config directory.",
                step("search_files", {"pattern": "timeout", "path": "/srv/shop/config"},
                     hp.search_files_result(hp.search_matches(sub("search2"), "timeout", n=30)))),
            rnd("Reading the inventory client.",
                step("read_file", {"path": "src/inventory/client.py"},
                     hp.read_file_result(hp.generic_module_source(sub("client"), "Inventory service client.", 26)))),
        ],
        "All the timeouts are in the per-service blocks and the client already raises StockReservationConflict cleanly.",
    )
    # ---- T24 ----------------------------------------------------------------------------------
    cl = sub("changelog")
    changelog_text = "# Changelog\n\n" + "\n".join(
        f"## 2025.03.{d:02d}\n- {cl.choice(['Fix', 'Add', 'Tune', 'Drop'])} "
        f"{cl.choice(['retry jitter', 'ledger batching', 'search ranking', 'session refresh', 'carrier mapping'])}\n"
        for d in range(14, 0, -1)
    )
    add_turn(
        "Read the changelog so I can bump it later, and show me how recent commits describe their changes.",
        [
            rnd("Reading CHANGELOG.md.",
                step("read_file", {"path": "CHANGELOG.md"}, hp.read_file_result(changelog_text))),
            rnd("And the recent history with stats.",
                step("terminal", {"command": "git -C /srv/shop log --stat -30"}, hp.terminal_result(hp.git_log_stat(sub("gitlog"), 30)))),
        ],
        "The changelog uses one dated section per release; commits are short imperative one-liners.",
    )
    # ---- T25 ----------------------------------------------------------------------------------
    add_turn(
        "Run the whole suite one more time.",
        [rnd("Running everything.",
             step("terminal", {"command": "cd /srv/shop && pytest -v tests/"},
                  hp.terminal_result(hp.pytest_verbose_output(sub("pytestv"), 143), exit_code=0), key="terminal:cd /srv/shop && pytest -q tests/#3"))],
        "143 tests pass.",
    )
    # ---- T26 ----------------------------------------------------------------------------------
    t26 = add_turn(
        "Show git status and the diff stat.",
        [
            rnd("Status first.", step("terminal", {"command": "git -C /srv/shop status --short"},
                                       hp.terminal_result(" M src/orders/service.py\n M tests/test_orders.py\n"))),
            rnd("Now the stat.", step("terminal", {"command": "git -C /srv/shop diff --stat"},
                                      hp.terminal_result(" src/orders/service.py | 14 +++++++++++---\n tests/test_orders.py  |  7 +++++++\n 2 files changed, 18 insertions(+), 3 deletions(-)\n"))),
        ],
        "Two files changed. Decision D-6: we squash-merge this PR.",
    )
    fact("D4", "decision", "Decision D-6", t26, "assistant", False)
    # ---- T27 (identical repeat of T9) --------------------------------------------------------
    add_turn(
        "Print the final version of the service file once more, I want to paste it in the PR.",
        [rnd("Re-reading the service module.",
             step("read_file", {"path": "src/orders/service.py"}, hp.read_file_result(service_new)))],
        "Same content as before the test run.",
    )
    # ---- T28 ----------------------------------------------------------------------------------
    t28 = add_turn(
        "One more thing: the PR title must be 'Release holds on failed reservations'.",
        [],
        "Noted.",
    )
    fact("C3", "continuation", "Release holds on failed reservations", t28, "user", False)
    # ---- T29 ----------------------------------------------------------------------------------
    add_turn(
        "I think we're done investigating.",
        [],
        "Agreed. I'll wait for your go-ahead before opening the PR.",
    )

    # ---- final-question turn -------------------------------------------------------------------
    keep = max(1, turns - 1)
    kept = scripted[:keep]
    kept_gold = [g for g in gold if g.turn <= keep]
    # Verify every needle really is in its source text (scenario drift guard).
    for g in kept_gold:
        t = scripted[g.turn - 1]
        if g.source == "user":
            hay = t.user
        elif g.source == "assistant":
            hay = t.final + "\n".join(r.say for r in t.rounds) + "\n".join(
                json.dumps(s.args) for r in t.rounds for s in r.steps)
        else:
            hay = "\n".join(s.result for r in t.rounds for s in r.steps)
        assert g.needle in hay or json.dumps(g.needle)[1:-1] in hay, f"gold {g.id} needle not in turn {g.turn} {g.source}"

    sc = Scenario(
        turns=kept,
        gold=kept_gold,
        error_lines=error_lines,
        log_turn=t3,
        final_question=FINAL_QUESTION,
    )
    sc.facts_by_id = {g.id: g for g in kept_gold}
    return sc
