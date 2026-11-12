"""Adaptive runtime: policy-driven proactive cleanup through the real host seams."""
from __future__ import annotations

import json
import math
import time
from contextvars import copy_context
from copy import deepcopy
from functools import wraps

import pytest
from engine_support import Chat, fake_summary_llm, make_engine, pytest_failure_with_passes, server_log, use_archive_commit
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin import engine as engine_module
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.engine import TameruContextEngine
from hermes_tameru_plugin.hermes_compat import message_budget_tokens
from hermes_tameru_plugin.payload import parse_payload
from hermes_tameru_plugin.recovery import EXPAND_TOOL_NAME
from hermes_tameru_plugin.render import make_ref

pytestmark = pytest.mark.real_hermes
TASK = "Investigate the failed deploy and the flaky suite"


def _engine(monkeypatch, *, engine_type=TameruContextEngine, **settings_kw):
    """A real compressor with a real window; the user's real config file is isolated away."""
    monkeypatch.setattr(engine_module, "read_hermes_config", lambda: ({}, {}))
    settings = TameruSettings(input_budget_tokens=128_000, output_reserve_tokens=0,
                              **settings_kw)
    engine = engine_type(
        settings=settings, config_context_length=128_000, threshold_percent=0.75,
        quiet_mode=True, protect_last_n=4, proactive_prune_min_reclaim_tokens=0,
    )
    engine.bind_session_state(session_db=None, session_id="s1")
    return engine


def _history(n_logs: int = 8) -> list[dict]:
    chat = Chat(TASK)
    for seed in range(1, n_logs + 1):
        chat.tool("terminal", {"command": f"server-log {seed}"}, server_log(seed))
    return chat.finish()


def test_host_zero_reservation_survives_unspecified_model_updates(hermes_home, monkeypatch):
    monkeypatch.setattr(engine_module, "read_hermes_config", lambda: ({}, {}))
    unknown = TameruContextEngine(config_context_length=128_000, quiet_mode=True)
    budget = unknown.get_status()["tameru"]["policy"]["budget"]
    assert budget["window_tokens"] == 128_000
    assert budget["output_reserve_tokens"] is None
    assert budget["source"] == "unknown"

    known = TameruContextEngine(config_context_length=128_000, quiet_mode=True, max_tokens=0)
    budget = known.get_status()["tameru"]["policy"]["budget"]
    assert budget["output_reserve_tokens"] == 0
    assert budget["source"] == "host"
    known.update_model("pending", 128_000, max_tokens=None)
    assert known.get_status()["tameru"]["policy"]["budget"]["output_reserve_tokens"] == 0


def test_configured_reserve_applies_to_initial_and_switched_host_budget(hermes_home, monkeypatch):
    monkeypatch.setattr(engine_module, "read_hermes_config", lambda: ({}, {}))
    engine = TameruContextEngine(
        settings=TameruSettings(output_reserve_tokens=64_000),
        config_context_length=128_000, threshold_percent=0.75, quiet_mode=False,
    )
    # Hermes caps its absolute minimum trigger at 85% of this 64K input budget.
    assert engine.threshold_tokens == 54_400
    engine.update_model("pending", 128_000, max_tokens=16_000)
    assert engine.threshold_tokens == 54_400
    budget = engine.get_status()["tameru"]["policy"]["budget"]
    assert budget["output_reserve_tokens"] == 64_000
    assert budget["source"] == "configured_reserve"


def test_input_ceiling_preserves_a_tighter_native_cap(hermes_home, monkeypatch):
    monkeypatch.setattr(engine_module, "read_hermes_config", lambda: ({}, {}))
    settings = TameruSettings(input_budget_tokens=64_000)
    limited = TameruContextEngine(
        settings=settings, config_context_length=128_000, threshold_percent=0.75, quiet_mode=True,
    )
    assert limited.threshold_tokens == 54_400
    tighter = TameruContextEngine(
        settings=settings, config_context_length=128_000, threshold_percent=0.75,
        threshold_tokens_cap=40_000, quiet_mode=True,
    )
    assert tighter.threshold_tokens == 40_000
    tighter.update_model("pending", 256_000)
    assert tighter.threshold_tokens == 40_000


def test_disabled_budget_settings_keep_stock_host_behavior(hermes_home, monkeypatch):
    from agent.context_compressor import ContextCompressor

    monkeypatch.setattr(engine_module, "read_hermes_config", lambda: ({}, {}))
    arguments = dict(model="pending", config_context_length=128_000,
                     threshold_percent=0.75, quiet_mode=True)
    stock = ContextCompressor(**arguments)
    disabled = TameruContextEngine(
        settings=TameruSettings(enabled=False, input_budget_tokens=64_000, output_reserve_tokens=64_000),
        **arguments,
    )
    assert disabled.threshold_tokens == stock.threshold_tokens
    assert disabled.max_tokens == stock.max_tokens
    assert disabled.threshold_tokens_cap == stock.threshold_tokens_cap
    budget = disabled.get_policy_status()["budget"]
    assert budget["input_limit_tokens"] is None
    assert budget["output_reserve_tokens"] is None


def test_conflicting_declared_input_and_host_reservation_decline_cleanup(hermes_home, monkeypatch):
    monkeypatch.setattr(engine_module, "read_hermes_config", lambda: ({}, {}))
    engine = TameruContextEngine(
        settings=TameruSettings(input_budget_tokens=96_000),
        config_context_length=128_000, max_tokens=64_000, quiet_mode=True,
    )
    messages = _history()
    result, count = engine.prune_tool_results_only(messages, current_tokens=1_000_000)
    assert result is messages and count == 0
    status = engine.get_policy_status()
    assert status["budget"]["output_reserve_tokens"] == 64_000
    assert status["last_check"]["reason"] == "inconsistent_input_limit"
    assert status["last_commit"] is None


def test_earlier_commits_where_later_does_not(hermes_home, monkeypatch):
    """The activation control alone moves the timing; retention and the candidate stay equal."""
    later = _engine(monkeypatch, activation="later")
    earlier = _engine(monkeypatch, activation="earlier")
    messages = _history()
    snapshot = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    schemas = deepcopy(earlier.get_tool_schemas())

    denied, denied_count = later.prune_tool_results_only(messages, current_tokens=80_000)
    assert denied is messages and denied_count == 0, "Later must not fire at 62% of a 128k host trigger"
    assert json.dumps(messages, sort_keys=True, ensure_ascii=False) == snapshot

    accepted, count = earlier.prune_tool_results_only(messages, current_tokens=80_000)
    assert count > 0 and accepted is not messages, "Earlier fires at the same point"
    assert json.dumps(messages, sort_keys=True, ensure_ascii=False) == snapshot, "caller list untouched"
    assert earlier.get_tool_schemas() == schemas


