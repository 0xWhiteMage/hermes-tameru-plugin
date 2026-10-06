"""The seam-A ladder: which rung a demoted tool result lands on, and what takes Hermes' own line."""
from __future__ import annotations

import json
import random

import pytest
from conftest import REAL_HERMES
from engine_support import (
    Chat, demote, in_scope, make_engine, pytest_failure, server_log, source_file,
)
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.hermes_compat import summarize_tool_result, tool_calls_by_id
from hermes_tameru_plugin.render import classify_render, parse_header

TASK_NAMING_THE_TEST = "tests/test_orders.py::test_reserve_stock_rollback failed"


def _rung(content: str) -> str | None:
    header = parse_header(content)
    return header["rung"] if header else None


def _one(task: str, name: str, args: dict, content, **engine_kw):
    """A chat holding one tool result of ``content``; ``(engine, messages, idx)``."""
    chat = Chat(task)
    idx = chat.tool(name, args, content)
    return make_engine(**engine_kw), chat.finish(), idx


def test_extract_rung_keeps_the_lines_the_query_names():
    engine, msgs, idx = _one(TASK_NAMING_THE_TEST, "terminal", {"command": "pytest -q"}, pytest_failure())
    changed, out = demote(engine, msgs, idx)
    row = out[idx]["content"]
    assert changed and _rung(row) == "extract"
    assert len(row) < 0.7 * len(msgs[idx]["content"])
    assert "test_reserve_stock_rollback" in row
    assert json.loads(row.split("\n")[1]) == {"exit_code": 1, "error": None}   # meta line
    assert classify_render(row) == "v1"


def test_brief_rung_when_the_extract_is_not_acceptable():
    engine, msgs, idx = _one("why did the payment gateway time out?", "terminal",
                             {"command": "tail -n 450 app.log"}, server_log())
    changed, out = demote(engine, msgs, idx)
    row = out[idx]["content"]
    assert changed and _rung(row) == "brief"
    assert len(row) <= engine._settings.brief_chars + 200
    assert "FATAL" in row or "ERROR" in row


def test_header_rung_in_the_pressure_pass_and_when_degraded():
    engine, msgs, idx = _one(TASK_NAMING_THE_TEST, "terminal", {"command": "pytest -q"}, pytest_failure())
    for scope in ({"pressure": True}, {"degrade": True}):
        changed, out = demote(engine, msgs, idx, scope=scope)
        row = out[idx]["content"]
        assert changed and _rung(row) == "header" and "\n" not in row and len(row) <= 380


def test_superseded_rung_names_the_later_call():
    chat = Chat("fix the rollback")
    first = chat.tool("read_file", {"path": "orders/service.py"}, source_file())
    chat.tool("patch", {"path": "orders/service.py", "old_string": "a", "new_string": "b"},
              hp.patch_result("--- a\n+++ b\n", ["orders/service.py"]))
    chat.tool("read_file", {"path": "orders/service.py"}, source_file(patched=True))
    msgs = chat.finish()
    changed, out = demote(make_engine(), msgs, first)
    row = out[first]["content"]
    assert changed and _rung(row) == "superseded" and "\n" not in row
    assert "superseded by msg" in row and "stale-after-write" in row


def test_supersession_can_be_switched_off():
    chat = Chat("x")
    first = chat.tool("read_file", {"path": "orders/service.py"}, source_file())
    chat.tool("read_file", {"path": "orders/service.py"}, source_file())
    msgs = chat.finish()
    engine = make_engine(TameruSettings(supersession=False))
    _, out = demote(engine, msgs, first)
    assert _rung(out[first]["content"]) != "superseded"


@pytest.mark.parametrize("name,args,content", [
    ("skill_view", {"name": "deploy"}, hp.skill_view_result("deploy", "d", hp.skill_body(random.Random(1)))),
    ("memory", {"action": "read"}, "m" * 3000),
    ("todo", {}, "t" * 3000),
    ("tameru_expand", {"ref": "deadbeef"}, "e" * 3000),
])
def test_exempt_tools_take_hermes_line(name, args, content):
    engine, msgs, idx = _one("q", name, args, content)
    changed, out = demote(engine, msgs, idx)
    assert changed and classify_render(out[idx]["content"]) is None
    assert out[idx]["content"] == summarize_tool_result(name, json.dumps(args), content)
    assert len(engine._store) == 0


def test_settings_extend_the_exempt_set():
    engine, msgs, idx = _one("q", "terminal", {"command": "tail app.log"}, server_log(),
                             settings=TameruSettings(exempt_tools=("terminal",)))
    changed, out = demote(engine, msgs, idx)
    assert changed and classify_render(out[idx]["content"]) is None


def test_refused_call_keeps_the_refusal_line():
    content = json.dumps({"status": "blocked", "error": "BLOCKED: rm -rf was NOT consented",
                          "details": "x" * 2000})
    engine, msgs, idx = _one("q", "terminal", {"command": "rm -rf build"}, content)
    changed, out = demote(engine, msgs, idx)
    assert changed and classify_render(out[idx]["content"]) is None
    assert len(engine._store) == 0
    if REAL_HERMES:   # the stub's summarizer has no refusal wording
        assert "BLOCKED, not run" in out[idx]["content"]


