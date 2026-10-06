"""Seam B: Hermes' lean-tail stubs of old tail rows keep a Tameru ``ref`` the agent can expand."""
from __future__ import annotations

import json

import pytest
from agent.context_compressor import _is_summary_stub
from engine_support import Chat, demote, make_engine, pytest_failure, server_log, source_file
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.hermes_compat import LEAN_STUB_RE
from hermes_tameru_plugin.recovery import expand
from hermes_tameru_plugin.render import classify_render, parse_header

ROUNDS = 9   # Hermes keeps the newest few rounds whole; the older ones are demoted


def _tail(first_content=None, name="terminal", args=None) -> tuple[list[dict], int]:
    """``ROUNDS`` rounds of big results; round 0 holds ``first_content`` (default: a pytest failure)."""
    chat = Chat("why did the payment gateway time out?")
    first = chat.tool(name, args or {"command": "pytest -q"}, first_content or pytest_failure(0))
    for i in range(1, ROUNDS):
        chat.tool("terminal", {"command": f"tail -n {100 + i} app.log"}, server_log(i))
    return chat.messages, first


def _lean(engine):
    engine.tail_mode = "lean"
    return engine


def test_hermes_alone_leaves_a_recovery_stub_without_a_ref():
    """The premise: stock Hermes stubs the oldest tail row, and the stub names no ref."""
    messages, first = _tail()
    from agent.context_compressor import ContextCompressor
    stock = ContextCompressor.__new__(ContextCompressor)
    stock.quiet_mode, stock._session_id = True, "s1"
    out = ContextCompressor._demote_stale_tail_tools(stock, messages, 2)
    assert LEAN_STUB_RE.match(out[first]["content"]) and "ref=" not in out[first]["content"]


def test_a_raw_row_becomes_a_header_with_a_ref_that_expands():
    engine = _lean(make_engine())
    messages, first = _tail()
    out = engine._demote_stale_tail_tools(messages, 2)
    row = out[first]["content"]
    header = parse_header(row)
    assert header and header["rung"] == "header" and "\n" not in row
    assert len(row) <= 380 and _is_summary_stub(row) and not LEAN_STUB_RE.match(row)
    assert messages[first]["content"] != row                  # the input list is untouched
    body = json.loads(expand(engine._store, {"ref": header["ref"]}))
    assert "test_reserve_stock_rollback" in body["content"]


def test_a_rendered_row_keeps_its_ref():
    """A row big enough for Hermes to stub (>= 1,500 chars) is cut to a header with the same ref."""
    engine = _lean(make_engine(TameruSettings(brief_chars=2400)))
    messages, first = _tail(server_log(0), args={"command": "tail -n 450 app.log"})
    _, rendered = demote(engine, messages, first)
    row = rendered[first]["content"]
    assert parse_header(row)["rung"] == "brief" and len(row) >= 1500
    ref = parse_header(row)["ref"]
    messages[first] = rendered[first]
    out = engine._demote_stale_tail_tools(messages, 2)
    cut = out[first]["content"]
    assert parse_header(cut)["ref"] == ref and parse_header(cut)["rung"] == "header"
    assert "\n" not in cut and not LEAN_STUB_RE.match(cut) and _is_summary_stub(cut)
    assert engine._store.get(ref)


def test_rows_inside_the_kept_rounds_are_untouched():
    engine = _lean(make_engine())
    messages, first = _tail()
    out = engine._demote_stale_tail_tools(messages, 2)
    assert out[-1] is messages[-1]
    assert classify_render(out[-1]["content"]) is None


def test_exempt_tools_keep_hermes_stub():
    engine = _lean(make_engine())
    content = hp.skill_view_result("deploy", "d", "body line\n" * 400)
    messages, first = _tail(content, "skill_view", {"name": "deploy"})
    out = engine._demote_stale_tail_tools(messages, 2)
    assert LEAN_STUB_RE.match(out[first]["content"])


def test_legacy_rows_keep_hermes_stub():
    legacy = "\n[…]\n".join("\n".join(f"line {j} of block {i} " * 5 for j in range(8)) for i in range(3))
    engine = _lean(make_engine())
    messages, first = _tail(legacy)
    out = engine._demote_stale_tail_tools(messages, 2)
    assert LEAN_STUB_RE.match(out[first]["content"])


def test_kill_switch_and_missing_seam_leave_hermes_output():
    messages, first = _tail()
    off = _lean(make_engine(TameruSettings(enabled=False)))
    assert LEAN_STUB_RE.match(off._demote_stale_tail_tools(messages, 2)[first]["content"])
    engine = _lean(make_engine())
    engine._seams = type(engine._seams)()
    assert LEAN_STUB_RE.match(engine._demote_stale_tail_tools(messages, 2)[first]["content"])


def test_a_failure_in_the_hook_returns_hermes_output(monkeypatch):
    from hermes_tameru_plugin import engine as engine_module

    def boom(*a, **kw):
        raise RuntimeError("render failed")

    monkeypatch.setattr(engine_module, "header_only", boom)
    engine = _lean(make_engine())
    messages, first = _tail(source_file())
    out = engine._demote_stale_tail_tools(messages, 2)
    assert LEAN_STUB_RE.match(out[first]["content"])
    assert engine._telemetry.snapshot()["events"]["error"]["count"] == 1


@pytest.mark.parametrize("tail_start", [0, 2])
def test_rows_before_tail_start_are_never_touched(tail_start):
    engine = _lean(make_engine())
    messages, first = _tail()
    out = engine._demote_stale_tail_tools(messages, tail_start + 4)
    assert out[first] is messages[first]
