"""``get_status``, the expand tool's wiring and the engine's lifecycle hooks."""
from __future__ import annotations

import json

from engine_support import Chat, demote, make_engine, pytest_failure
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.engine import ENGINE_VERSION
from hermes_tameru_plugin.recovery import EXPAND_SCHEMA, EXPAND_TOOL_NAME
from hermes_tameru_plugin.render import make_ref, parse_header

TASK = "tests/test_orders.py::test_reserve_stock_rollback failed"


class _Db:
    """A usable session store: reads history, commits compactions."""

    def __init__(self, rows=()):
        self.rows, self.asked = list(rows), []

    def get_messages(self, session_id, include_compacted=False):
        self.asked.append((session_id, include_compacted))
        return self.rows

    def archive_and_compact(self, *args, **kwargs):
        return None


class OpaqueDb:
    """What ``plugins.isolation: host`` hands an engine."""

    def __getattr__(self, name):
        return None   # no capability is callable


def test_status_extends_hermes_dict():
    engine = make_engine()
    status = engine.get_status()
    for key in ("last_prompt_tokens", "threshold_tokens", "context_length", "usage_percent", "compression_count"):
        assert key in status
    info = status["tameru"]
    assert info["engine_version"] == ENGINE_VERSION and info["version"]
    assert info["enabled"] is True and info["expand_injected"] is False
    assert set(info["seams"]) == {"demote", "pressure", "lean_tail", "augment", "compress_kw", "reasons"}
    assert info["seams"]["demote"] is True
    assert info["store"] == {"entries": 0, "chars": 0}
    assert info["isolated"] is False and info["warnings"] == []
    json.dumps(status)   # plain data: the status goes to the UI


def test_status_counts_the_store_and_telemetry():
    engine = make_engine()
    chat = Chat(TASK)
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    demote(engine, chat.finish(), idx)
    info = engine.get_status()["tameru"]
    assert info["store"]["entries"] == 1 and info["store"]["chars"] > 1000
    assert info["telemetry"]["events"]["render"]["labels"]["rung"] == {"extract": 1}
    assert info["telemetry"]["chars"]["saved"] > 0


def test_status_flags_an_isolated_session_store():
    engine = make_engine()
    assert engine.get_status()["tameru"]["isolated"] is False   # nothing bound yet
    engine.bind_session_state(session_db=OpaqueDb(), session_id="s1")
    assert engine.get_status()["tameru"]["isolated"] is True
    engine.bind_session_state(session_db=_Db(), session_id="s1")
    assert engine.get_status()["tameru"]["isolated"] is False


def test_status_carries_load_warnings():
    engine = make_engine()
    engine._warnings.append("tameru.min_savings: bad value")
    assert engine.get_status()["tameru"]["warnings"] == ["tameru.min_savings: bad value"]


def test_expand_schema_is_injected_first_and_flagged():
    engine = make_engine()
    schemas = engine.get_tool_schemas()
    assert schemas[0] == EXPAND_SCHEMA and schemas[0]["name"] == EXPAND_TOOL_NAME
    assert engine.get_status()["tameru"]["expand_injected"] is True
    assert engine.get_tool_schemas() == schemas   # static: cache-safe


def test_expand_schema_is_withheld_when_off():
    for settings in (TameruSettings(expand_tool=False), TameruSettings(enabled=False)):
        engine = make_engine(settings)
        assert all(s.get("name") != EXPAND_TOOL_NAME for s in engine.get_tool_schemas())
        assert engine.get_status()["tameru"]["expand_injected"] is False


def test_handle_tool_call_expands_a_stored_original_and_defers_other_tools():
    engine = make_engine()
    chat = Chat(TASK)
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    _, out = demote(engine, chat.finish(), idx)
    ref = parse_header(out[idx]["content"])["ref"]
    body = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref, "grep": "test_reserve_stock_rollback"}))
    assert body["ref"] == ref and "test_reserve_stock_rollback" in body["content"]
    other = json.loads(engine.handle_tool_call("no_such_tool", {}))
    assert "error" in other
    miss = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": "00000000"}))
    assert "error" in miss and "session_search" in miss["hint"]


def test_expand_falls_back_to_the_session_archive():
    original = hp.terminal_result("line one\nline two\nline three " * 20)
    row = {"role": "tool", "tool_call_id": "call_9", "content": original, "_tool_call_uid": "uid-9"}
    db = _Db([row])
    engine = make_engine()
    engine.bind_session_state(session_db=db, session_id="s1")
    ref = make_ref(row, original)
    body = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref, "start_line": 1, "end_line": 1}))
    assert "error" not in body and "line one" in body["content"]
    assert db.asked and db.asked[0][0] == "s1"


def test_expand_never_reads_an_isolated_store():
    engine = make_engine()
    engine.bind_session_state(session_db=OpaqueDb(), session_id="s1")
    miss = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": "00000000"}))
    assert "error" in miss


def test_session_reset_and_end_clear_the_originals():
    engine = make_engine()
    for hook in (engine.on_session_reset, lambda: engine.on_session_end("s1", [])):
        chat = Chat(TASK)
        idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
        demote(engine, chat.finish(), idx)
        assert len(engine._store) == 1 and len(engine._memo) >= 1
        hook()
        assert len(engine._store) == 0 and len(engine._memo) == 0


def test_identity():
    engine = make_engine()
    assert engine.name == "tameru" and engine.DISPLAY_NAME == "Tameru (貯める)"
    message = engine.get_automatic_compaction_status_message(phase="start", default_message="working")
    assert message == "🗜️ Tameru (貯める) compaction — working"


def _turns():
    chat = Chat(TASK)
    chat.tool("read_file", {"path": "orders/service.py"}, hp.read_file_result("def f():\n    pass\n"))
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    return chat.messages


def test_the_artifact_trail_joins_the_summary_when_the_ledger_is_on():
    engine = make_engine(TameruSettings(ledger=True))
    engine.tail_mode = "lean"
    summary = engine._augment_summary_lean("## Goal\nfix it", _turns())
    assert "## Artifact Trail (Tameru, exact)" in summary and "orders/service.py" in summary
    assert "pytest -q" in summary and "exit 1" in summary
    assert "User asked:" not in summary and summary.count("## Artifact Trail") == 1
    again = engine._augment_summary_lean(summary, _turns())
    assert again.count("## Artifact Trail") == 1 and len(again) <= len(summary) + 50


def test_the_ledger_is_on_by_default_and_off_when_disabled():
    default = make_engine(TameruSettings())
    default.tail_mode = "lean"
    assert "Artifact Trail" in default._augment_summary_lean("## Goal\nfix it", _turns())
    for settings in (TameruSettings(ledger=False), TameruSettings(enabled=False, ledger=True)):
        engine = make_engine(settings)
        engine.tail_mode = "lean"
        assert "Artifact Trail" not in engine._augment_summary_lean("## Goal\nfix it", _turns())
