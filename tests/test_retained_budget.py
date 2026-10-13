"""Rendered rows share ``retained_extract_budget_chars``: the newest keep their bodies, older ones step down."""
from __future__ import annotations

import random

import pytest
from engine_support import Chat, make_engine, prune_pass, pytest_failure_with_passes
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.render import parse_header

ROWS = 5


def server_log(seed: int) -> str:
    """A 160-line application log (kept small: every extract attempt costs engine time)."""
    return hp.terminal_result(hp.server_log(random.Random(seed), n_lines=160)[0])


def _session() -> tuple[list[dict], list[int]]:
    chat = Chat("why did the payment gateway time out?")
    idxs = [chat.tool("terminal", {"command": f"tail -n {400 + i} app.log"}, server_log(i)) for i in range(ROWS)]
    return chat.finish(), idxs


def _rungs(messages: list[dict], idxs: list[int]) -> list[str]:
    return [parse_header(messages[i]["content"])["rung"] for i in idxs]


def _bodies(messages: list[dict], idxs: list[int]) -> int:
    """Chars of the extract/brief bodies (``errors`` rows have a budget of their own)."""
    return sum(
        len(messages[i]["content"]) for i in idxs
        if "\n" in messages[i]["content"] and parse_header(messages[i]["content"])["rung"] != "errors"
    )


def _all_bodies() -> TameruSettings:
    return TameruSettings(small_window_threshold_tokens=0, retained_extract_budget_chars=10**6, retained_extract_budget_ratio=0)


def test_rows_beyond_the_budget_step_down_newest_first():
    messages, idxs = _session()
    committed, _ = prune_pass(make_engine(_all_bodies()), messages)
    rungs = _rungs(committed, idxs)
    assert set(rungs) <= {"brief", "extract"}
    newest_two = sum(len(committed[i]["content"]) for i in idxs[-2:])

    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, retained_extract_budget_chars=newest_two + 50, retained_extract_budget_ratio=0))
    stepped, n = prune_pass(engine, committed)
    assert n == ROWS - 2
    assert _rungs(stepped, idxs) == ["header"] * (ROWS - 2) + rungs[-2:]
    assert all(stepped[i] is committed[i] for i in idxs[-2:]) or all(
        stepped[i]["content"] == committed[i]["content"] for i in idxs[-2:]
    )
    # Demoted rows keep their ref, so the original stays recoverable.
    for i in idxs[:-2]:
        assert parse_header(stepped[i]["content"])["ref"] == parse_header(committed[i]["content"])["ref"]


def test_one_pass_keeps_its_bodies_within_the_budget():
    """Within one pass the budget goes to the newest rows first (fix-all R5: briefs honour the pass-2
    reservation too), and the older rows fall back to headers."""
    messages, idxs = _session()
    budget = 4000
    out, _ = prune_pass(make_engine(TameruSettings(small_window_threshold_tokens=0, retained_extract_budget_chars=budget, retained_extract_budget_ratio=0)), messages)
    bodied = [_rungs(out, idxs)[k] in ("extract", "brief") for k in range(ROWS)]
    assert any(bodied) and not all(bodied)
    assert bodied == sorted(bodied), bodied
    assert _bodies(out, idxs) <= budget
    for i in idxs:   # every row stays recoverable
        assert parse_header(out[i]["content"])["ref"]


def test_a_budget_that_fits_everything_changes_nothing():
    messages, idxs = _session()
    committed, _ = prune_pass(make_engine(_all_bodies()), messages)
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, retained_extract_budget_chars=10**6, retained_extract_budget_ratio=0))
    again, n = prune_pass(engine, committed)
    assert n == 0 and again == committed


def test_new_rows_stop_taking_bodies_when_the_budget_is_spent():
    messages, idxs = _session()
    budget = 3000
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, retained_extract_budget_chars=budget, retained_extract_budget_ratio=0))
    out, _ = prune_pass(engine, messages)
    rungs = _rungs(out, idxs)
    assert ("header" in rungs or "errors" in rungs) and rungs.count("brief") + rungs.count("extract") >= 1
    assert _bodies(out, idxs) <= budget
    assert all(len(out[i]["content"]) <= 380 for i in idxs if "\n" not in out[i]["content"])


def test_the_budget_counts_rows_outside_this_pass_too():
    """Rendered rows in the protected tail still use up budget, so older rows give way."""
    messages, idxs = _session()
    committed, _ = prune_pass(make_engine(_all_bodies()), messages)
    chat = Chat()
    tail_idx = chat.tool("terminal", {"command": "tail -n 449 app.log"}, server_log(9))
    tail = chat.messages[2:]
    session = [*committed, *tail]
    one = len(committed[idxs[0]]["content"])
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, retained_extract_budget_chars=one + 50, retained_extract_budget_ratio=0))
    out, _ = prune_pass(engine, session)
    assert tail_idx and _rungs(out, idxs).count("header") >= ROWS - 1


@pytest.mark.real_hermes
def test_only_a_committing_pass_changes_rows():
    """Hermes' min-reclaim gate decides: when it declines, the input list comes back untouched."""
    messages, idxs = _session()
    committed, _ = prune_pass(make_engine(_all_bodies()), messages)
    before = [dict(m) for m in committed]
    engine = make_engine(
        TameruSettings(small_window_threshold_tokens=0, retained_extract_budget_chars=100, retained_extract_budget_ratio=0), proactive_prune_min_reclaim_tokens=10**7,
    )
    res = engine.prune_tool_results_only(committed, 500_000)
    assert res[0] is committed and committed == before


def _old_failure_then_logs() -> tuple[list[dict], int, list[int]]:
    """An old pytest failure whose extract is large (~3.3k), then four logs that each need a ~1.2-1.8k brief."""
    chat = Chat("tests/test_orders.py::test_reserve_stock_rollback failed")
    old = chat.tool("terminal", {"command": "pytest -v"}, pytest_failure_with_passes())
    idxs = [chat.tool("terminal", {"command": f"tail -n {400 + i} app.log"}, server_log(i)) for i in range(4)]
    return chat.finish(), old, idxs


def test_an_old_extract_leaves_room_for_the_briefs_still_to_come():
    """Hermes visits pass 2 oldest first. 1.4.1: an old row's extract may only use the room left after a
    ``brief_chars`` reservation for each later row of the pass, so one big early extract no longer starves the
    newer rows down to headers (the tameru_1_4 replay regression). Fix-all R5: the brief honours the
    reservation too, so here the old row only keeps its header and the newer logs keep a brief. A log whose
    allotment is too small for a brief with every error template falls to the ``errors`` rung (a brief that misses
    a template is refused), so no log loses an error line."""
    messages, old, idxs = _old_failure_then_logs()
    budget = 6000
    out, _ = prune_pass(make_engine(TameruSettings(small_window_threshold_tokens=0, retained_extract_budget_chars=budget, retained_extract_budget_ratio=0)), messages)
    rungs = _rungs(out, [old, *idxs])
    assert rungs[0] in ("header", "errors"), rungs   # neither its extract (~3.3k) nor its brief fits the room
    assert set(rungs[1:]) <= {"brief", "errors"} and rungs[-3:] == ["brief"] * 3, rungs
    assert _bodies(out, [old, *idxs]) <= budget


def test_the_reservation_does_not_bite_when_the_budget_fits():
    messages, old, idxs = _old_failure_then_logs()
    out, _ = prune_pass(make_engine(_all_bodies()), messages)
    assert _rungs(out, [old, *idxs]) == ["extract"] + ["brief"] * len(idxs)