def test_persisted_output_and_skill_markers_take_hermes_line():
    persisted = hp.persisted_output("p" * 1500, 90_000, "/tmp/spill.txt")
    engine, msgs, idx = _one("q", "terminal", {"command": "make"}, persisted)
    _, out = demote(engine, msgs, idx)
    assert classify_render(out[idx]["content"]) is None and len(out[idx]["content"]) < 400


def test_short_results_take_hermes_line():
    body = "x" * 500
    engine, msgs, idx = _one("q", "terminal", {"command": "echo"}, hp.terminal_result(body))
    _, out = demote(engine, msgs, idx)
    assert classify_render(out[idx]["content"]) is None and len(engine._store) == 0


def test_multimodal_results_take_the_parent_path():
    content = [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:x"}}]
    engine, msgs, idx = _one("q", "browser_snapshot", {}, content)
    result = [dict(m) for m in msgs]
    with in_scope(engine, msgs):
        engine._demote_tool_result_at(result, idx, tool_calls_by_id(result), 200, set())
    assert not isinstance(result[idx]["content"], str) or classify_render(result[idx]["content"]) is None
    assert len(engine._store) == 0


def test_pass_budget_sends_the_rest_to_hermes_line():
    settings = TameruSettings(pass_char_budget=10_000)
    chat = Chat(TASK_NAMING_THE_TEST)
    a = chat.tool("terminal", {"command": "pytest a"}, pytest_failure(1))   # ~7k: fits
    b = chat.tool("terminal", {"command": "pytest b"}, pytest_failure(2))   # does not fit any more
    msgs = chat.finish()
    engine = make_engine(settings)
    result = [dict(m) for m in msgs]
    with in_scope(engine, msgs) as scope:
        scope.budget = settings.pass_char_budget
        for i in (a, b):
            engine._demote_tool_result_at(result, i, tool_calls_by_id(result), 200, set())
    assert classify_render(result[a]["content"]) == "v1"
    assert classify_render(result[b]["content"]) is None
    assert engine._telemetry.snapshot()["events"]["budget_skip"]["count"] == 1


def test_no_acceptable_rung_falls_back_to_hermes_line():
    """Nothing small enough: Hermes' own line, and nothing stored."""
    engine, msgs, idx = _one("q", "terminal", {"command": "ls"}, server_log(),
                             settings=TameruSettings(brief_chars=10, max_risk="low"))
    _, out = demote(engine, msgs, idx)
    assert classify_render(out[idx]["content"]) is None
    assert out[idx]["content"].startswith("[terminal]")


def test_originals_are_kept_for_expand():
    engine, msgs, idx = _one(TASK_NAMING_THE_TEST, "terminal", {"command": "pytest -q"}, pytest_failure())
    _, out = demote(engine, msgs, idx)
    ref = parse_header(out[idx]["content"])["ref"]
    assert ref in engine._store and "test_reserve_stock_rollback" in engine._store.get(ref)


def test_the_kill_switch_gives_stock_behaviour():
    engine, msgs, idx = _one("q", "terminal", {"command": "tail app.log"}, server_log(),
                             settings=TameruSettings(enabled=False))
    _, out = demote(engine, msgs, idx)
    assert classify_render(out[idx]["content"]) is None and out[idx]["content"].startswith("[terminal]")
    assert len(engine._store) == 0


@pytest.mark.parametrize("scope", [{"pressure": True}, {"degrade": True}])
def test_header_row_keeps_the_tail_of_a_multi_line_parent_line(scope):
    """A heredoc command makes Hermes' line span lines; the header must keep its ``-> exit N`` tail."""
    command = "python - <<'EOF'\nprint(1)\nEOF"
    engine, msgs, idx = _one(
        "q", "terminal", {"command": command}, hp.terminal_result("x" * 3000, exit_code=2),
    )
    parent = summarize_tool_result("terminal", json.dumps({"command": command}), msgs[idx]["content"])
    _, out = demote(engine, msgs, idx, scope=scope)
    row = out[idx]["content"]
    assert _rung(row) == "header" and "\n" not in row and len(row) < 400
    assert " ".join(parent.splitlines()) in row


def test_the_brief_is_not_computed_when_the_extract_is_accepted(monkeypatch):
    from hermes_tameru_plugin import engine as engine_module

    calls: list[int] = []
    real = engine_module.brief_context
    monkeypatch.setattr(engine_module, "brief_context", lambda *a, **k: calls.append(1) or real(*a, **k))
    engine, msgs, idx = _one(TASK_NAMING_THE_TEST, "terminal", {"command": "pytest -q"}, pytest_failure())
    _, out = demote(engine, msgs, idx)
    assert _rung(out[idx]["content"]) == "extract" and calls == []
