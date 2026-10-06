"""Hermes decides when to prune and whether to commit; the engine only changes what a row becomes."""
from __future__ import annotations

import pytest
from engine_support import Chat, make_engine, pytest_failure, server_log, source_file

pytestmark = pytest.mark.real_hermes


def _messages() -> list[dict]:
    chat = Chat("why did the payment gateway time out?")
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    chat.tool("terminal", {"command": "tail -n 450 app.log"}, server_log())
    chat.tool("read_file", {"path": "orders/service.py"}, source_file())
    return chat.finish()


def _count_seam_calls(engine) -> list[int]:
    calls: list[int] = []
    original = engine._demote_tool_result_at

    def spy(result, idx, *args, **kwargs):
        calls.append(idx)
        return original(result, idx, *args, **kwargs)

    engine._demote_tool_result_at = spy
    return calls


def test_proactive_prune_disabled_makes_zero_seam_calls_and_returns_the_input():
    engine = make_engine(proactive_prune_tokens=0)
    calls = _count_seam_calls(engine)
    messages = _messages()
    res = engine.prune_tool_results_only(messages, 500_000)
    assert res[0] is messages and res[1] == 0 and calls == []
    assert engine._telemetry.snapshot()["events"] == {}


def test_below_the_token_trigger_makes_zero_seam_calls():
    engine = make_engine(proactive_prune_tokens=100_000)
    calls = _count_seam_calls(engine)
    messages = _messages()
    res = engine.prune_tool_results_only(messages, 10)
    assert res[0] is messages and calls == []


def test_short_conversation_makes_zero_seam_calls():
    engine = make_engine()
    calls = _count_seam_calls(engine)
    chat = Chat()
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    messages = chat.messages
    res = engine.prune_tool_results_only(messages, 500_000)
    assert res[0] is messages and calls == []


def test_commit_returns_hermes_own_pair():
    engine = make_engine()
    messages = _messages()
    pruned, count = engine.prune_tool_results_only(messages, 500_000)
    assert pruned is not messages and count >= 3
    assert engine._telemetry.snapshot()["events"].get("escalation") is None


def test_failed_min_reclaim_gate_returns_the_input_after_exactly_one_escalation():
    engine = make_engine(proactive_prune_min_reclaim_tokens=10_000_000)
    messages = _messages()
    before = [dict(m) for m in messages]
    res = engine.prune_tool_results_only(messages, 500_000)
    assert res[0] is messages and res[1] == 0
    assert messages == before                      # the input is never mutated
    assert engine._telemetry.snapshot()["events"]["escalation"]["count"] == 1
    # Nothing was rewritten, so no row is counted as rendered and no character as saved.
    snapshot = engine._telemetry.snapshot()
    assert "render" not in snapshot["events"] and snapshot["chars"] == {"before": 0, "after": 0, "saved": 0}


def test_the_escalation_pass_runs_with_header_only_rungs():
    engine = make_engine(proactive_prune_min_reclaim_tokens=10_000_000)
    degrade = []
    new_scope = engine._new_scope
    engine._new_scope = lambda messages, **fields: degrade.append(fields.get("degrade", False)) or new_scope(
        messages, **fields,
    )
    engine.prune_tool_results_only(_messages(), 500_000)
    assert degrade == [False, True]


def test_a_declined_pass_leaves_no_telemetry_log_line(tmp_path):
    from hermes_tameru_plugin.config import TameruSettings

    log = tmp_path / "t.jsonl"
    engine = make_engine(TameruSettings(telemetry_log=str(log)), proactive_prune_min_reclaim_tokens=10_000_000)
    assert engine.prune_tool_results_only(_messages(), 500_000)[1] == 0
    assert not log.exists()
    committed = make_engine(TameruSettings(telemetry_log=str(log)))
    assert committed.prune_tool_results_only(_messages(), 500_000)[1] >= 3
    assert log.read_text().count('"event": "render"') >= 3


def test_nothing_eligible_means_no_escalation():
    engine = make_engine()
    chat = Chat()
    for i in range(3):
        chat.tool("terminal", {"command": f"echo {i}"}, "short")
    messages = chat.finish()
    res = engine.prune_tool_results_only(messages, 500_000)
    assert res[0] is messages
    assert "escalation" not in engine._telemetry.snapshot()["events"]


def test_kill_switch_runs_stock_prune():
    from hermes_tameru_plugin.config import TameruSettings
    engine = make_engine(TameruSettings(enabled=False))
    messages = _messages()
    pruned, count = engine.prune_tool_results_only(messages, 500_000)
    assert count >= 3 and engine._telemetry.snapshot()["events"] == {}


def test_escalation_commits_when_header_only_rows_pass_the_gate():
    from agent.context_compressor import _estimate_msg_budget_tokens
    from engine_support import prune_pass

    def tokens(msgs):
        return sum(_estimate_msg_budget_tokens(m) for m in msgs)

    messages = _messages()
    normal, _ = prune_pass(make_engine(), messages)
    degraded, _ = prune_pass(make_engine(), messages, degrade=True)
    reclaim_normal, reclaim_degraded = tokens(messages) - tokens(normal), tokens(messages) - tokens(degraded)
    assert reclaim_degraded > reclaim_normal > 0

    engine = make_engine(proactive_prune_min_reclaim_tokens=(reclaim_normal + reclaim_degraded) // 2)
    pruned, count = engine.prune_tool_results_only(messages, 500_000)
    assert pruned is not messages and count >= 3
    assert engine._telemetry.snapshot()["events"]["escalation"]["count"] == 1
    assert tokens(messages) - tokens(pruned) >= engine.proactive_prune_min_reclaim_tokens
