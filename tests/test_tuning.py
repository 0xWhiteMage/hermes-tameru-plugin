"""Phase 5 tuning: the token-sized prune tail, the pass-4 guard, and errors that outlive their rows."""
from __future__ import annotations

import json

from engine_support import MIN_PRUNE_CHARS, Chat, demote, in_scope, make_engine, prune_pass, server_log
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.render import classify_render, parse_header
from hermes_tameru_plugin.tameru.compress_context import error_fingerprints

COUNT_TAIL = 20


def _session(fillers: int = 8) -> tuple[list[dict], int]:
    """One big log, then ``fillers`` small rounds: the log sits inside Hermes' 20-message count tail."""
    chat = Chat()
    big = chat.tool("terminal", {"command": "tail -n 450 app.log"}, server_log())
    for i in range(fillers):
        chat.tool("terminal", {"command": f"echo filler {i}"}, hp.terminal_result("ok"))
    chat.user("continue")
    assert len(chat.messages) - big <= COUNT_TAIL
    return chat.messages, big


def _prune(engine, messages: list[dict], **scope) -> list[dict]:
    with in_scope(engine, messages, **scope):
        out, _ = engine._prune_old_tool_results(
            messages, protect_tail_count=COUNT_TAIL, min_prune_chars=MIN_PRUNE_CHARS,
        )
    return out


def _engine(**settings):
    engine = make_engine(TameruSettings(**settings), protect_last_n=COUNT_TAIL)
    engine.tail_token_budget = 1500   # the big log alone is far over it, the small rounds are not
    return engine


def test_the_token_tail_demotes_a_big_old_row_inside_the_count_tail():
    messages, big = _session()
    out = _prune(_engine(), messages)
    assert classify_render(out[big]["content"]) == "v1", "a 50 KB result 17 messages ago is not worth its bytes"
    assert len(out[big]["content"]) <= TameruSettings().max_extract_chars < len(messages[big]["content"]) / 5
    assert all(out[i] == messages[i] for i in range(len(messages) - 8, len(messages))), "the newest rows stay"


def test_prune_tail_count_keeps_hermes_message_tail():
    messages, big = _session()
    out = _prune(_engine(prune_tail="count"), messages)
    assert out[big]["content"] == messages[big]["content"]


def test_a_full_compaction_keeps_hermes_own_boundary():
    """``compress`` already passes a token tail of its own: the proactive-prune rule must not apply there."""
    messages, big = _session()
    out = _prune(_engine(), messages, full=True)
    assert out[big]["content"] == messages[big]["content"]


def test_the_token_tail_is_never_shorter_than_it_would_be_without_it():
    """When everything fits the budget the count tail still decides (the short tail wins, never the long)."""
    messages, big = _session()
    engine = _engine()
    engine.tail_token_budget = 10**6
    out = _prune(engine, messages)
    assert out[big]["content"] == messages[big]["content"]


def test_the_disabled_plugin_leaves_the_prune_to_hermes():
    messages, big = _session()
    out = _prune(_engine(enabled=False), messages)
    assert out[big]["content"] == messages[big]["content"]


def _skill_chat() -> tuple[list[dict], int]:
    chat = Chat()
    idx = chat.tool(
        "skill_view", {"name": "debugging"},
        hp.skill_view_result("debugging", "Debug", hp.skill_body(__import__("random").Random(1), n_sections=18)),
    )
    chat.user("go on")
    return chat.messages, idx


def test_pass_4_of_a_proactive_prune_spares_instruction_rows_but_a_compaction_may_cut_them():
    messages, idx = _skill_chat()
    changed, out = demote(make_engine(), messages, idx, rest=())
    assert not changed and out[idx] == messages[idx]
    changed, out = demote(make_engine(), messages, idx, rest=(), scope={"full": True})
    assert changed and out[idx]["content"] != messages[idx]["content"]


# ---- errors outlive the rows that held them --------------------------------------------------------
def _error_session() -> tuple[list[dict], int]:
    chat = Chat()
    log = chat.tool("terminal", {"command": "tail -n 450 app.log"}, server_log())
    return chat.finish(), log


def test_a_summary_keeps_the_errors_of_a_row_that_was_cut_to_a_header_and_kept_in_the_tail():
    messages, log = _error_session()
    original = json.loads(messages[log]["content"])["output"]
    engine = make_engine(TameruSettings(retained_extract_budget_chars=0))
    with in_scope(engine, messages, full=True):
        out, _ = engine._prune_old_tool_results(messages, protect_tail_count=4, min_prune_chars=MIN_PRUNE_CHARS)
        assert parse_header(out[log]["content"])["rung"] == "header"
        summary = engine._augment_summary_lean("## Goal\nfix it", [{"role": "user", "content": "hi"}])
    assert "## Artifact Trail (Tameru, exact)\nErrors:" in summary
    want = error_fingerprints(original)
    assert want and error_fingerprints(summary).keys() >= want.keys()


def test_the_errors_of_a_header_from_an_earlier_pass_are_found_by_its_ref():
    messages, log = _error_session()
    engine = make_engine(TameruSettings(retained_extract_budget_chars=0))
    committed, _ = prune_pass(engine, messages)
    assert parse_header(committed[log]["content"])["rung"] == "header"
    with in_scope(engine, committed):   # a later compaction: nothing was cut in this pass
        summary = engine._augment_summary_lean("## Goal\nfix it", [{"role": "user", "content": "hi"}])
    want = error_fingerprints(json.loads(messages[log]["content"])["output"])
    assert error_fingerprints(summary).keys() >= want.keys()


def test_a_body_that_keeps_its_errors_adds_nothing_to_the_summary_outside_its_turns():
    messages, log = _error_session()
    engine = make_engine()
    committed, _ = prune_pass(engine, messages)
    assert parse_header(committed[log]["content"])["rung"] == "brief"
    with in_scope(engine, committed):
        summary = engine._augment_summary_lean("## Goal\nfix it", [{"role": "user", "content": "hi"}])
    assert "Errors:" not in summary


def test_with_the_ledger_off_the_summary_carries_no_trail():
    messages, log = _error_session()
    engine = make_engine(TameruSettings(ledger=False, retained_extract_budget_chars=0))
    with in_scope(engine, messages, full=True):
        engine._prune_old_tool_results(messages, protect_tail_count=4, min_prune_chars=MIN_PRUNE_CHARS)
        assert "Artifact Trail" not in engine._augment_summary_lean("## Goal\nfix it", [])


def test_errors_of_a_brief_that_a_compaction_steps_down_to_a_header_are_kept():
    messages, log = _error_session()
    engine = make_engine()
    committed, _ = prune_pass(engine, messages)
    assert parse_header(committed[log]["content"])["rung"] == "brief"
    engine._settings = TameruSettings(retained_extract_budget_chars=0)   # the budget is now spent: step down
    with in_scope(engine, committed, full=True) as scope:
        out, _ = engine._prune_old_tool_results(committed, protect_tail_count=4, min_prune_chars=MIN_PRUNE_CHARS)
        assert parse_header(out[log]["content"])["rung"] == "header" and scope.gone
        summary = engine._augment_summary_lean("## Goal\nfix it", [])
    want = error_fingerprints(json.loads(messages[log]["content"])["output"])
    assert error_fingerprints(summary).keys() >= want.keys()