def test_a_small_rewrite_does_not_batch_under_an_invalidated_suffix(hermes_home, monkeypatch):
    """Eligible rows exist, but ~9K reclaim against a ~50K suffix stays below the share floor."""
    engine = _engine(monkeypatch, activation="earlier")
    chat = Chat(TASK)
    chat.tool("terminal", {"command": "server-log 1"}, server_log(1))
    for i in range(6):
        chat.tool("terminal", {"command": f"echo filler {i}"}, hp.terminal_result("ok"))
    chat.tool("terminal", {"command": "server-log 2"}, server_log(2))
    chat.user("check this run " + ("reference detail " * 11_400))
    messages = chat.finish()
    chars_before = engine._telemetry.snapshot()["chars"]

    res, count = engine.prune_tool_results_only(messages, current_tokens=80_000)
    assert res is messages and count == 0
    assert len(engine._store) == 0
    assert engine._telemetry.snapshot()["chars"] == chars_before
    assert engine.get_policy_status()["last_commit"] is None


@pytest.mark.real_hermes
def test_a_committed_prune_updates_policy_state_but_not_a_clone_or_reset(hermes_home, tmp_path, monkeypatch):
    """A real SessionDB commit: one persistence attempt, durable rearm, exact originals."""
    from hermes_state import SessionDB
    from agent.context_compressor import PROACTIVE_PRUNE_REARM_MODEL_CONFIG_KEY
    from hermes_tameru_plugin.payload import parse_payload

    db = SessionDB(tmp_path / "state.db")
    try:
        engine = _engine(monkeypatch, activation="earlier")
        use_archive_commit(monkeypatch, engine)
        engine.bind_session_state(session_db=db, session_id="sess-1")
        db.create_session("sess-1", "cli")
        db.append_message("sess-1", "user", TASK)
        raw = server_log(1)
        db.append_messages_batch("sess-1", [
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "terminal", "arguments": json.dumps({"command": "server-log 1"})}}]},
            {"role": "tool", "content": raw, "tool_name": "terminal", "tool_call_id": "call_1"},
        ])
        for i in range(2, 9):
            db.append_messages_batch("sess-1", [
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": f"call_{i}", "type": "function",
                    "function": {"name": "terminal",
                                 "arguments": json.dumps({"command": f"server-log {i}"})}}]},
                {"role": "tool", "content": server_log(i), "tool_name": "terminal",
                 "tool_call_id": f"call_{i}"},
            ])
        db.append_message("sess-1", "user", "what failed?")
        convo = db.get_messages_as_conversation("sess-1")
        new_row = next(m for m in convo if m.get("tool_call_id") == "call_1")
        new_ref = make_ref(new_row, raw)
        inner = parse_payload(raw).inner

        real_commit = db.archive_and_compact
        calls = []

        def counted(*a, **k):
            calls.append(1)
            return real_commit(*a, **k)

        db.archive_and_compact = counted
        res, n = engine.prune_tool_results_only(convo, current_tokens=80_000)
        assert res is not convo and n > 0
        assert calls == [1], "exactly one native persistence attempt"
        rearm = engine.get_status()["tameru"]["policy"]["rearm_body_tokens"]
        stored = db.get_session_model_config_value(
            "sess-1", PROACTIVE_PRUNE_REARM_MODEL_CONFIG_KEY)
        assert type(stored) is int
        # Storage holds Hermes' own runway; the plugin's shorter mark lives in memory only and is never written back.
        assert rearm <= stored
        resumed = _engine(monkeypatch, activation="earlier")
        resumed.bind_session_state(session_db=db, session_id="sess-1")
        assert resumed.get_status()["tameru"]["policy"]["rearm_body_tokens"] == stored
        assert engine._store.peek(new_ref) is not None
        assert json.loads(engine.handle_tool_call(
            EXPAND_TOOL_NAME, {"ref": new_ref, "max_chars": 50_000}))["content"] == inner
        assert engine.get_policy_status()["last_commit"] is not None

        engine._session_db = None            # the durable handle is not part of a clone's state
        clone = engine.clone_for_agent()
        assert len(clone._policy.growth) == 0, "growth observations never carry into a clone"
        assert clone._policy.last_commit is None
        assert clone._policy.controls == engine._policy.controls
        assert len(clone._store) == 0
        engine.on_session_reset()
        assert engine._policy.last_commit is None
        assert len(engine._store) == 0
        assert engine._policy.controls == clone._policy.controls
    finally:
        db.close()


def test_lowering_the_runway_never_writes_to_hermes_session_storage(hermes_home, monkeypatch):
    """The shortened re-prune runway stays in memory: no private durable write, no error counted, the mark still lowered."""
    def refuse(self, *args, **kwargs):
        raise AssertionError("the plugin must not write Hermes' session storage for the runway")

    monkeypatch.setattr(TameruContextEngine, "_durable_write", refuse, raising=False)
    engine = _engine(monkeypatch, activation="earlier", preference="balanced")
    messages = _history()
    pruned, count = engine.prune_tool_results_only(messages, current_tokens=80_000)
    assert pruned is not messages and count > 0
    reclaimed = message_budget_tokens(messages) - message_budget_tokens(pruned)
    native = message_budget_tokens(pruned) + max(reclaimed, engine._raw_native("proactive_prune_tokens"))
    assert 0 < engine._proactive_prune_rearm_tokens < native          # shortened below Hermes' own runway, in memory
    assert engine._telemetry.snapshot().get("errors", {}).get("regrowth", 0) == 0


def test_an_adaptive_commit_rearms_at_the_plans_regrowth_floor(hermes_home, monkeypatch):
    """The committed plan's regrowth floor, not Hermes' reclaim-sized runway, sets the next rearm."""
    armed = []
    real = TameruContextEngine._arm_regrowth

    def spy(self, pruned, gap=0):
        armed.append(gap)
        return real(self, pruned, gap)

    monkeypatch.setattr(TameruContextEngine, "_arm_regrowth", spy)
    engine = _engine(monkeypatch, activation="earlier", preference="balanced")
    messages = _history()
    pruned, count = engine.prune_tool_results_only(messages, current_tokens=80_000)
    assert pruned is not messages and count > 0
    floor = math.ceil(engine.threshold_tokens * 0.15)          # balanced regrowth share of the host trigger
    assert armed == [floor]
    reclaimed = message_budget_tokens(messages) - message_budget_tokens(pruned)
    native_trigger = engine._raw_native("proactive_prune_tokens")
    assert engine._proactive_prune_rearm_tokens < (
        message_budget_tokens(pruned) + max(reclaimed, native_trigger))


