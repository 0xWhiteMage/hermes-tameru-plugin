"""Rendered rows share ``retained_extract_budget_chars``: the newest keep their bodies, older ones step down."""
from __future__ import annotations

import random

import pytest
from engine_support import Chat, make_engine, prune_pass
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
    return sum(len(messages[i]["content"]) for i in idxs if "\n" in messages[i]["content"])


def test_rows_beyond_the_budget_step_down_newest_first():
    messages, idxs = _session()
    committed, _ = prune_pass(make_engine(), messages)
    assert set(_rungs(committed, idxs)) == {"brief"}
    one = len(committed[idxs[0]]["content"])

    engine = make_engine(TameruSettings(retained_extract_budget_chars=2 * one + 50))
    stepped, n = prune_pass(engine, committed)
    assert n == ROWS - 2
    assert _rungs(stepped, idxs) == ["header"] * (ROWS - 2) + ["brief"] * 2
    assert all(stepped[i] is committed[i] for i in idxs[-2:]) or all(
        stepped[i]["content"] == committed[i]["content"] for i in idxs[-2:]
    )
    # Demoted rows keep their ref, so the original stays recoverable.
    for i in idxs[:-2]:
        assert parse_header(stepped[i]["content"])["ref"] == parse_header(committed[i]["content"])["ref"]


def test_a_budget_that_fits_everything_changes_nothing():
    messages, idxs = _session()
    committed, _ = prune_pass(make_engine(), messages)
    engine = make_engine(TameruSettings(retained_extract_budget_chars=10**6))
    again, n = prune_pass(engine, committed)
    assert n == 0 and again == committed


def test_new_rows_stop_taking_bodies_when_the_budget_is_spent():
    messages, idxs = _session()
    budget = 3000
    engine = make_engine(TameruSettings(retained_extract_budget_chars=budget))
    out, _ = prune_pass(engine, messages)
    rungs = _rungs(out, idxs)
    assert "header" in rungs and rungs.count("brief") + rungs.count("extract") >= 1
    assert _bodies(out, idxs) <= budget
    assert all(len(out[i]["content"]) <= 380 for i in idxs if "\n" not in out[i]["content"])


def test_the_budget_counts_rows_outside_this_pass_too():
    """Rendered rows in the protected tail still use up budget, so older rows give way."""
    messages, idxs = _session()
    committed, _ = prune_pass(make_engine(), messages)
    chat = Chat()
    tail_idx = chat.tool("terminal", {"command": "tail -n 449 app.log"}, server_log(9))
    tail = chat.messages[2:]
    session = [*committed, *tail]
    one = len(committed[idxs[0]]["content"])
    engine = make_engine(TameruSettings(retained_extract_budget_chars=one + 50))
    out, _ = prune_pass(engine, session)
    assert tail_idx and _rungs(out, idxs).count("header") >= ROWS - 1


@pytest.mark.real_hermes
def test_only_a_committing_pass_changes_rows():
    """Hermes' min-reclaim gate decides: when it declines, the input list comes back untouched."""
    messages, idxs = _session()
    committed, _ = prune_pass(make_engine(), messages)
    before = [dict(m) for m in committed]
    engine = make_engine(
        TameruSettings(retained_extract_budget_chars=100), proactive_prune_min_reclaim_tokens=10**7,
    )
    res = engine.prune_tool_results_only(committed, 500_000)
    assert res[0] is committed and committed == before
