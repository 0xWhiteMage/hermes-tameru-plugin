"""``tameru_expand`` through Hermes' real tool-injection and session-archive paths (real Hermes only).

The engine is loaded from a temp ``HERMES_HOME/plugins/tameru`` and built by ``_build_context_engine``; the
schema reaches the agent through ``_inject_context_engine_tools``; the call goes to ``handle_tool_call`` the
way ``tool_executor`` dispatches context-engine tools (``handle_tool_call(name, args, messages=...)``).
"""
from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import pytest
from engine_support import Chat, fake_summary_llm, pytest_failure, server_log, use_archive_commit
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.recovery import EXPAND_SCHEMA, EXPAND_TOOL_NAME, READ_ORIGINAL_TOOL_NAME
from hermes_tameru_plugin.render import is_tameru_rendered, make_ref, parse_header

pytestmark = pytest.mark.real_hermes

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "hermes_tameru_plugin"
CONFIG = """\
context:
  engine: tameru
compression:
  protect_last_n: 4
  proactive_prune_tokens: 1000
  proactive_prune_min_result_chars: 200
  proactive_prune_min_reclaim_tokens: 0
"""


def config(**settings) -> str:
    """The base config plus ``plugins.entries.tameru.settings``. These tests drive the fixed prune gates above, so they
    opt in to the legacy policy; adaptive (the default everywhere) has its own trigger."""
    merged = {"policy_mode": "legacy", **settings}
    lines = "".join(
        f"        {name}: {str(value).lower() if isinstance(value, bool) else value}\n" for name, value in merged.items())
    return CONFIG + "plugins:\n  entries:\n    tameru:\n      settings:\n" + lines


# ---- helpers ------------------------------------------------------------------------------------
def build_agent(session_db=None, *, enabled_toolsets=None, tools=None):
    """An agent namespace whose context engine Hermes built from the installed plugin (not yet tool-injected)."""
    from agent import agent_init
    from hermes_cli.config import load_config

    cfg = load_config()
    agent = types.SimpleNamespace(
        model="test-model", provider="", base_url="", api_key="", api_mode="chat_completions",
        quiet_mode=True, session_id="sess-1", platform="cli", _compression_threshold_autoraised=None,
        tools=[] if tools is None else tools, valid_tool_names=set(), enabled_toolsets=enabled_toolsets,
    )
    agent_init._build_context_engine(agent, cfg, agent_init._parse_compression_config(agent, cfg), [], 128_000, session_db)
    return agent


@pytest.fixture
def home(hermes_home):
    (hermes_home / "plugins").mkdir()
    shutil.copytree(PLUGIN_DIR, hermes_home / "plugins" / "tameru", ignore=shutil.ignore_patterns("__pycache__"))
    (hermes_home / "config.yaml").write_text(config(), encoding="utf-8")
    try:
        yield hermes_home
    finally:
        for name in [m for m in sys.modules if m.startswith("_hermes_user_context_engine")]:
            del sys.modules[name]


def long_log() -> str:
    """Numbered lines so line ranges are easy to assert on; line 200 is the one that matters."""
    rows = [f"step {i:03d}: ok" for i in range(1, 200)]
    rows.append("step 200: FAILED hard (code 17)")
    return "\n".join(rows)


def pruned_session(engine, content: str | None = None) -> tuple[list[dict], str, str]:
    """Prune a session whose first tool result is ``content``; ``(rows, ref, original_content)``."""
    content = content if content is not None else hp.terminal_result(long_log(), exit_code=1)
    chat = Chat("why did the job fail?")
    chat.tool("terminal", {"command": "run-job.sh"}, content)
    messages = chat.finish("what failed?")
    out, n = engine.prune_tool_results_only(messages, current_tokens=60_000)
    assert n > 0
    row = next(m for m in out if m["role"] == "tool")["content"]
    assert is_tameru_rendered(row)
    return out, parse_header(row)["ref"], content