def test_selected_stage_publishes_and_its_overrides_expire(hermes_home, monkeypatch):
    from agent.context_compressor import ContextCompressor

    class DerivedEngine(TameruContextEngine):
        pass

    engine = _engine(monkeypatch, engine_type=DerivedEngine, activation="earlier")
    normal_gates = engine.proactive_prune_tokens, engine.tail_token_budget
    native_prune = ContextCompressor._prune_old_tool_results
    contexts = []

    @wraps(native_prune)
    def capture(self, *args, **kwargs):
        contexts.append(copy_context())
        return native_prune(self, *args, **kwargs)

    monkeypatch.setattr(ContextCompressor, "_prune_old_tool_results", capture)
    messages = _history()
    result, count = engine.prune_tool_results_only(messages, current_tokens=85_000)
    assert result is not messages and count > 0
    assert len(contexts) == 1, "The sufficient standard pass must stop further planning"
    status = engine.get_status()["tameru"]
    assert status["store"]["entries"] > 0
    assert status["activity"]["items"]
    assert status["policy"]["last_commit"]["stage"] == "standard"
    assert contexts[0].run(
        lambda: (engine.proactive_prune_tokens, engine.tail_token_budget)
    ) == normal_gates


def test_stage_retention_reaches_the_actual_old_tool_body(hermes_home, monkeypatch):
    engine = _engine(
        # The marker line alone is 592 characters, so the extract budget must hold it plus the row's header and the
        # Hermes marker: 400 never could (the retained row is then a one-line brief without the path).
        monkeypatch, activation="earlier", max_extract_chars=1200, brief_chars=1200,
        errors_row_chars=0, preference="preserve_detail",
    )
    use_archive_commit(monkeypatch, engine)
    witness = "DEPLOY_MARKER path=/releases/" + "/".join(
        f"segment{i:02d}" for i in range(55)
    ) + "/manifest.lock"
    task = "Find the complete DEPLOY_MARKER path in log1; preserve the exact path."
    chat = Chat(task)
    first = None
    for seed in range(8):
        lines = [
            f"INFO record{i:04d}: worker {seed} heartbeat received"
            for i in range(700)
        ]
        lines.insert(350, witness)
        index = chat.tool(
            "terminal", {"command": f"cat log{seed + 1}"}, hp.terminal_result("\n".join(lines)),
        )
        if first is None:
            first = index
    messages = chat.finish(task)
    original = messages[first]["content"]
    result, count = engine.prune_tool_results_only(messages, current_tokens=80_000)
    assert count > 0
    assert result[first]["content"] != original
    assert witness in result[first]["content"]
    assert len(result[first]["content"]) <= 1_000
    assert messages[first]["content"] == original


@pytest.mark.real_hermes
def test_a_persistence_failure_commits_nothing_and_the_retry_succeeds(hermes_home, tmp_path, monkeypatch):
    """A failed archive_and_compact is one attempt; the restored retry commits for real."""
    from hermes_state import SessionDB
    from hermes_tameru_plugin.payload import parse_payload

    db = SessionDB(tmp_path / "state.db")
    try:
        engine = _engine(monkeypatch, activation="earlier")
        use_archive_commit(monkeypatch, engine)
        engine.bind_session_state(session_db=db, session_id="sess-1")
        db.create_session("sess-1", "cli")
        db.append_messages_batch("sess-1", _history())
        convo = db.get_messages_as_conversation("sess-1")
        snapshot = json.dumps(convo, sort_keys=True)
        original = next(m for m in convo if m["role"] == "tool")
        ref = make_ref(original, original["content"])
        expected = parse_payload(original["content"]).inner
        before = engine.get_status()["tameru"]
        real_commit = db.archive_and_compact
        attempts = []

        def reject(*a, **k):
            attempts.append("failed")
            raise OSError("simulated persistence failure")

        db.archive_and_compact = reject
        res, n = engine.prune_tool_results_only(convo, current_tokens=80_000)
        assert res is convo and n == 0
        assert attempts == ["failed"]
        assert json.dumps(convo, sort_keys=True) == snapshot
        after = engine.get_status()["tameru"]
        assert after["policy"]["last_commit"] is None
        assert after["store"] == before["store"]
        assert after["telemetry"]["chars"] == before["telemetry"]["chars"]
        assert after["activity"]["items"] == before["activity"]["items"] == []

        def commit(*a, **k):
            attempts.append("succeeded")
            return real_commit(*a, **k)

        db.archive_and_compact = commit
        result, count = engine.prune_tool_results_only(convo, current_tokens=80_000)
        assert result is not convo and count > 0
        assert attempts == ["failed", "succeeded"]
        assert engine.get_status()["tameru"]["policy"]["last_commit"] is not None
        recovered = json.loads(engine.handle_tool_call(
            EXPAND_TOOL_NAME, {"ref": ref, "max_chars": 50_000}))
        assert recovered["content"] == expected
        assert json.dumps(convo, sort_keys=True) == snapshot
    finally:
        db.close()


@pytest.mark.parametrize("current_tokens", [85_000, 1_000_000])
def test_reset_during_pruning_cannot_commit_an_old_candidate(
    hermes_home, tmp_path, monkeypatch, current_tokens,
):
    from agent.context_compressor import ContextCompressor
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "reset.db")
    try:
        db.create_session("reset", "cli")
        db.append_messages_batch("reset", _history())
        engine = _engine(monkeypatch, activation="earlier")
        engine.bind_session_state(db, "reset")
        messages = db.get_messages_as_conversation("reset")
        real_commit = db.archive_and_compact
        commits = []

        def commit(*args, **kwargs):
            commits.append(True)
            return real_commit(*args, **kwargs)

        db.archive_and_compact = commit
        native_prune = ContextCompressor._prune_old_tool_results
        resets = []

        @wraps(native_prune)
        def reset_after_stage(self, *args, **kwargs):
            result = native_prune(self, *args, **kwargs)
            if not resets:
                resets.append(True)
                self.on_session_reset()
            return result

        monkeypatch.setattr(ContextCompressor, "_prune_old_tool_results", reset_after_stage)
        result, count = engine.prune_tool_results_only(messages, current_tokens=current_tokens)
        assert resets == [True]
        assert result is messages and count == 0
        assert commits == []
        status = engine.get_status()["tameru"]
        assert status["store"]["entries"] == 0
        assert status["activity"]["items"] == []
        assert status["policy"]["last_commit"] is None
        assert status["policy"]["last_check"] is None
    finally:
        db.close()


