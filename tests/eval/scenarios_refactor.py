"""The ``refactor`` replay scenario: a code session made of read -> patch -> re-read cycles.

35 turns in a small shop repository (seven source files of 9-18 KB): rename ``OrderStatus.PAID`` to ``SETTLED``
everywhere, then move the pricing block out of ``orders/service.py`` into a new module. Every file is read,
patched and read again, ``search_files`` / ``rg -n`` are re-run as the rename advances (each answer shrinks) and
``pytest`` is run again and again, so nearly every old result is superseded by a newer one of the same call or the
same path. All file contents come from one ``VirtualRepo``: a re-read really shows the edits, a grep really finds
what is left, the final ``git diff`` is the real diff of the session.

Only the *latest* read of a file is current; facts that live only in an older read of it (the cap comment that the
extraction replaces, the failing test names of the first red run) are what supersession puts at risk.
"""
from __future__ import annotations

import re

import payloads_extra as pe
from fixtures import hermes_payloads as hp
from scenario import Scenario
from scenario_kit import Kit, ScenarioSpec

REFACTOR_TURNS = 35

REFACTOR_FINAL_QUESTION = (
    "Write the PR description. I need: the ticket id, the audit table the status writes go to, the partner ticket "
    "behind the legacy paid webhook, the comment that justified the old discount cap, which tests failed on the "
    "first red run, how many tests pass at the end, which new module and which new test file hold the pricing "
    "code, which lint finding we cleaned up, what we decided about the stored value, the PAID alias and the new "
    "cap, how we merge, the branch and the PR title, and what has to run before merging."
)

_REPO = "/srv/shop"
_SERVICE = "src/shop/orders/service.py"
_MODELS = "src/shop/orders/models.py"
_API = "src/shop/orders/api.py"
_CART = "src/shop/cart.py"
_CHECKOUT = "src/shop/checkout.py"
_REPORTS = "src/shop/reports.py"
_TESTS = "tests/test_orders.py"
_ENGINE = "src/shop/pricing/engine.py"
_PRICING_TESTS = "tests/test_pricing.py"

_AUDIT_COMMENT = "# every status write is mirrored into the audit table order_status_audit_v3 (see ledger/audit.py)\n"
_WEBHOOK_LINE = (
    'LEGACY_PARTNER_WEBHOOK = "https://hooks.shop.example/orders/paid/v1"  '
    "# SHOP-4102: the partner still expects status=paid\n"
)

_CAP_IMPORT = "from shop.orders.service import DISCOUNT_TIER_CAP"

_NAMED_TESTS = '''

def test_settled_order_cannot_be_cancelled_twice(order):
    order.status = OrderStatus.PAID
    with pytest.raises(InvalidTransition):
        move_return(move_return(order, OrderStatus.CANCELLED), OrderStatus.CANCELLED)


def test_settlement_stamps_updated_at(order):
    order.status = OrderStatus.RESERVED
    settled = move_invoice(order, OrderStatus.PAID)
    assert settled.updated_at is not None and settled.status is OrderStatus.PAID


def test_checkout_totals_use_the_discount_cap(order):
    order.status = OrderStatus.NEW
    assert move_quote(order, OrderStatus.RESERVED).status is OrderStatus.RESERVED
'''

_NAMED_FAILURES = (
    ("tests/test_orders.py::test_settlement_stamps_updated_at", "AttributeError: PAID", "settled = move_invoice(order, OrderStatus.PAID)"),
    ("tests/test_orders.py::test_settled_order_cannot_be_cancelled_twice", "AttributeError: PAID", "order.status = OrderStatus.PAID"),
)


def _failing(tests_src: str, n: int) -> tuple[tuple[str, str, str], ...]:
    """The two named tests plus the first ``n`` generated tests whose body names ``OrderStatus.PAID``."""
    found = re.findall(r"def (test_\w+)\(order\):\n    order\.status = OrderStatus\.PAID\n", tests_src)
    return _NAMED_FAILURES + tuple(
        (f"tests/test_orders.py::{name}", "AttributeError: PAID", "order.status = OrderStatus.PAID") for name in found[:n]
    )