class RecordingDB:
    """Opaque-free stand-in for ``SessionDB`` that archives nothing and finds nothing."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, bool]] = []

    def archive_and_compact(self, session_id, messages, **kwargs):
        return None

    def get_messages(self, session_id, include_compacted=False):
        self.asked.append((session_id, include_compacted))
        return []


# ---- schema injection ---------------------------------------------------------------------------
def test_the_schema_is_injected_through_hermes_inject_context_engine_tools(home):
    from agent import agent_init

    agent = build_agent()
    agent_init._inject_context_engine_tools(agent)
    names = [t["function"]["name"] for t in agent.tools]
    assert names == [READ_ORIGINAL_TOOL_NAME]
    schema = agent.tools[0]["function"]
    assert schema["parameters"] == EXPAND_SCHEMA["parameters"] and "ref" in schema["parameters"]["required"]
    assert READ_ORIGINAL_TOOL_NAME in agent.valid_tool_names
    assert agent._context_engine_tool_names == {READ_ORIGINAL_TOOL_NAME}
    assert agent.context_compressor._expand_injected is True
    assert agent.context_compressor.get_status()["tameru"]["expand_injected"] is True


def test_injection_is_deduplicated_and_gated_on_enabled_toolsets(home):
    from agent import agent_init

    existing = [{"type": "function", "function": {"name": READ_ORIGINAL_TOOL_NAME, "parameters": {"type": "object"}}},
                {"type": "function", "function": {"name": EXPAND_TOOL_NAME, "parameters": {"type": "object"}}}]
    agent = build_agent(tools=list(existing))
    agent_init._inject_context_engine_tools(agent)
    assert agent.tools == existing                                  # already registered: not appended twice

    gated = build_agent(enabled_toolsets=["memory"])
    agent_init._inject_context_engine_tools(gated)
    assert gated.tools == [] and gated._context_engine_tool_names == set()   # no leak past platform_toolsets

    allowed = build_agent(enabled_toolsets=["context_engine"])
    agent_init._inject_context_engine_tools(allowed)
    assert [t["function"]["name"] for t in allowed.tools] == [READ_ORIGINAL_TOOL_NAME]


def test_expand_tool_can_be_switched_off_in_the_settings(hermes_home):
    from agent import agent_init

    (hermes_home / "plugins").mkdir()
    shutil.copytree(PLUGIN_DIR, hermes_home / "plugins" / "tameru", ignore=shutil.ignore_patterns("__pycache__"))
    (hermes_home / "config.yaml").write_text(
        CONFIG + "plugins:\n  entries:\n    tameru:\n      settings:\n        expand_tool: false\n", encoding="utf-8")
    try:
        agent = build_agent()
        agent_init._inject_context_engine_tools(agent)
        assert agent.tools == [] and agent.context_compressor._expand_injected is False
    finally:
        for name in [m for m in sys.modules if m.startswith("_hermes_user_context_engine")]:
            del sys.modules[name]


# ---- round trip on a rendered ref ---------------------------------------------------------------
def test_handle_tool_call_round_trips_a_rendered_ref(home):
    agent = build_agent(session_db=RecordingDB())
    engine = agent.context_compressor
    out, ref, content = pruned_session(engine)

    # the dispatch Hermes' tool_executor uses for names in _context_engine_tool_names
    result = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref, "start_line": 200, "end_line": 200}, messages=out))
    assert result["ref"] == ref and result["content"] == "step 200: FAILED hard (code 17)"
    assert result["returned_lines"] == [200, 200] and result["total_lines"] == 200 and result["truncated"] is False

    grep = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref, "grep": "failed"}))
    assert "step 200: FAILED hard" in grep["content"]
    assert "error" in json.loads(engine.handle_tool_call("some_other_tool", {}))   # other names reach the parent


def test_a_clone_of_the_engine_answers_from_its_own_store_only(home):
    engine = build_agent(session_db=RecordingDB()).context_compressor
    _, ref, _ = pruned_session(engine)
    clone = engine.clone_for_agent()
    miss = json.loads(clone.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref}))
    assert "error" in miss and "ref not found" in miss["error"]
    assert "content" in json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref}))


# ---- the miss hint ------------------------------------------------------------------------------
def test_a_miss_points_at_session_search_with_the_session_id(home):
    db = RecordingDB()
    engine = build_agent(session_db=db).context_compressor
    out = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": "deadbeef"}))
    assert "ref not found" in out["error"]
    assert "session_search(" in out["hint"] and "role_filter='tool'" in out["hint"] and "sess-1" in out["hint"]
    assert db.asked == [("sess-1", True)], "the archive was consulted, including compacted rows"


@pytest.mark.parametrize("args", [{}, {"ref": "not-hex"}, {"ref": "deadbeef", "start_line": "x"}, {"ref": "deadbeef", "grep": "("}])
def test_bad_arguments_return_an_error_json(home, args):
    engine = build_agent().context_compressor
    out = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, args))
    assert "error" in out


# ---- session-DB fallback (a real SessionDB) -----------------------------------------------------
def _persist(db, session_id: str, tool_content: str) -> list[dict]:
    """Flush ``user, assistant(tool call), tool`` rounds like Hermes does; the live conversation comes back."""
    db.create_session(session_id, "cli")
    db.append_message(session_id, "user", "why did the job fail?")
    calls = [("call_1", "run-job.sh", tool_content)] + [
        (f"call_{i}", f"echo filler {i}", hp.terminal_result("ok")) for i in range(2, 7)
    ]
    for call_id, command, content in calls:
        db.append_messages_batch(session_id, [
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": "terminal", "arguments": json.dumps({"command": command})}}]},
            {"role": "tool", "content": content, "tool_name": "terminal", "tool_call_id": call_id},
        ])
    db.append_message(session_id, "user", "what failed?")
    return db.get_messages_as_conversation(session_id)


def test_the_original_is_served_from_the_session_archive_after_the_store_is_gone(home, tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    try:
        agent = build_agent(session_db=db)
        engine = agent.context_compressor
        original = hp.terminal_result(long_log(), exit_code=1)
        conversation = _persist(db, "sess-1", original)

        pruned, n = engine.prune_tool_results_only(conversation, current_tokens=60_000)   # a real commit
        assert n > 0 and pruned is not conversation
        live = [m["content"] for m in db.get_messages("sess-1") if m["role"] == "tool"]
        assert any(is_tameru_rendered(c) for c in live), "Hermes archived the original and kept the render"
        ref = parse_header(next(c for c in live if is_tameru_rendered(c)))["ref"]

        engine._store.clear()                       # a new process: nothing in memory, the archive remains
        assert ref not in engine._store
        out = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref, "start_line": 200}))
        assert out["content"] == "step 200: FAILED hard (code 17)" and out["total_lines"] == 200

        miss = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": "00000000"}))
        assert "ref not found" in miss["error"] and "sess-1" in miss["hint"]
        assert engine.get_status()["tameru"]["isolated"] is False
    finally:
        db.close()


def test_an_isolated_session_db_proxy_degrades_to_the_store_only(home):
    class OpaqueSessionDB:
        """An isolation proxy: no ``get_messages`` / ``archive_and_compact``, so the archive is unreachable."""

    engine = build_agent(session_db=OpaqueSessionDB()).context_compressor
    assert engine.get_status()["tameru"]["isolated"] is True
    miss = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": "deadbeef"}))
    assert "ref not found" in miss["error"]


def test_refs_of_secret_bearing_results_are_not_stored_and_expand_says_so_via_the_hint(home):
    engine = build_agent(session_db=RecordingDB()).context_compressor
    secret = hp.terminal_result(long_log() + "\nOPENAI_API_KEY=sk-" + "a1b2c3d4" * 6, exit_code=1)
    out, ref, _ = pruned_session(engine, secret)
    assert ref not in engine._store
    miss = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref}))
    assert "error" in miss and "hint" in miss


def test_expand_returns_exact_original_lines_for_a_log_row(home):
    engine = build_agent(session_db=RecordingDB()).context_compressor
    chat = Chat("why did the payment gateway time out?")
    log = server_log(3)
    chat.tool("terminal", {"command": "tail -n 450 app.log"}, log)
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    out, n = engine.prune_tool_results_only(chat.finish("what failed?"), current_tokens=60_000)
    assert n >= 2
    row = next(m for m in out if m["role"] == "tool")["content"]
    ref = parse_header(row)["ref"]
    full = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref, "max_chars": 50_000}))
    inner = json.loads(log)["output"]
    assert full["content"] == inner and full["total_lines"] == inner.count("\n") + 1


@pytest.mark.real_hermes
def test_a_failed_db_commit_commits_no_cache_state(home, tmp_path, monkeypatch):
    """Native DB persistence failure: the declined prune leaves the live store,
    memo and input untouched; the retry commits for real."""
    from hermes_state import SessionDB
    from hermes_tameru_plugin.payload import parse_payload

    (home / "config.yaml").write_text(config(store_max_entries=1), encoding="utf-8")
    db = SessionDB(tmp_path / "state.db")
    try:
        agent = build_agent(session_db=db)
        engine = agent.context_compressor
        use_archive_commit(monkeypatch, engine)
        old_raw = hp.terminal_result(long_log(), exit_code=1)
        conversation = _persist(db, "sess-1", old_raw)
        pruned, n = engine.prune_tool_results_only(conversation, current_tokens=10**6)
        assert n > 0 and pruned is not conversation
        live = [m["content"] for m in db.get_messages("sess-1") if m["role"] == "tool"]
        old_ref = parse_header(next(c for c in live if is_tameru_rendered(c)))["ref"]
        old_inner = parse_payload(old_raw).inner
        assert engine._store.peek(old_ref) is not None

        # a fresh eligible round, persisted durably before the failed prune
        new_raw = hp.terminal_result(long_log() + "\nsecond marker", exit_code=1)
        rows = [{"role": "assistant", "content": "I will check the next run."},
                {"role": "user", "content": "Check the second run."}]
        ids = [("second-run", new_raw)] + [
            (f"second-fill-{i}", hp.terminal_result("ok")) for i in range(6)]
        for call_id, content in ids:
            rows += [
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "terminal", "arguments": json.dumps({"command": call_id})}}]},
                {"role": "tool", "content": content, "tool_name": "terminal", "tool_call_id": call_id},
            ]
        rows.append({"role": "user", "content": "What changed?"})
        db.append_messages_batch("sess-1", rows)
        convo = db.get_messages_as_conversation("sess-1")
        new_row = next(m for m in convo if m.get("tool_call_id") == "second-run")
        new_ref = make_ref(new_row, new_raw)
        new_inner = parse_payload(new_raw).inner
        snapshot_db = db.get_messages("sess-1")
        snapshot_in = json.dumps(convo, sort_keys=True)
        memo_before = len(engine._memo._items)
        chars_before = engine._telemetry.snapshot()["chars"]

        failures = []
        real_commit = db.archive_and_compact

        def reject(*a, **k):
            failures.append(1)
            raise OSError("simulated persistence failure")

        db.archive_and_compact = reject
        res = engine.prune_tool_results_only(convo, current_tokens=10**6)
        assert res[0] is convo and res[1] == 0
        assert failures == [1], "persistence failure must not trigger a stronger second commit attempt"
        assert db.get_messages("sess-1") == snapshot_db
        assert json.dumps(convo, sort_keys=True) == snapshot_in
        assert engine._store.peek(old_ref) is not None and engine._store.peek(new_ref) is None
        assert json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": old_ref}))["content"] == old_inner
        assert len(engine._memo._items) == memo_before
        assert engine._telemetry.snapshot()["chars"] == chars_before

        db.archive_and_compact = real_commit
        res2 = engine.prune_tool_results_only(convo, current_tokens=10**6)
        assert res2[0] is not convo and res2[1] > 0
        live2 = [m["content"] for m in db.get_messages("sess-1") if m["role"] == "tool"]
        assert any(is_tameru_rendered(c) and new_ref in c for c in live2), "the new ref landed in the real DB"
        assert engine._store.peek(new_ref) is not None
        assert engine._store.peek(old_ref) is None, "cap=1: the committed retry evicts the warm entry"
        assert json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": new_ref}))["content"] == new_inner
    finally:
        db.close()


@pytest.mark.parametrize("newer_attempt", [False, True])
def test_full_commit_callback_uses_actual_rows_and_current_attempt(home, tmp_path, monkeypatch, newer_attempt):
    from agent.conversation_compression import _claim_compressor_attempt
    from hermes_state import SessionDB
    from hermes_tameru_plugin.payload import parse_payload

    fake_summary_llm(monkeypatch)
    db = SessionDB(tmp_path / "full-state.db")
    try:
        engine = build_agent(session_db=db).context_compressor
        _persist(db, "sess-1", hp.terminal_result(long_log(), exit_code=1))
        for seed in range(10, 17):
            call_id = f"full-{seed}"
            db.append_messages_batch("sess-1", [
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "terminal", "arguments": json.dumps({"command": f"tail app-{seed}.log"})},
                }]},
                {"role": "tool", "content": server_log(seed), "tool_name": "terminal", "tool_call_id": call_id},
            ])
        db.append_message("sess-1", "user", "Explain why the payment gateway timed out.")
        conversation = db.get_messages_as_conversation("sess-1")
        originals = {
            make_ref(row, row["content"]): parse_payload(row["content"]).inner
            for row in conversation if row.get("role") == "tool"
        }
        _claim_compressor_attempt(engine)
        candidate = engine.compress(conversation, 200_000, force=True)
        assert candidate != conversation and candidate[-1]["role"] == "user"
        assert engine.get_status()["tameru"]["store"]["entries"] == 0
        assert engine.activity_snapshot()["items"] == []
        candidate[-1] = dict(candidate[-1], content=candidate[-1]["content"] + "\nTODO: explain the timeout.")
        db.archive_and_compact("sess-1", candidate)
        if newer_attempt:
            _claim_compressor_attempt(engine)
        engine.on_session_start("sess-1", boundary_reason="compression", old_session_id="sess-1")
        status = engine.get_status()["tameru"]
        if newer_attempt:
            assert status["store"]["entries"] == 0
            assert status["activity"]["items"] == []
            assert status["telemetry"]["chars"]["before"] == 0
        else:
            assert status["store"]["entries"] > 0
            assert status["activity"]["items"]
            assert status["telemetry"]["chars"]["before"] > 0
            for item in status["activity"]["items"]:
                ref = item["ref"]
                assert ref in originals
                restored = json.loads(engine.handle_tool_call(
                    EXPAND_TOOL_NAME, {"ref": ref, "max_chars": 100_000}))
                assert restored["content"] == originals[ref]
    finally:
        db.close()