def test_auto_full_uses_local_finalization_and_waits_for_adoption(hermes_home, monkeypatch):
    import agent.context_compressor as cc
    fake_summary_llm(monkeypatch)
    fake_call = cc.call_llm
    calls = []
    def observe(*args, **kwargs):
        calls.append(True)
        return fake_call(*args, **kwargs)
    monkeypatch.setattr(cc, 'call_llm', observe)
    engine = _engine(monkeypatch)
    messages = _history()
    before = deepcopy(messages)
    schemas = deepcopy(engine.get_tool_schemas())
    originals = {make_ref(m, m['content']): parse_payload(m['content']).inner
                 for m in messages if m.get('role') == 'tool'}
    candidate = engine.compress(messages, current_tokens=100_000)
    assert candidate is not messages and candidate != messages
    assert calls == [], 'Useful automatic local reclaim must not call the summary model'
    assert engine.compression_count == 1
    assert engine._last_compression_telemetry['method'] == 'tameru_local'
    assert messages == before
    assert engine.get_tool_schemas() == schemas
    status = engine.get_status()['tameru']
    assert status['store']['entries'] == 0 and status['activity']['items'] == []
    assert status['policy']['last_commit'] is None
    engine.on_turn_complete(candidate)
    status = engine.get_status()['tameru']
    assert status['policy']['last_commit']['method'] == 'local_full'
    assert status['policy']['last_commit']['stage'] == 'standard'
    assert status['store']['entries'] > 0 and status['activity']['items']
    ref = status['activity']['items'][0]['ref']
    recovered = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {'ref': ref, 'max_chars': 100_000}))
    assert recovered['content'] == originals[ref]
    committed = deepcopy(status['policy']['last_commit'])
    engine.on_turn_complete(candidate)
    assert engine.get_policy_status()['last_commit'] == committed


@pytest.mark.parametrize("kwargs,current", [
    ({}, 1_000_000),
    ({"force": True}, 100_000),
    ({"focus_topic": "Preserve the failed deploy evidence"}, 100_000),
    ({"memory_context": "Retain the investigation objective."}, 100_000),
    ({"bypass_cooldown": True}, 100_000),
])
def test_full_overhead_and_summary_directives_keep_one_native_fallback(hermes_home, monkeypatch, kwargs, current):
    import agent.context_compressor as cc
    fake_summary_llm(monkeypatch)
    engine = _engine(monkeypatch)
    messages = _history()
    before = deepcopy(messages)
    calls = {"compress": 0, "prune": 0}
    native_compress = cc.ContextCompressor.compress
    native_prune = cc.ContextCompressor._prune_old_tool_results
    @wraps(native_compress)
    def spy_compress(self, *args, **kw):
        calls["compress"] += 1
        return native_compress(self, *args, **kw)
    @wraps(native_prune)
    def spy_prune(self, *args, **kw):
        calls["prune"] += 1
        return native_prune(self, *args, **kw)
    monkeypatch.setattr(cc.ContextCompressor, "compress", spy_compress)
    monkeypatch.setattr(cc.ContextCompressor, "_prune_old_tool_results", spy_prune)
    result = engine.compress(messages, current_tokens=current, **kwargs)
    assert calls["compress"] == 1
    assert calls["prune"] <= 3, "selected preprocessing replays once, never re-runs"
    assert messages == before
    if result is not messages:
        engine.on_turn_complete(result)
        commit = engine.get_policy_status()["last_commit"]
        if commit is not None:
            assert commit["method"] == "full"


def test_owned_bodyless_full_candidate_uses_native_ledger_path(hermes_home, monkeypatch):
    import agent.context_compressor as cc
    fake_summary_llm(monkeypatch)
    engine = _engine(monkeypatch, retained_extract_budget_chars=0,
                     retained_extract_budget_ratio=0, retained_error_budget_chars=0,
                     errors_row_chars=0)
    calls = {"compress": 0}
    native_compress = cc.ContextCompressor.compress
    @wraps(native_compress)
    def spy_compress(self, *args, **kw):
        calls["compress"] += 1
        return native_compress(self, *args, **kw)
    monkeypatch.setattr(cc.ContextCompressor, "compress", spy_compress)
    messages = _history()
    before = deepcopy(messages)
    result = engine.compress(messages, current_tokens=100_000)
    assert calls["compress"] == 1
    assert messages == before
    if result is not messages:
        engine.on_turn_complete(result)
        commit = engine.get_policy_status()["last_commit"]
        if commit is not None:
            assert commit["method"] != "local_full"


def test_reset_during_full_staging_discards_the_candidate(hermes_home, monkeypatch):
    import agent.context_compressor as cc
    fake_summary_llm(monkeypatch)
    fake_call = cc.call_llm
    calls = []
    def observe(*args, **kwargs):
        calls.append(True)
        return fake_call(*args, **kwargs)
    monkeypatch.setattr(cc, "call_llm", observe)
    engine = _engine(monkeypatch)
    resets = []
    native_prune = cc.ContextCompressor._prune_old_tool_results
    @wraps(native_prune)
    def prune_then_reset(self, messages, *args, **kwargs):
        out = native_prune(self, messages, *args, **kwargs)
        if not resets:
            resets.append(True)
            self.on_session_reset()
        return out
    monkeypatch.setattr(cc.ContextCompressor, "_prune_old_tool_results", prune_then_reset)
    messages = _history()
    result = engine.compress(messages, current_tokens=100_000)
    assert resets == [True]
    assert result is messages
    assert calls == []
    status = engine.get_status()["tameru"]
    assert status["store"]["entries"] == 0 and status["activity"]["items"] == []
    assert status["policy"]["last_commit"] is None
    assert engine.compression_count == 0


def test_stale_full_entry_cannot_discard_an_existing_preparation(hermes_home, monkeypatch):
    import agent.conversation_compression as conv
    fake_summary_llm(monkeypatch)
    engine = _engine(monkeypatch)
    messages = _history()
    first = engine.compress(messages, current_tokens=100_000)
    assert first is not messages and engine.get_status()["tameru"]["store"]["entries"] == 0
    with monkeypatch.context() as mp:
        mp.setattr(conv, "_raise_if_stale_attempt",
                   lambda engine: (_ for _ in ()).throw(RuntimeError("stale attempt")))
        with pytest.raises(RuntimeError, match="stale attempt"):
            engine.compress(messages, current_tokens=100_000)
    engine.on_turn_complete(first)
    status = engine.get_status()["tameru"]
    assert status["store"]["entries"] > 0
    assert status["policy"]["last_commit"]["method"] == "local_full"