def _build_repo(rng_for) -> tuple[pe.VirtualRepo, dict[str, str]]:
    files = pe.refactor_files(rng_for)
    files[_MODELS] = files[_MODELS].replace(
        'log = logging.getLogger("shop.orders.models")\n', 'log = logging.getLogger("shop.orders.models")\n' + _AUDIT_COMMENT, 1)
    anchor = "log = logging.getLogger(__name__)\n"
    assert anchor in files[_REPORTS]
    files[_REPORTS] = files[_REPORTS].replace(anchor, anchor + "\n" + _WEBHOOK_LINE, 1)
    imp = "from tests.fakes import make_order\n"
    assert imp in files[_TESTS]
    files[_TESTS] = files[_TESTS].replace(imp, imp + _CAP_IMPORT + "\n", 1).rstrip("\n") + "\n" + _NAMED_TESTS
    return pe.VirtualRepo(files), dict(files)


def build_refactor(seed: int = 0, turns: int = REFACTOR_TURNS) -> Scenario:
    """Rename ``PAID`` to ``SETTLED`` across seven files, then extract the pricing module."""
    kit = Kit("refactor", seed)
    step, rnd, tool = kit.step, kit.rnd, hp.terminal_result
    repo, original = _build_repo(kit.rng)

    def read(path: str, nth: int):
        return step("read_file", {"path": path}, hp.read_file_result(repo.read(path)), nth=nth)

    def patch(path: str, old: str, new: str, *, replace_all: bool = False):
        diff, _ = repo.patch(path, old, new, replace_all=replace_all)
        args = {"path": path, "old_string": old, "new_string": new}
        if replace_all:
            args["replace_all"] = True
        return step("patch", args, hp.patch_result(diff, [path]))

    def rename_all(path: str):
        return patch(path, "OrderStatus.PAID", "OrderStatus.SETTLED", replace_all=True)

    def rg_cmd(token: str) -> str:
        return f"rg -n {token} src tests"

    def rg(token: str, nth: int):
        rows = repo.grep(token, "src", "tests")
        text = "".join(f"{p}:{n}:{line}\n" for p, n, line in rows)
        return step("terminal", {"command": rg_cmd(token)}, tool(text, exit_code=0 if rows else 1), nth=nth)

    def search(token: str, nth: int):
        rows = repo.grep(token, "src", "tests")
        return step("search_files", {"pattern": token, "path": _REPO}, hp.search_files_result(rows), nth=nth)

    pytest_cmd = f"cd {_REPO} && pytest -q tests/"
    failing = _failing(original[_TESTS], 6)
    assert len(failing) == 8, failing

    # ---- T1: the task, the first decisions, where PAID lives -------------------------------------
    t1 = kit.turn(
        "Ticket SHOP-4410: rename OrderStatus.PAID to SETTLED everywhere and move the pricing code out of "
        "src/shop/orders/service.py into a new pricing module. There is no data migration in this PR, so the "
        "stored value stays 'paid'. Do not keep a PAID alias: callers move to SETTLED in this PR. "
        "Open the PR from branch refactor/settled-status once the suite is green. The repo is at /srv/shop. "
        "First find every use of PAID.",
        [
            rnd("Searching for the enum member.", search("OrderStatus.PAID", 1)),
            rnd("And the plain word, to catch strings and comments.", rg("PAID", 1)),
        ],
        "PAID is used in the models, the service, the API, the cart, checkout, the reports and the tests; the stored value "
        "is the string 'paid'. I will go file by file, models first.",
    )
    kit.fact("R1", "recall", "SHOP-4410", t1, "user", True)
    kit.fact("D1", "decision", "There is no data migration in this PR", t1, "user", True)
    kit.fact("D2", "decision", "Do not keep a PAID alias", t1, "user", True)
    kit.fact("C1", "continuation", "branch refactor/settled-status", t1, "user", True)
    # ---- T2 ---------------------------------------------------------------------------------------
    kit.turn("Read the models.", [rnd("Reading models.py.", read(_MODELS, 1))],
             "OrderStatus is defined here with PAID = \"paid\"; the status helpers below it use OrderStatus.PAID in many places.")
    # ---- T3: patch + re-read ----------------------------------------------------------------------
    t3 = kit.turn(
        "Rename the member and every use in models.py, then read it again to check.",
        [
            rnd("Renaming the enum member.", patch(_MODELS, '    PAID = "paid"\n', '    SETTLED = "paid"\n')),
            rnd("And its uses in this module.", rename_all(_MODELS)),
            rnd("Re-reading models.py.", read(_MODELS, 2)),
        ],
        "models.py is done: the member is SETTLED with the same stored value.",
    )
    kit.fact("R2", "recall", "order_status_audit_v3", t3, "tool", True, "read_file:" + _MODELS + "#2")
    # ---- T4: service ------------------------------------------------------------------------------
    t4 = kit.turn("Now the service.", [rnd("Reading service.py.", read(_SERVICE, 1))],
                  "service.py holds the pricing block (subtotal, discount tiers, tax) plus the status helpers.")
    kit.fact("R3", "recall", "SHOP-3917", t4, "tool", True, "read_file:" + _SERVICE + "#1")
    # ---- T5: first red run ------------------------------------------------------------------------
    text1, errs1 = pe.pytest_run(kit.rng("pytest1"), failed=failing, passed=206, seconds=9.31, log_lines=6)
    t5 = kit.turn(
        "Run the tests so we see what the rename broke so far.",
        [rnd("Running the suite.", step("terminal", {"command": pytest_cmd}, tool(text1, exit_code=1), nth=1))],
        "Eight tests fail with AttributeError: PAID, as expected: the service and the test file still use the old name.",
    )
    kit.errors(t5, errs1)
    kit.fact("A1", "artifact", "test_settlement_stamps_updated_at", t5, "tool", True, "terminal:" + pytest_cmd + "#1")
    kit.fact("R4", "recall", "8 failed, 206 passed in 9.31s", t5, "tool", True, "terminal:" + pytest_cmd + "#1")
    # ---- T6 ---------------------------------------------------------------------------------------
    kit.turn(
        "Fix the service and read it again.",
        [rnd("Renaming in service.py.", rename_all(_SERVICE)), rnd("Re-reading service.py.", read(_SERVICE, 2))],
        "service.py uses SETTLED now.",
    )
    # ---- T7 ---------------------------------------------------------------------------------------
    kit.turn("What is left?", [rnd("Grepping again.", rg("OrderStatus.PAID", 2))],
             "api.py, cart.py, checkout.py, reports.py and the tests still use OrderStatus.PAID.")
    # ---- T8-T9: api -------------------------------------------------------------------------------
    kit.turn("Read the API module.", [rnd("Reading api.py.", read(_API, 1))], "The handlers compare against OrderStatus.PAID in several places.")
    kit.turn("Rename there and read it again.",
             [rnd("Renaming in api.py.", rename_all(_API)), rnd("Re-reading api.py.", read(_API, 2))], "api.py is done.")
    # ---- T10-T11: cart ----------------------------------------------------------------------------
    kit.turn("Same for the cart.", [rnd("Reading cart.py.", read(_CART, 1))], "The cart has its own status helpers.")
    kit.turn("Rename and re-read.",
             [rnd("Renaming in cart.py.", rename_all(_CART)), rnd("Re-reading cart.py.", read(_CART, 2))], "cart.py is done.")
    # ---- T12: second red run ------------------------------------------------------------------------
    text2, errs2 = pe.pytest_run(kit.rng("pytest2"), failed=failing[:3], passed=211, seconds=9.28, log_lines=3)
    t12 = kit.turn(
        "Run the suite again.",
        [rnd("Running the suite.", step("terminal", {"command": pytest_cmd}, tool(text2, exit_code=1), nth=2))],
        "Three failures left, all in the test file.",
    )
    kit.errors(t12, errs2)
    # ---- T13-T14: checkout --------------------------------------------------------------------------
    kit.turn("Checkout next.", [rnd("Reading checkout.py.", read(_CHECKOUT, 1))], "checkout.py reserves stock and then reads the status.")
    kit.turn("Rename and re-read.",
             [rnd("Renaming in checkout.py.", rename_all(_CHECKOUT)), rnd("Re-reading checkout.py.", read(_CHECKOUT, 2))],
             "checkout.py is done.")
    # ---- T15-T16: reports ----------------------------------------------------------------------------
    kit.turn("And the reports module.", [rnd("Reading reports.py.", read(_REPORTS, 1))],
             "The revenue query builds its status list from OrderStatus.PAID.value and OrderStatus.FULFILLED.value.")
    t16 = kit.turn("Rename and re-read.",
                   [rnd("Renaming in reports.py.", rename_all(_REPORTS)), rnd("Re-reading reports.py.", read(_REPORTS, 2))],
                   "reports.py is done; the SQL still compares against the stored value 'paid'.")
    kit.fact("R5", "recall", "SHOP-4102", t16, "tool", True, "read_file:" + _REPORTS + "#2")
    # ---- T17: what is left -----------------------------------------------------------------------------
    kit.turn("Anything left outside the tests?",
             [rnd("Searching again.", search("OrderStatus.PAID", 2)), rnd("And the plain word.", rg("PAID", 2))],
             "Only tests/test_orders.py is left, plus the stored string.")
    # ---- T18-T19: the test file -------------------------------------------------------------------------
    kit.turn("Read the tests.", [rnd("Reading test_orders.py.", read(_TESTS, 1))], "The tests build orders in every status.")
    kit.turn("Rename there and re-read.",
             [rnd("Renaming in the tests.", rename_all(_TESTS)), rnd("Re-reading test_orders.py.", read(_TESTS, 2))],
             "The tests use SETTLED now.")
    # ---- T20: green -------------------------------------------------------------------------------------
    text3, _ = pe.pytest_run(kit.rng("pytest3"), passed=214, seconds=9.12)
    kit.turn("Run everything.",
             [rnd("Running the suite.", step("terminal", {"command": pytest_cmd}, tool(text3), nth=3))],
             "The suite is green with the rename.")
    # ---- T21: the pricing decision ----------------------------------------------------------------------
    t21 = kit.turn(
        "Good. Now the pricing move. Marketing signed off a lower cap, so lower the discount cap to 0.30 in the new "
        "module. Squash-merge this PR when it is approved, and the PR title is 'Rename PAID to SETTLED and extract pricing'. "
        "Show me where the pricing functions are used first.",
        [rnd("Searching for the pricing entry points.", search("compute_subtotal", 1)),
         rnd("And the callers.", rg("apply_discount_tiers", 1))],
        "Decision D-14: the new module starts from the same code and only the cap changes. TODO(next): run the nightly job "
        "integration-eu-2 before merging.",
    )
    kit.fact("D3", "decision", "lower the discount cap to 0.30", t21, "user", True)
    kit.fact("D4", "decision", "Squash-merge this PR", t21, "user", True)
    kit.fact("C2", "continuation", "Rename PAID to SETTLED and extract pricing", t21, "user", True)
    kit.fact("C3", "continuation", "nightly job integration-eu-2", t21, "assistant", True)
    # ---- T22: write the engine ---------------------------------------------------------------------------
    engine_src = pe.engine_module()
    t22 = kit.turn(
        "Create the module.",
        [rnd("Writing src/shop/pricing/engine.py.",
             step("write_file", {"path": _ENGINE, "content": engine_src}, pe.write_file_result(engine_src, dirs_created=True)))],
        "src/shop/pricing/engine.py has the subtotal, discount tier and tax functions with the 0.30 cap.",
    )
    repo.write(_ENGINE, engine_src)
    kit.fact("A2", "artifact", "src/shop/pricing/engine.py", t22, "assistant", True)
    # ---- T23: cut the block out of the service ------------------------------------------------------------
    block = pe.PRICING_HEADER_CONSTANTS + "\n\n" + pe.PRICING_FUNCTIONS
    new_import = "from shop.pricing.engine import apply_discount_tiers, compute_subtotal, tax_for_region  # noqa: F401 (re-export)\n"
    kit.turn(
        "Take the block out of the service and keep the old names importable from there.",
        [rnd("Replacing the block by an import.", patch(_SERVICE, block, new_import)),
         rnd("Re-reading service.py.", read(_SERVICE, 3))],
        "service.py re-exports the three functions from the new module.",
    )
    # ---- T24: collection errors --------------------------------------------------------------------------------
    text4, errs4 = pe.pytest_collect_errors(
        [(_TESTS, 6)], "DISCOUNT_TIER_CAP", _CAP_IMPORT, module="shop.orders.service")
    t24 = kit.turn(
        "Run the suite.",
        [rnd("Running the suite.", step("terminal", {"command": pytest_cmd}, tool(text4, exit_code=2), nth=4))],
        "The tests import the cap constant from the service; it moved with the block.",
    )
    kit.errors(t24, errs4)
    # ---- T25: fix the importers -------------------------------------------------------------------------------------
    kit.turn(
        "Fix the imports in the tests and in checkout.",
        [rnd("Importing the cap from the new module in the tests.",
             patch(_TESTS, _CAP_IMPORT, "from shop.pricing.engine import DISCOUNT_TIER_CAP")),
         rnd("Checking the other importers.", rg("shop.orders.service", 1))],
        "Only the test module imported the cap from the service module.",
    )
    # ---- T26: pricing tests --------------------------------------------------------------------------------------------
    pricing_tests = pe.pricing_tests()
    t26 = kit.turn(
        "Add tests for the new module.",
        [rnd("Writing tests/test_pricing.py.",
             step("write_file", {"path": _PRICING_TESTS, "content": pricing_tests}, pe.write_file_result(pricing_tests, dirs_created=False)))],
        "tests/test_pricing.py covers the subtotal, the cap, the tax lookup and the rounding.",
    )
    repo.write(_PRICING_TESTS, pricing_tests)
    kit.fact("A3", "artifact", "tests/test_pricing.py", t26, "assistant", True)
    # ---- T27: ruff ----------------------------------------------------------------------------------------------------------
    findings = [(_SERVICE, 5, "ROUND_HALF_UP"), (_SERVICE, 8, "Sequence"), (_SERVICE, 9, "Tier"),
                (_CART, 7, "Iterable"), (_REPORTS, 6, "ROUND_HALF_UP"), (_CHECKOUT, 7, "Sequence"), (_API, 8, "Iterable")]
    t27 = kit.turn(
        "Run ruff.",
        [rnd("Linting.", step("terminal", {"command": f"cd {_REPO} && ruff check src tests"}, tool(pe.ruff_output(findings), exit_code=1)))],
        "Seven unused imports, most of them in service.py after the move.",
    )
    kit.fact("A4", "artifact", "`Tier` imported but unused", t27, "tool", False, f"terminal:cd {_REPO} && ruff check src tests")
    # ---- T28: clean the service imports ------------------------------------------------------------------------------------------
    kit.turn(
        "Clean the service imports and read it again.",
        [rnd("Dropping the unused imports.",
             patch(_SERVICE, "from decimal import ROUND_HALF_UP, Decimal\n", "from decimal import Decimal\n")),
         rnd("And the Tier import.", patch(_SERVICE, "from shop.orders.tiers import Tier\n", "")),
         rnd("Re-reading service.py.", read(_SERVICE, 4))],
        "service.py has no unused imports left.",
    )
    # ---- T29: final green ------------------------------------------------------------------------------------------------------------
    text5, _ = pe.pytest_run(kit.rng("pytest5"), passed=220, seconds=10.07)
    t29 = kit.turn(
        "Run everything one more time.",
        [rnd("Running the suite.", step("terminal", {"command": pytest_cmd}, tool(text5), nth=5))],
        "Everything passes with the pricing move.",
    )
    kit.fact("R6", "recall", "220 passed in 10.07s", t29, "tool", True, "terminal:" + pytest_cmd + "#5")
    # ---- T30: status and stat -----------------------------------------------------------------------------------------------------------
    changed = sorted(p for p in repo.files if repo.files[p] != original.get(p))
    stat_rows = []
    for p in changed:
        old, new = original.get(p, ""), repo.files[p]
        ins = sum(1 for l in pe.unified_diff(p, old, new).splitlines() if l.startswith("+") and not l.startswith("+++"))
        dele = sum(1 for l in pe.unified_diff(p, old, new).splitlines() if l.startswith("-") and not l.startswith("---"))
        stat_rows.append(f" {p:<34} | {ins + dele:>3} {'+' * min(ins, 30)}{'-' * min(dele, 30)}")
    kit.turn(
        "Show git status and the stat.",
        [rnd("Status.", step("terminal", {"command": f"git -C {_REPO} status --short"},
                             tool("".join(f" M {p}\n" if p in original else f"?? {p}\n" for p in changed)))),
         rnd("Stat.", step("terminal", {"command": f"git -C {_REPO} diff --stat"},
                           tool("\n".join(stat_rows) + f"\n {len(changed)} files changed\n")))],
        f"{len(changed)} files touched.",
    )
    # ---- T31: the diff ---------------------------------------------------------------------------------------------------------------------------
    diff = "\n".join(
        f"diff --git a/{p} b/{p}\nindex {pe.hex_(kit.rng('idx-' + p), 7)}..{pe.hex_(kit.rng('idy-' + p), 7)} 100644\n"
        + pe.unified_diff(p, original.get(p, ""), repo.files[p], context=3)
        for p in changed
    ) + "\n"
    kit.turn("Show the whole diff so I can review.",
             [rnd("Full diff.", step("terminal", {"command": f"git -C {_REPO} diff"}, tool(diff)))],
             "The diff is mostly mechanical renames plus the pricing move.")
    # ---- T32: grep for leftovers ---------------------------------------------------------------------------------------------------------------------
    kit.turn("Make sure PAID is gone.",
             [rnd("Final grep.", rg("OrderStatus.PAID", 3)), rnd("And the search tool.", search("PAID", 3))],
             "No OrderStatus.PAID is left; only the stored string and the partner webhook comment mention paid.")
    # ---- T33: reread service -----------------------------------------------------------------------------------------------------------------------------
    kit.turn("Print the service one last time for the PR.", [rnd("Re-reading service.py.", read(_SERVICE, 5))],
             "Same content as before the test run.")
    # ---- T34 ---------------------------------------------------------------------------------------------------------------------------------------------------
    kit.turn("I think we are done.", [], "Agreed. I will wait for your go-ahead before opening the PR.")

    return kit.finish(REFACTOR_FINAL_QUESTION, turns, natural=REFACTOR_TURNS, log_turn=t5)


REFACTOR_SPEC = ScenarioSpec(
    "refactor", build_refactor, REFACTOR_TURNS, 4,
    "35 turns, code: read -> patch -> re-read on seven 9-18 KB files, rg / search_files and pytest re-run as the rename advances",
)