@pytest.mark.parametrize("legitimate", [False, True])
def test_compression_rotation_adopts_only_the_exact_committed_prefix(hermes_home, tmp_path, monkeypatch, legitimate):
    from hermes_state import SessionDB
    fake_summary_llm(monkeypatch)
    db = SessionDB(tmp_path / "state.db")
    try:
        engine = _engine(monkeypatch)
        db.create_session("s1", "cli")
        db.append_messages_batch("s1", _history())
        db.create_session("rotated", "cli")
        engine.bind_session_state(session_db=db, session_id="s1")
        original = db.get_messages_as_conversation("s1")
        candidate = engine.compress(original, current_tokens=100_000)
        assert candidate is not original and candidate != original
        assert engine.get_status()["tameru"]["store"]["entries"] == 0
        db.append_messages_batch("rotated", candidate if legitimate
                                 else [{"role": "user", "content": "Unrelated checkpoint"}])
        engine.on_session_start("rotated", boundary_reason="compression", old_session_id="s1")
        status = engine.get_status()["tameru"]
        if legitimate:
            assert status["store"]["entries"] > 0
            assert status["policy"]["last_commit"]["method"] == "local_full"
        else:
            assert status["store"]["entries"] == 0 and status["activity"]["items"] == []
            assert status["policy"]["last_commit"] is None
    finally:
        db.close()


@pytest.mark.parametrize("future", [False, True])
def test_accepted_prefix_accounting_includes_host_postprocessed_tail(hermes_home, monkeypatch, future):
    fake_summary_llm(monkeypatch)
    engine = _engine(monkeypatch)
    messages = _history() + [{"role": "user", "content": "Continue the deployment investigation."}]
    candidate = engine.compress(messages, current_tokens=100_000)
    installed = deepcopy(candidate)
    assert installed[-1]["role"] == "user"
    installed[-1]["content"] += "\n\nCurrent TODOs:\n- Verify rollback." + " notes" * 100
    observed = deepcopy(installed)
    if future:
        observed.append({"role": "assistant", "content": "A future answer after acceptance. " * 100})
    engine.on_turn_complete(observed)
    commit = engine.get_policy_status()["last_commit"]
    assert commit is not None
    assert (commit["estimated_reclaim_tokens"]
            == message_budget_tokens(messages) - message_budget_tokens(installed))


@pytest.mark.parametrize("mode,recent,should_prune", [
    ("adaptive", "auto", True),
    ("adaptive", "long", False),
    ("fixed", "auto", True),
    ("fixed", "long", False),
    ("legacy", "long", True),
])
def test_configured_count_tail_is_respected_by_adaptive_recent_modes(
        hermes_home, monkeypatch, mode, recent, should_prune):
    """prune_tail='count' is honored; a token tail only comes from a non-auto recent choice or a full pass."""
    monkeypatch.setattr(engine_module, "read_hermes_config", lambda: ({}, {}))
    engine = make_engine(TameruSettings(prune_tail="count", recent_protection=recent,
                                        policy_mode=mode, small_window_threshold_tokens=0))
    engine.bind_session_state(session_db=None, session_id="s1")
    chat = Chat(TASK)
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    before = deepcopy(chat.messages)
    result = engine.prune_tool_results_only(messages, current_tokens=1_000_000)
    assert chat.messages == before
    status = engine.get_policy_status()
    if should_prune:
        result_list, count = result
        assert result_list is not chat.messages and count > 0
        assert status["proactive_recent_basis"] == "messages"
        assert status["proactive_recent_messages"] == engine.protect_last_n
        if mode != "legacy":
            assert status["last_commit"]["recent_basis"] == "messages"
    else:
        assert result[0] is chat.messages and result[1] == 0
        assert status["proactive_recent_basis"] == "tokens"
        assert status["proactive_recent_messages"] is None


def _observation_engine(monkeypatch, **kwargs):
    monkeypatch.setattr(engine_module, "read_hermes_config", lambda: ({}, {}))
    settings_fields = {"output_reserve_tokens", "input_budget_tokens", "policy_mode",
                       "recent_protection", "activation", "preference", "prune_tail",
                       "retained_extract_budget_chars", "retained_extract_budget_ratio",
                       "retained_error_budget_chars", "errors_row_chars"}
    settings_kw = {key: kwargs.pop(key) for key in list(kwargs) if key in settings_fields}
    engine = TameruContextEngine(settings=TameruSettings(**settings_kw),
                                 config_context_length=128_000, quiet_mode=True, **kwargs)
    engine.bind_session_state(session_db=None, session_id="obs")
    return engine


def _observe(engine, **overrides):
    kwargs = dict(session_id="obs", lifecycle_id=engine._activity.lifecycle_id,
                  model=getattr(engine, "model", None), provider=getattr(engine, "provider", None),
                  base_url=getattr(engine, "base_url", None), api_mode=getattr(engine, "api_mode", None),
                  started_at=time.time(),
                  request={"body": {"model": getattr(engine, "model", None), "max_tokens": 32_000}})
    kwargs.update(overrides)
    return engine.observe_api_request(**kwargs)


def test_request_observation_informs_budget_without_mutating_native(hermes_home, monkeypatch):
    engine = _observation_engine(monkeypatch)
    schemas = deepcopy(engine.get_tool_schemas())
    native_state = (engine.max_tokens, engine.threshold_tokens,
                    engine.tail_token_budget, engine.proactive_prune_tokens)
    assert engine.get_policy_status()["budget"]["source"] == "unknown"
    assert _observe(engine)
    status = engine.get_policy_status()
    assert status["budget"]["source"] == "previous_request" and status["budget"]["estimated"]
    assert status["budget"]["usable_input_tokens"] == 128_000 - 32_000
    assert status["budget"]["output_reserve_tokens"] == 32_000
    assert (engine.max_tokens, engine.threshold_tokens,
            engine.tail_token_budget, engine.proactive_prune_tokens) == native_state
    assert engine.get_tool_schemas() == schemas
    request = {"body": {"model": engine.model, "max_tokens": 32_000, "text": "NESTED_SENTINEL"}}
    frozen = deepcopy(request)
    assert _observe(engine, request=request)
    status = engine.get_policy_status()
    assert status["budget"]["source"] == "previous_request"
    assert status["budget"]["output_reserve_tokens"] == 32_000
    assert request == frozen
    assert "NESTED_SENTINEL" not in json.dumps(status)
    truncated = {"_truncated": True, "preview": "SECRET_SENTINEL"}
    assert _observe(engine, request=truncated)
    st = engine.get_policy_status()
    assert st["budget"]["source"] == "unknown" and st["budget"]["output_reserve_tokens"] is None
    assert st["last_request"]["source"] == "truncated_request"
    assert "SECRET_SENTINEL" not in json.dumps(st)
    assert not _observe(engine, started_at=st["last_request"]["started_at"] - 1)
    assert not _observe(engine, started_at=time.time() - 10**9)
    assert not _observe(engine, started_at=time.time() + 1_000)
    assert not _observe(engine, started_at=True)
    assert not _observe(engine, started_at=float("nan"))


@pytest.mark.parametrize("cap,expected", [(0, 128_000), (32_000, 128_000 - 32_000)])
def test_request_observation_cap_values(hermes_home, monkeypatch, cap, expected):
    engine = _observation_engine(monkeypatch)
    assert _observe(engine, request={"body": {"max_tokens": cap}})
    budget = engine.get_policy_status()["budget"]
    assert budget["output_reserve_tokens"] == cap and budget["usable_input_tokens"] == expected


@pytest.mark.parametrize("cap", [True, -1, 1.5, None])
def test_request_observation_invalid_caps_are_unknown_not_zero(hermes_home, monkeypatch, cap):
    engine = _observation_engine(monkeypatch)
    assert _observe(engine, request={"body": {"max_tokens": cap}})
    budget = engine.get_policy_status()["budget"]
    assert budget["output_reserve_tokens"] is None and budget["usable_input_tokens"] is None


@pytest.mark.parametrize("alias", ["max_completion_tokens", "max_output_tokens"])
def test_request_observation_cap_aliases(hermes_home, monkeypatch, alias):
    engine = _observation_engine(monkeypatch)
    assert _observe(engine, request={"body": {alias: 40_000}})
    assert engine.get_policy_status()["budget"]["output_reserve_tokens"] == 40_000


def test_request_observation_conflicting_caps_are_unknown(hermes_home, monkeypatch):
    engine = _observation_engine(monkeypatch)
    assert _observe(engine, request={"body": {"max_tokens": 10_000, "max_completion_tokens": 10_000}})
    assert engine.get_policy_status()["budget"]["output_reserve_tokens"] == 10_000
    assert _observe(engine, request={"body": {"max_tokens": 10_000, "max_completion_tokens": 20_000}})
    assert engine.get_policy_status()["budget"]["output_reserve_tokens"] is None


@pytest.mark.parametrize("payload", [
    {"body": {"max_tokens": 20_000, "extra_body": {"max_tokens": 20_000}}},
    {"body": {"_truncated_items": ["max_tokens"]}},
    {"body": {"max_tokens": "32000 tokens"}},
])
def test_request_observation_malformed_payloads_are_unknown(hermes_home, monkeypatch, payload):
    engine = _observation_engine(monkeypatch)
    assert _observe(engine, request=payload)
    assert engine.get_policy_status()["budget"]["output_reserve_tokens"] is None


def test_explicit_budgets_beat_observation(hermes_home, monkeypatch):
    for kw, expected in [({"output_reserve_tokens": 32_000}, ("configured_reserve", 32_000, False)),
                         ({"input_budget_tokens": 96_000}, ("configured_input", None, False)),
                         ({"max_tokens": 32_000}, ("host", 32_000, False)),
                         ({"max_tokens": 0}, ("host", 0, False))]:
        engine = _observation_engine(monkeypatch, **kw)
        assert _observe(engine, request={"body": {"max_tokens": 64_000}})
        budget = engine.get_policy_status()["budget"]
        source, reserve, estimated = expected
        assert budget["source"] == source and budget["estimated"] == estimated
        assert budget["output_reserve_tokens"] == reserve
        if source == "configured_input":
            assert budget["usable_input_tokens"] == 96_000
            assert budget["invalid_reason"] is None
        assert engine.get_policy_status()["last_request"]["output_reserve_tokens"] == 64_000


def test_observation_lifecycle_reset_and_replay_rules(hermes_home, monkeypatch):
    engine = _observation_engine(monkeypatch)
    assert _observe(engine)
    old_lifecycle = engine._activity.lifecycle_id
    old_started = engine.get_policy_status()["last_request"]["started_at"]
    clone = engine.clone_for_agent()
    assert clone.get_policy_status()["last_request"] is None
    time.sleep(0.05)      # Windows' clock ticks about every 15 ms: the reset must be strictly later than the observation
    engine.on_session_reset()
    time.sleep(0.05)      # and the observations after it strictly later than the reset
    assert engine.get_policy_status()["last_request"] is None
    # A replay of the pre-reset observation cannot resurrect the cleared cap.
    assert not engine.observe_api_request(session_id="obs", lifecycle_id=old_lifecycle,
                                          model=engine.model, provider=getattr(engine, "provider", None),
                                          base_url=getattr(engine, "base_url", None),
                                          api_mode=getattr(engine, "api_mode", None),
                                          started_at=time.time(),
                                          request={"body": {"max_tokens": 32_000}})
    assert not engine.observe_api_request(session_id="obs",
                                          lifecycle_id=engine._activity.lifecycle_id,
                                          model=engine.model, provider=getattr(engine, "provider", None),
                                          base_url=getattr(engine, "base_url", None),
                                          api_mode=getattr(engine, "api_mode", None),
                                          started_at=old_started,
                                          request={"body": {"max_tokens": 32_000}})
    assert _observe(engine)

    engine2 = _observation_engine(monkeypatch)
    assert _observe(engine2)
    engine2.update_model("different-model", 64_000)
    assert engine2.get_policy_status()["last_request"] is None
    # Old-route callbacks are dropped even with a fresh timestamp.
    assert not engine2.observe_api_request(
        session_id="obs", lifecycle_id=engine2._activity.lifecycle_id, model="old-model",
        provider=getattr(engine2, "provider", None), base_url=getattr(engine2, "base_url", None),
        api_mode=getattr(engine2, "api_mode", None), started_at=time.time(),
        request={"body": {"max_tokens": 32_000}})
    assert engine2.observe_api_request(
        session_id="obs", lifecycle_id=engine2._activity.lifecycle_id, model="different-model",
        provider=getattr(engine2, "provider", None), base_url=getattr(engine2, "base_url", None),
        api_mode=getattr(engine2, "api_mode", None), started_at=time.time(),
        request={"body": {"max_tokens": 32_000}})
    assert engine2.get_policy_status()["budget"]["usable_input_tokens"] == 64_000 - 32_000


def test_ambiguous_registry_session_observes_neither_engine(hermes_home, monkeypatch):
    from hermes_tameru_plugin.activity import SessionRegistry
    registry = SessionRegistry()
    engine_a = _observation_engine(monkeypatch)
    engine_b = _observation_engine(monkeypatch)
    engine_a._activity_registry = registry
    engine_b._activity_registry = registry
    registry.bind(engine_a, "", "obs")
    registry.bind(engine_b, "", "obs")
    registry.observe_request(session_id="obs", model=engine_a.model,
                             provider=getattr(engine_a, "provider", None),
                             base_url=getattr(engine_a, "base_url", None),
                             api_mode=getattr(engine_a, "api_mode", None),
                             request={"body": {"max_tokens": 32_000}},
                             started_at=time.time())
    assert engine_a.get_policy_status()["last_request"] is None
    assert engine_b.get_policy_status()["last_request"] is None


def _session_policy_db(tmp_path):
    from hermes_state import SessionDB
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", "cli")
    db.create_session("s2", "cli")
    return db


def test_persisted_policy_controls_roundtrip_and_reset(hermes_home, monkeypatch, tmp_path):
    from hermes_tameru_plugin.session_policy import POLICY_KEY
    db = _session_policy_db(tmp_path)
    try:
        db.patch_session_model_config("s1", {"foreign_plugin": {"nested": "keep-me"}})
        engine = _engine(monkeypatch)
        engine.bind_session_state(session_db=db, session_id="s1")
        life = engine._activity.lifecycle_id
        history = _history()
        db.append_messages_batch("s1", history)
        before_messages = deepcopy(db.get_messages_as_conversation("s1"))
        schemas = deepcopy(engine.get_tool_schemas())
        native = (engine.max_tokens, engine.threshold_tokens,
                  engine.tail_token_budget, engine.proactive_prune_tokens)

        status = engine.set_policy_controls(
            {"preference": "preserve_detail", "activation": "earlier", "recent": "long"},
            session_id="s1", lifecycle_id=life)
        assert status["controls_source"] == "session"
        assert status["controls"] == {"mode": "adaptive", "preference": "preserve_detail",
                                      "activation": "earlier", "recent": "long"}
        assert db.get_messages_as_conversation("s1") == before_messages
        assert engine.get_tool_schemas() == schemas
        assert (engine.max_tokens, engine.threshold_tokens,
                engine.tail_token_budget, engine.proactive_prune_tokens) == native
        assert db.get_session_model_config_value("s1", "foreign_plugin") == {"nested": "keep-me"}

        fresh = _engine(monkeypatch)
        fresh.bind_session_state(session_db=db, session_id="s1")
        assert fresh.get_policy_status()["controls_source"] == "session"
        assert fresh.get_policy_status()["controls"]["preference"] == "preserve_detail"

        memory = _engine(monkeypatch)
        memory.set_policy_controls({"preference": "preserve_detail", "activation": "earlier",
                                    "recent": "long"}, session_id="s1",
                                   lifecycle_id=memory._activity.lifecycle_id)
        clone = memory.clone_for_agent()
        assert clone.get_policy_status()["controls"]["recent"] == "long"
        assert clone.get_policy_status()["last_check"] is None
        assert clone.get_policy_status()["last_commit"] is None
        assert clone.get_policy_status()["last_request"] is None

        engine.on_session_reset()
        assert engine.get_policy_status()["controls"]["preference"] == "preserve_detail"
        assert engine.get_policy_status()["controls_source"] == "session"

        engine.bind_session_state(session_db=db, session_id="s2")
        assert engine.get_policy_status()["controls"]["preference"] == "balanced"
        assert engine.get_policy_status()["controls_source"] == "default"
        engine.bind_session_state(session_db=db, session_id="s1")
        assert engine.get_policy_status()["controls"]["preference"] == "preserve_detail"

        status = engine.set_policy_controls({}, reset=True, session_id="s1",
                                            lifecycle_id=engine._activity.lifecycle_id)
        assert status["controls"] == {"mode": "adaptive", "preference": "balanced",
                                      "activation": "auto", "recent": "auto"}
        assert status["controls_source"] == "default"
        assert db.get_session_model_config_value("s1", POLICY_KEY) is None
        reopened = _engine(monkeypatch)
        reopened.bind_session_state(session_db=db, session_id="s1")
        assert reopened.get_policy_status()["controls"]["preference"] == "balanced"
    finally:
        db.close()


def test_policy_setter_fencing_and_write_failures(hermes_home, monkeypatch, tmp_path):
    from hermes_tameru_plugin.session_policy import POLICY_KEY, PolicyError
    db = _session_policy_db(tmp_path)
    try:
        engine = _engine(monkeypatch)
        engine.bind_session_state(session_db=db, session_id="s1")
        life = engine._activity.lifecycle_id
        with pytest.raises(PolicyError):
            engine.set_policy_controls({"preference": "save_space"},
                                       session_id="s1", lifecycle_id="stale-life")
        with pytest.raises(PolicyError):
            engine.set_policy_controls({"preference": "save_space"},
                                       session_id="other", lifecycle_id=life)
        assert engine.get_policy_status()["controls"]["preference"] == "balanced"
        assert db.get_session_model_config_value("s1", POLICY_KEY) is None

        real_patch = db.patch_session_model_config
        def raise_private(*a, **k):
            raise OSError("PRIVATE_SENTINEL")
        monkeypatch.setattr(db, "patch_session_model_config", raise_private)
        with pytest.raises(PolicyError) as excinfo:
            engine.set_policy_controls({"preference": "save_space"},
                                       session_id="s1", lifecycle_id=life)
        assert "PRIVATE_SENTINEL" not in str(excinfo.value)
        monkeypatch.setattr(db, "patch_session_model_config", real_patch)
        assert engine.get_policy_status()["controls"]["preference"] == "balanced"
        assert db.get_session_model_config_value("s1", POLICY_KEY) is None

        # A session row that does not exist cannot carry a durable override.
        orphan = _engine(monkeypatch)
        orphan.bind_session_state(session_db=db, session_id="missing-chat")
        with pytest.raises(PolicyError):
            orphan.set_policy_controls({"preference": "save_space"},
                                       session_id="missing-chat",
                                       lifecycle_id=orphan._activity.lifecycle_id)
        with pytest.raises(PolicyError):
            orphan.set_policy_controls({}, reset=True, session_id="missing-chat",
                                       lifecycle_id=orphan._activity.lifecycle_id)
        assert orphan.get_policy_status()["controls"]["preference"] == "balanced"
    finally:
        db.close()


def test_policy_change_during_full_staging_flows_to_rotated_session(hermes_home, monkeypatch, tmp_path):
    from hermes_tameru_plugin.session_policy import POLICY_KEY, PolicyError
    fake_summary_llm(monkeypatch)
    import agent.context_compressor as cc
    calls = []
    fake_call = cc.call_llm
    monkeypatch.setattr(cc, "call_llm",
                        lambda *a, **k: calls.append(True) or fake_call(*a, **k))
    db = _session_policy_db(tmp_path)
    try:
        db.create_session("child", "cli")
        db.patch_session_model_config("child", {POLICY_KEY: {
            "version": 1, "controls": {"mode": "adaptive", "preference": "preserve_detail",
                                       "activation": "auto", "recent": "auto"}}})
        engine = _engine(monkeypatch)
        engine.bind_session_state(session_db=db, session_id="s1")
        db.append_messages_batch("s1", _history())
        messages = db.get_messages_as_conversation("s1")
        life = engine._activity.lifecycle_id
        engine.set_policy_controls({"preference": "preserve_detail"},
                                   session_id="s1", lifecycle_id=life)
        candidate = engine.compress(messages, current_tokens=100_000)
        assert candidate is not messages
        assert engine.get_status()["tameru"]["store"]["entries"] == 0
        engine.set_policy_controls({"preference": "save_space"},
                                   session_id="s1", lifecycle_id=life)
        assert engine.get_status()["tameru"]["store"]["entries"] == 0
        db.append_messages_batch("child", candidate)
        engine.on_session_start("child", boundary_reason="compression", old_session_id="s1")
        assert calls == []
        assert engine._activity.lifecycle_id == life
        status = engine.get_policy_status()
        assert status["last_commit"]["method"] == "local_full"
        assert status["last_commit"]["plan"]["controls"]["preference"] == "preserve_detail"
        assert status["controls"]["preference"] == "save_space"
        assert db.get_session_model_config_value("child", POLICY_KEY)["controls"]["preference"] == "save_space"
        child = _engine(monkeypatch)
        child.bind_session_state(session_db=db, session_id="child")
        assert child.get_policy_status()["controls"]["preference"] == "save_space"
        assert engine.get_status()["tameru"]["store"]["entries"] > 0
        with pytest.raises(PolicyError):
            engine.set_policy_controls({"preference": "balanced"},
                                       session_id="s1",
                                       lifecycle_id=engine._activity.lifecycle_id)
        assert engine.get_policy_status()["controls"]["preference"] == "save_space"
        assert db.get_session_model_config_value("child", POLICY_KEY)["controls"]["preference"] == "save_space"
    finally:
        db.close()


def test_malformed_policy_records_fall_back_to_defaults(hermes_home, monkeypatch, tmp_path):
    from hermes_tameru_plugin.session_policy import POLICY_KEY
    db = _session_policy_db(tmp_path)
    try:
        valid = {"mode": "adaptive", "preference": "save_space",
                 "activation": "earlier", "recent": "short"}
        for i, record in enumerate([
                {"version": True, "controls": dict(valid)},
                {"version": 2, "controls": dict(valid)},
                {"version": 1, "controls": {"mode": "bogus", "preference": "save_space",
                                            "activation": "earlier", "recent": "short"}},
        ]):
            sid = f"bad{i}"
            db.create_session(sid, "cli")
            db.patch_session_model_config(sid, {POLICY_KEY: record})
            engine = _engine(monkeypatch)
            engine.bind_session_state(session_db=db, session_id=sid)
            status = engine.get_policy_status()
            assert status["controls"]["preference"] == "balanced"
            assert status["controls_source"] == "default"
            assert status["controls_issue"]
            assert db.get_session_model_config_value(sid, POLICY_KEY) == record
    finally:
        db.close()


def test_reentrant_bind_during_policy_patch_fails_the_fence(hermes_home, monkeypatch, tmp_path):
    from hermes_tameru_plugin.session_policy import POLICY_KEY, PolicyError
    db = _session_policy_db(tmp_path)
    try:
        engine = _engine(monkeypatch)
        engine.bind_session_state(session_db=db, session_id="s1")
        life = engine._activity.lifecycle_id
        real_patch = db.patch_session_model_config
        def patch_then_rebind(sid, patch):
            real_patch(sid, patch)
            engine.bind_session_state(session_db=db, session_id="s2")
        monkeypatch.setattr(db, "patch_session_model_config", patch_then_rebind)
        with pytest.raises(PolicyError):
            engine.set_policy_controls({"preference": "save_space"},
                                       session_id="s1", lifecycle_id=life)
        assert engine.get_policy_status()["controls"]["preference"] == "balanced"
        assert db.get_session_model_config_value("s2", POLICY_KEY) is None
    finally:
        db.close()


def test_policy_controls_without_db_and_mode_gating(hermes_home, monkeypatch):
    from hermes_tameru_plugin.session_policy import PolicyError
    engine = _engine(monkeypatch)
    life = engine._activity.lifecycle_id
    status = engine.set_policy_controls({"preference": "preserve_detail"},
                                        session_id="s1", lifecycle_id=life)
    assert status["controls_source"] == "memory"
    assert status["controls"]["preference"] == "preserve_detail"
    with pytest.raises(PolicyError):
        engine.set_policy_controls({"preference": "not-a-preference"},
                                   session_id="s1", lifecycle_id=life)
    with pytest.raises(PolicyError):
        engine.set_policy_controls({"bogus_knob": "earlier"},
                                   session_id="s1", lifecycle_id=life)
    with pytest.raises(PolicyError):
        engine.set_policy_controls({}, session_id="s1", lifecycle_id=life, reset="yes")

    legacy = _engine(monkeypatch, policy_mode="legacy")
    legacy_life = legacy._activity.lifecycle_id
    legacy.set_policy_controls({"mode": "legacy"}, session_id="s1", lifecycle_id=legacy_life)
    with pytest.raises(PolicyError):
        legacy.set_policy_controls({"recent": "long"}, session_id="s1", lifecycle_id=legacy_life)
    with pytest.raises(PolicyError):
        legacy.set_policy_controls({"mode": "legacy", "activation": "earlier"},
                                   session_id="s1", lifecycle_id=legacy_life)
    legacy.set_policy_controls({"mode": "adaptive"}, session_id="s1", lifecycle_id=legacy_life)
    legacy.set_policy_controls({"recent": "long"}, session_id="s1", lifecycle_id=legacy_life)
    assert legacy.get_policy_status()["controls"]["recent"] == "long"
