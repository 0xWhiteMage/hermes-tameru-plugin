"""Activity records, the /tameru command and the original-output viewer."""
from __future__ import annotations

import json
import time
import re
import shutil
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from engine_support import Chat, demote, make_engine, pytest_failure_with_passes, server_log
from hermes_tameru_plugin.activity import (
    ActivityState,
    SessionRegistry,
    display_text,
    digest,
    proposed_change,
)
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.payload import parse_payload
from hermes_tameru_plugin.render import make_ref, parse_header
from hermes_tameru_plugin.recovery import EXPAND_TOOL_NAME, READ_ORIGINAL_TOOL_NAME

TASK = "tests/test_orders.py::test_reserve_stock_rollback failed"


def _bound_engine(session_id="s1", settings=None, **kwargs):
    # The activity/policy command surface exercises the adaptive controls; ``make_engine`` alone defaults to legacy.
    if settings is None:
        settings = TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive")
    else:
        settings = replace(settings, policy_mode="adaptive")
    engine = make_engine(settings, **kwargs)
    engine.bind_session_state(session_db=None, session_id=session_id)
    return engine


def _pruned_chat(engine, *, expect_commit=True):
    chat = Chat(TASK)
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    chat.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    frozen = json.dumps(chat.finish(), sort_keys=True, ensure_ascii=False)
    res = engine.prune_tool_results_only(chat.messages, 10**6)
    if expect_commit:
        assert res[1] > 0 and res[0] is not chat.messages, "the old result must land outside token protection"
    assert json.dumps(chat.messages, sort_keys=True, ensure_ascii=False) == frozen  # input untouched
    return chat, res


def test_a_committed_prune_lands_in_the_snapshot_and_the_command_sees_it():
    engine = _bound_engine("s1")
    chat, res = _pruned_chat(engine)
    assert res[0] is not chat.messages

    snapshot = engine.activity_snapshot()
    assert len(snapshot["items"]) == 1
    item = snapshot["items"][0]
    assert item["tool"] == "terminal" and item["before_lines"] > item["kept_lines"]
    assert item["original"] == "memory" and item["live"] is True

    out = json.loads(engine._activity_registry.command("--json", session_id="s1"))
    assert out["ok"] and out["items"][0]["ref"] == item["ref"]

    frozen = json.dumps(res[0], sort_keys=True, ensure_ascii=False)
    view = json.loads(engine._activity_registry.command(f"--json view {item['ref']}", session_id="s1"))
    assert view["ok"] and "test_reserve_stock_rollback" in view["content"]
    assert json.dumps(res[0], sort_keys=True, ensure_ascii=False) == frozen


def test_inspect_pages_a_long_original_by_characters():
    engine = _bound_engine("s1")
    text = "x" * 30_000
    ref = make_ref({"tool_call_id": "c1"}, text)
    assert engine._store.put(ref, text)

    first = engine.inspect_original(ref)
    second = engine.inspect_original(ref, offset=12_000)
    assert first["content"] == "x" * 12_000 and first["next_offset"] == 12_000
    assert second["content"] == "x" * 12_000 and second["next_offset"] == 24_000
    assert first["total_chars"] == 30_000 and first["source"] == "memory"
    for bad in (-1, True, 30_001):
        assert "error" in engine.inspect_original(ref, offset=bad)
    assert "error" in engine.inspect_original("not-a-ref")
    assert "error" in engine.inspect_original(ref, max_chars=0)


def test_clones_have_their_own_activity_and_store():
    engine = _bound_engine("s1")
    _pruned_chat(engine)
    clone = engine.clone_for_agent()
    clone.bind_session_state(session_db=None, session_id="s2")

    registry = engine._activity_registry
    assert registry.resolve("s1") is engine and registry.resolve("s2") is clone
    assert clone.activity_snapshot()["items"] == [] and len(clone._store) == 0
    assert clone._activity.lifecycle_id != engine._activity.lifecycle_id

    ref = engine.activity_snapshot()["items"][0]["ref"]
    out = json.loads(registry.command(f"--json view {ref}", session_id="s2"))
    assert not out["ok"]
    assert SessionRegistry().resolve("s1") is None


def test_ambiguous_binding_refuses_to_resolve():
    registry = SessionRegistry()
    a, b = make_engine(), make_engine()
    a._activity_registry = b._activity_registry = registry
    a.bind_session_state(session_db=None, session_id="s1")
    b.bind_session_state(session_db=None, session_id="s1")
    assert registry.resolve("s1") is None
    out = json.loads(registry.command("--json", session_id="s1"))
    assert not out["ok"] and "No live Tameru context" in out["error"]
    out = json.loads(registry.command("--json", session_id=""))
    assert not out["ok"]


def test_a_declined_prune_records_nothing():
    engine = _bound_engine("s1", proactive_prune_min_reclaim_tokens=10**9)
    chat, res = _pruned_chat(engine, expect_commit=False)
    assert res[0] is chat.messages
    assert engine.activity_snapshot()["items"] == []


def test_status_reads_do_not_disturb_store_eviction_order():
    from dataclasses import replace as _replace

    engine = make_engine(TameruSettings(store_max_entries=2, store_max_chars=100_000,
                                        small_window_threshold_tokens=0))
    change, out = _genuine_change(engine)
    refs = []
    for name in ("a", "b"):
        text = f"{name}-text"
        ref = make_ref({"tool_call_id": name}, text)
        fixture = _replace(change, ref=ref, identity=(("tool_call_id", name),),
                           original_digest=digest(text))
        engine._activity.confirm([fixture], out, allow_missing=True)
        refs.append(ref)
    for name, ref in zip(("a", "b"), refs):
        assert engine._store.put(ref, f"{name}-text")   # final insertions before snapshot
    engine.get_status()                                # snapshot polls peek(), not entry()
    assert engine.inspect_original(refs[0])["content"] == "a-text"
    engine._store.put(make_ref({"tool_call_id": "c"}, "c-text"), "c-text")
    assert engine._store.peek(refs[0]) is None      # A evicted: polling did not refresh it
    assert engine._store.peek(refs[1]) is not None  # B still resident


def test_context_states_track_the_live_snapshot():
    engine = _bound_engine("s1")
    chat, res = _pruned_chat(engine)
    item = engine.activity_snapshot()["items"][0]
    live_rows = res[0]
    row = next(m for m in live_rows if m.get("role") == "tool"
               and isinstance(m.get("content"), str) and parse_header(m["content"]))

    engine.on_turn_complete([m for m in live_rows if m is not row])
    assert engine.activity_snapshot()["items"][0]["context_state"] == "absent"

    altered = [dict(m, content=m["content"] + "x") if m is row else m for m in live_rows]
    engine.on_turn_complete(altered)
    snap = engine.activity_snapshot()["items"][0]
    assert snap["context_state"] == "changed" and snap["live"] is False

    engine.on_turn_complete(live_rows)
    snap = engine.activity_snapshot()["items"][0]
    assert snap["context_state"] == "present" and snap["live"] is True
    assert engine.inspect_original(item["ref"])["content"]  # record still viewable


class _Archive:
    """Minimal session-owned archive: rows look like SessionDB history."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def get_messages(self, session_id, include_compacted=False, **kwargs):
        return list(self.rows)

    def archive_and_compact(self, *args, **kwargs):
        return True


def test_a_reset_during_archive_read_drops_the_old_original():
    engine = _bound_engine('s1')
    row = {'role': 'tool', 'tool_call_id': 'pending', 'content': 'ORIGINAL_BEFORE_RESET'}
    ref = make_ref(row, row['content'])

    class ResettingArchive(_Archive):
        def get_messages(self, *args, **kwargs):
            engine.on_session_reset()
            return super().get_messages(*args, **kwargs)

    engine.bind_session_state(session_db=ResettingArchive([row]), session_id='s1')
    answer = json.loads(engine._activity_registry.command('--json view ' + ref, session_id='s1'))
    assert not answer['ok']
    assert 'content' not in answer


def test_archive_availability_is_remembered_then_recycled():
    engine = make_engine()
    chat = Chat(TASK)
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    archive_row = dict(messages[idx])
    engine.bind_session_state(session_db=_Archive([archive_row]), session_id="s1")

    changed, out = demote(engine, messages, idx)
    assert changed
    original = parse_payload(archive_row["content"]).inner
    change = proposed_change(messages[idx], out[idx], "terminal",
                             original=original, previous=None)
    engine2 = make_engine()
    engine2.bind_session_state(session_db=_Archive([archive_row]), session_id="s1")
    engine2._activity.confirm([change], out)
    assert len(engine2._store) == 0                     # nothing in memory

    view = engine2.inspect_original(change.ref)
    assert view["content"] == original and view["source"] == "archive"
    assert engine2.activity_snapshot()["items"][0]["original"] == "archive"
    assert len(engine2._store) == 0                     # archive reads stay out of the store
    plain = engine2._activity_registry.command("changes", session_id="s1")
    assert "Available" in plain                        # CLI maps 'archive' too, no KeyError

    engine2 = make_engine()
    engine2.bind_session_state(session_db=_Archive(), session_id="s1")
    engine2._activity.confirm([change], out)
    assert "error" in engine2.inspect_original(change.ref)
    assert engine2.activity_snapshot()["items"][0]["original"] == "unavailable"

    engine2._activity.observe(out)                     # fresh snapshot recycles the check
    assert engine2.activity_snapshot()["items"][0]["original"] == "unchecked"


def test_unrelated_rebind_drops_the_previous_sessions_records():
    engine = _bound_engine("s1")
    _pruned_chat(engine)
    ref = engine.activity_snapshot()["items"][0]["ref"]
    engine.bind_session_state(session_db=None, session_id="s2")
    assert engine.activity_snapshot()["items"] == [] and len(engine._store) == 0
    assert engine._activity_registry.resolve("s2") is engine
    out = json.loads(engine._activity_registry.command(f"--json view {ref}", session_id="s2"))
    assert not out["ok"]


def test_compression_rotation_preserves_records_and_moves_ownership():
    engine = _bound_engine("s1")
    _pruned_chat(engine)
    ref = engine.activity_snapshot()["items"][0]["ref"]
    engine.on_session_start("s1-rotated", boundary_reason="compression", old_session_id="s1")
    assert engine.activity_snapshot()["items"][0]["ref"] == ref and len(engine._store) == 1
    registry = engine._activity_registry
    assert registry.resolve("s1-rotated") is engine and registry.resolve("s1") is None


def test_a_rebind_mid_read_discards_the_payload():
    engine = _bound_engine("s1")
    _pruned_chat(engine)
    ref = engine.activity_snapshot()["items"][0]["ref"]

    def stealing(*a, **k):
        engine.bind_session_state(session_db=None, session_id="s2")
        return {"sentinel": "never shown"}

    engine.inspect_original = stealing
    out = json.loads(engine._activity_registry.command(f"--json view {ref}", session_id="s1"))
    assert not out["ok"] and "active session changed" in out["error"]


def test_the_quiet_flag_suppresses_notices_but_not_the_command():
    engine = _bound_engine("s1")
    engine.emit_automatic_compaction_status = False
    posted = []
    engine._activity_registry.commands_available = True
    engine._activity_registry.post_message = posted.append
    _pruned_chat(engine)
    assert posted == []
    assert json.loads(engine._activity_registry.command("--json", session_id="s1"))["ok"]


def test_failed_engine_registration_creates_no_command():
    import hermes_tameru_plugin as plugin

    captured = []

    class Ctx:
        def get_config(self, name):
            raise LookupError(name)

        def register_context_engine(self, engine):
            raise RuntimeError("host rejected the engine")

        def register_command(self, *a, **k):
            captured.append((a, k))

    plugin.register(Ctx())
    assert captured == []


def _genuine_change(engine):
    chat = Chat(TASK)
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    changed, out = demote(engine, messages, idx)
    assert changed
    original = parse_payload(messages[idx]["content"]).inner
    change = proposed_change(messages[idx], out[idx], "terminal", original=original, previous=None)
    assert change is not None
    return change, out


def test_deferred_changes_land_only_on_a_matching_snapshot():
    engine = make_engine()
    change, out = _genuine_change(engine)
    state = ActivityState()
    state.defer([change], out)
    assert state.rows() == []
    assert state.observe([{"role": "tool", "content": "different"}]) == 0
    assert state.rows() == []
    assert state.observe(out + [{"role": "assistant", "content": "done"}]) == 1
    assert state.rows()[0].ref == change.ref


def test_a_forged_header_and_a_mismatched_store_entry_are_not_claimed():
    engine = make_engine()
    change, out = _genuine_change(engine)
    rendered = next(m["content"] for m in out if isinstance(m.get("content"), str)
                    and parse_header(m["content"]))
    forged_before = {"role": "tool", "tool_call_id": "call_9999", "content": "attacker body"}
    forged_after = {**forged_before, "content": rendered}
    assert proposed_change(forged_before, forged_after, "terminal", original=None, previous=None) is None

    engine._activity.confirm([change], out, allow_missing=True)
    engine._store.put(change.ref, "not the original", tool="terminal")
    assert "error" in engine.inspect_original(change.ref)


def test_display_text_escapes_controls_but_keeps_the_stored_original():
    raw = "line1\nline2\tTAB\x07BELL\u202aEMBED\u202c日本語\ud800"
    shown = display_text(raw)
    assert "\\u0007" in shown and "\\u202a" in shown and "\\ud800" in shown
    assert "line1\nline2\tTAB" in shown and "日本語" in shown

    engine = _bound_engine("s1")
    ref = make_ref({"tool_call_id": "c"}, raw)
    assert engine._store.put(ref, raw)
    assert engine.inspect_original(ref)["content"] == raw


def test_session_end_and_reset_drop_the_binding_and_activity():
    engine = _bound_engine("s1")
    _pruned_chat(engine)
    assert engine.activity_snapshot()["items"]
    engine.on_session_end("s1", [])
    assert engine.activity_snapshot()["items"] == []
    assert engine._activity_registry.resolve("s1") is None

    engine2 = _bound_engine("s2")
    _pruned_chat(engine2)
    assert engine2.activity_snapshot()["items"] and len(engine2._store) == 1
    before_reset = engine2._activity.lifecycle_id
    engine2.on_session_reset()
    assert engine2.activity_snapshot()["items"] == [] and len(engine2._store) == 0
    assert engine2._activity.lifecycle_id != before_reset and engine2._session_id == "s2"


def test_registration_wires_the_command_to_each_bound_clone():
    import hermes_tameru_plugin as plugin

    captured = {}
    messages = []

    class Ctx:
        def get_config(self, name):
            raise LookupError(name)

        def register_context_engine(self, engine):
            captured["engine"] = engine

        def register_command(self, name, handler, **kwargs):
            captured["command"] = (name, handler, kwargs)

        def post_message(self, text):
            messages.append(text)

    plugin.register(Ctx())
    assert captured["command"][0] == "tameru"
    engine = captured["engine"]
    assert engine._activity_registry.commands_available

    clone = engine.clone_for_agent()
    clone.bind_session_state(session_db=None, session_id="s9")
    # the registered engine was built with default settings; open Hermes' gates like make_engine does
    clone.proactive_prune_tokens = 1
    clone.proactive_prune_min_reclaim_tokens = 0
    clone.proactive_prune_min_result_chars = 200
    clone.protect_last_n = 4
    chat = Chat(TASK)
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    chat.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    res = clone.prune_tool_results_only(chat.finish(), 10**6)
    assert res[0] is not chat.messages
    handler = captured["command"][1]
    out = json.loads(handler("--json", session_id="s9"))
    assert out["ok"] and len(out["items"]) == 1
    assert len(messages) == 1
    assert messages[0].startswith("Tameru shortened 1 tool result") and messages[0].endswith("Details: /tameru")
    assert "Originals are kept" in messages[0]

    # a legacy ctx without register_command still registers the engine
    registered = []
    plugin.register(type("Ctx", (), {"register_context_engine": staticmethod(registered.append)})())
    assert len(registered) == 1


@contextmanager
def _native_home(home):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


@pytest.mark.real_hermes
@pytest.mark.parametrize('general_enabled', [True, False])
def test_real_discovery_keeps_profile_and_session_ownership(tmp_path, monkeypatch, general_enabled):
    from types import SimpleNamespace
    from agent import agent_init
    from agent.api_request_hooks import ApiRequestHooksMixin
    from agent.turn_api_request import _fire_pre_api_request_hook
    from hermes_cli import plugins
    from hermes_cli.config import atomic_config_write
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    launch = tmp_path / 'launch'
    launch.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(launch))
    monkeypatch.chdir(tmp_path)
    plugins._reset_plugin_managers_for_tests()
    homes = [tmp_path / 'alpha', tmp_path / 'beta']
    engines, handlers = [], []
    source = Path(__file__).resolve().parents[1] / 'hermes_tameru_plugin'
    try:
        for home, cap in zip(homes, (101, 202)):
            shutil.copytree(source, home / 'plugins' / 'tameru', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            atomic_config_write(home / 'config.yaml', {
                'context': {'engine': 'tameru'},
                'compression': {'proactive_prune_tokens': 1, 'proactive_prune_min_reclaim_tokens': 0,
                                'proactive_prune_min_result_chars': 200, 'protect_last_n': 4},
                'plugins': {'enabled': ['tameru'] if general_enabled else [], 'isolation': 'in_process',
                            'entries': {'tameru': {'settings': {
                                'policy_mode': 'legacy',   # these tests drive the fixed prune gates above
                                'store_max_entries': cap, 'prune_tail': 'count',
                                'small_window_threshold_tokens': 0,
                                'retained_extract_budget_ratio': 0,
                                'retained_extract_budget_chars': 16000,
                                'reprune_target_ratio': 0,
                            }}}},
            })
            with _native_home(home):
                manager = plugins.get_plugin_manager()
                # Isolate unrelated pip entrypoints only. Actual directory scanning,
                # manifest gating, imports, ctx registration and native selection remain real.
                monkeypatch.setattr(manager, '_scan_entry_points', lambda: [])
                manager.discover_and_load()
                prototype = plugins.get_plugin_context_engine()
                assert (prototype is not None) is general_enabled
                engine = agent_init._select_context_engine({'context': {'engine': 'tameru'}})
                assert engine is not None and engine is not prototype
                assert engine.get_policy_status()['controls']['mode'] == 'legacy'
                engine.bind_session_state(session_db=None, session_id='shared-id')
                engine.update_model('test-main', 128_000, provider='', base_url='',
                                    api_mode='chat_completions')
                engines.append(engine)
                handlers.append(plugins.get_plugin_command_handler('tameru'))
        refs, histories, originals = [], [], []
        for i, (home, engine) in enumerate(zip(homes, engines)):
            with _native_home(home):
                user_row = {'role': 'user', 'content': 'OBSERVATION_SENTINEL'}
                api_messages = [user_row]
                api_kwargs = {'model': engine.model, 'messages': api_messages,
                              'max_tokens': 101 + i}
                host = SimpleNamespace(
                    session_id='shared-id', model=engine.model, provider='', base_url='',
                    api_mode='chat_completions', platform='cli', tools=[], max_tokens=9999,
                    _api_request_payload_for_hook=ApiRequestHooksMixin()._api_request_payload_for_hook)
                _fire_pre_api_request_hook(
                    host, api_kwargs, api_messages, [], messages=api_messages,
                    original_user_message='OBSERVATION_SENTINEL', approx_tokens=10,
                    total_chars=20, retry_count=0, api_call_count=1,
                    api_request_id='probe', api_start_time=time.time(),
                    effective_task_id='task', turn_id='turn')
                status = engine.get_policy_status()
                if general_enabled:
                    assert status['budget']['source'] == 'previous_request'
                    assert status['budget']['estimated']
                    assert status['budget']['output_reserve_tokens'] == 101 + i
                    assert status['budget']['usable_input_tokens'] == 128_000 - (101 + i)
                    assert 'OBSERVATION_SENTINEL' not in json.dumps(status)
                else:
                    assert status['budget']['source'] == 'unknown'
                    assert status['budget']['output_reserve_tokens'] is None
                    assert status['last_request'] is None
                life = json.loads(handlers[i]('--json', session_id='shared-id'))['lifecycle_id']
                preference = 'save_space' if i == 0 else 'preserve_detail'
                answer = json.loads(handlers[i](
                    f'--json policy mode=adaptive preference={preference} --lifecycle {life}',
                    session_id='shared-id'))
                assert answer['ok'] and answer['policy']['controls']['preference'] == preference
                assert answer['capabilities']['policy'] is True
                payload = json.loads(pytest_failure_with_passes())
                payload['output'] = f'PROFILE_OWNER_{i}\n' + payload['output']
                originals.append(payload['output'])
                chat = Chat(TASK)
                chat.tool('terminal', {'command': 'pytest -v'}, json.dumps(payload))
                before = json.dumps(chat.finish(), sort_keys=True)
                after, _ = engine.prune_tool_results_only(chat.messages, 10**6)
                assert json.dumps(chat.messages, sort_keys=True) == before
                histories.append(json.dumps(after, sort_keys=True))
                table = json.loads(handlers[i]('--json', session_id='shared-id'))
                assert table['ok'] and table['items']
                refs.append(table['items'][0]['ref'])
                view = json.loads(handlers[i]('--json view ' + refs[i], session_id='shared-id'))
                assert view['ok'] and view['content'] == originals[i][:view['end_offset']]
                assert json.dumps(after, sort_keys=True) == histories[i]
                schemas = engine.get_tool_schemas()
                assert {s['name'] for s in schemas} == {'tameru_read_original'}
                read = json.loads(engine.handle_tool_call('tameru_read_original', {'ref': refs[i]}))
                assert 'error' not in read and read['content'].startswith(f'PROFILE_OWNER_{i}\n')
                assert engine.get_tool_schemas() == schemas
        assert refs[0] != refs[1]
        for i, home in enumerate(homes):
            with _native_home(home):
                denied = json.loads(handlers[i]('--json view ' + refs[1-i], session_id='shared-id'))
                assert not denied['ok']
                tool_denied = json.loads(engines[i].handle_tool_call('tameru_read_original', {'ref': refs[1-i]}))
                assert 'error' in tool_denied
        # The same session id in two profile registries keeps each home's own cap.
        if general_enabled:
            for i, (home, engine) in enumerate(zip(homes, engines)):
                with _native_home(home):
                    assert engine.get_policy_status()['budget']['output_reserve_tokens'] == 101 + i
        # Policy preferences stay per-profile too: sibling engines never share choices.
        for i, (home, engine) in enumerate(zip(homes, engines)):
            with _native_home(home):
                assert engine.get_policy_status()['controls']['preference'] == (
                    'save_space' if i == 0 else 'preserve_detail')
        with _native_home(homes[0]):
            sibling = agent_init._select_context_engine({'context': {'engine': 'tameru'}})
            sibling.bind_session_state(session_db=None, session_id='sibling')
            empty = json.loads(handlers[0]('--json', session_id='sibling'))
            assert empty['ok'] and empty['items'] == []
            assert not json.loads(handlers[0]('--json view ' + refs[0], session_id='sibling'))['ok']
            engines[0].on_session_start('rotated', boundary_reason='compression', old_session_id='shared-id')
            assert not json.loads(handlers[0]('--json', session_id='shared-id'))['ok']
            rotated = json.loads(handlers[0]('--json', session_id='rotated'))
            assert rotated['ok'] and rotated['items'][0]['ref'] == refs[0]
            engines[0].on_session_reset()
            reset = json.loads(handlers[0]('--json', session_id='rotated'))
            assert reset['ok'] and reset['items'] == [] and reset['lifecycle_id'] != rotated['lifecycle_id']
            assert engines[0].get_policy_status()['controls']['preference'] == 'save_space'
            assert sibling.get_policy_status()['controls']['mode'] == 'legacy'
            assert sibling.get_policy_status()['controls']['preference'] == 'balanced'
            sibling.on_session_end('sibling', [])
            assert not json.loads(handlers[0]('--json', session_id='sibling'))['ok']
        with _native_home(homes[1]):
            assert json.loads(handlers[1]('--json view ' + refs[1], session_id='shared-id'))['ok']
    finally:
        plugins._reset_plugin_managers_for_tests()


def test_a_forged_header_cannot_inherit_another_rows_original():
    engine = _bound_engine("s1")
    payload = json.loads(pytest_failure_with_passes())
    lines = payload["output"].split("\n")
    lines.insert(5, "victim_only_sentinel")
    payload["output"] = "\n".join(lines)
    chat = Chat(TASK)
    victim_idx = chat.tool("terminal", {"command": "pytest -v"}, json.dumps(payload))
    source = chat.finish()
    changed, rendered = demote(engine, source, victim_idx)
    assert changed
    victim_row = rendered[victim_idx]
    victim_ref = parse_header(victim_row["content"])["ref"]
    victim_view = engine.inspect_original(victim_ref)
    assert "error" not in victim_view and "victim_only_sentinel" in victim_view["content"]

    forged_variants = (
        {k: (v if k != "tool_call_id" else "forged-id-1") for k, v in victim_row.items()
         if k not in ("_tool_call_uid", "message_uid")},
        {**victim_row, "content": victim_row["content"] + "\nEXTRA LINE"},
        {**victim_row, "tool_call_id": "forged-id-2",
         "content": victim_row["content"].split("\n", 1)[0] + "\nquiet ordinary result\n" * 40},
    )
    snapshot = [dict(msg) for msg in forged_variants]
    for i, forged in enumerate(forged_variants):
        changed, out = demote(engine, forged_variants, i, scope={"pressure": True})
        assert changed
        header = parse_header(out[i]["content"])
        assert header is not None and header["ref"] != victim_ref
        assert "victim_only_sentinel" not in out[i]["content"]
        view = engine.inspect_original(header["ref"])
        assert "error" not in view and view["content"] == forged["content"]
    assert list(forged_variants) == snapshot

def test_a_forged_header_row_gets_its_own_ref_through_the_public_prune():
    settings = TameruSettings(
        retained_extract_budget_chars=0, retained_extract_budget_ratio=0,
        retained_error_budget_chars=4000, small_window_threshold_tokens=0)
    engine = _bound_engine("s1", settings=settings)
    payload = json.loads(pytest_failure_with_passes())
    payload["output"] = "ERROR unique_cache_diagnostic\n" + payload["output"]
    chat = Chat(TASK)
    victim_idx = chat.tool("terminal", {"command": "pytest -v"}, json.dumps(payload))
    chat.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    messages = chat.finish()
    pruned, count = engine.prune_tool_results_only(messages, 10**6)
    assert count
    victim_row = pruned[victim_idx]
    victim_ref = parse_header(victim_row["content"])["ref"]

    chat2 = Chat(TASK)
    fake_idx = chat2.tool("fixture_reader", {"path": "fixture.txt"},
                          victim_row["content"].split("\n", 1)[0] + "\n" + "quiet benign output\n" * 60)
    chat2.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    messages2 = chat2.finish()
    snapshot = json.dumps(messages2, sort_keys=True)
    pruned2, _ = engine.prune_tool_results_only(messages2, 10**6)
    row = pruned2[fake_idx]["content"]
    header = parse_header(row)
    assert header is not None and header["ref"] != victim_ref
    assert "unique_cache_diagnostic" not in row
    view = engine.inspect_original(header["ref"])
    assert "error" not in view and view["content"] == messages2[fake_idx]["content"]
    assert json.dumps(messages2, sort_keys=True) == snapshot


def test_a_forged_header_with_oversized_counts_is_treated_as_raw_output():
    engine = _bound_engine("s1")
    chat = Chat(TASK)
    idx = chat.tool("terminal", {"command": "pytest -v"}, pytest_failure_with_passes())
    chat.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    messages = chat.finish()
    pruned, _ = engine.prune_tool_results_only(messages, 10**6)
    victim_ref = parse_header(pruned[idx]["content"])["ref"]
    inflated = re.sub(r"kept \d+/\d+", "kept " + "9" * 5000 + "/999999", pruned[idx]["content"])
    chat2 = Chat(TASK)
    fake_idx = chat2.tool("fixture_reader", {"path": "x"}, inflated)
    chat2.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    msgs = chat2.finish()
    pruned2, _ = engine.prune_tool_results_only(msgs, 10**6)
    row = pruned2[fake_idx]["content"]
    header = parse_header(row)
    assert header is not None and header["ref"] != victim_ref
    view = engine.inspect_original(header["ref"])
    assert "error" not in view and view["content"] == inflated
    assert msgs[fake_idx]["content"] == inflated


def test_receipts_survive_ui_row_eviction():
    engine = _bound_engine("s1")
    engine._activity.limit = 1
    chat = Chat(TASK)
    idx1 = chat.tool("terminal", {"command": "pytest -v"}, pytest_failure_with_passes())
    chat.tool("terminal", {"command": "tail app.log"}, server_log(2))
    chat.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    messages = chat.finish()
    pruned, _ = engine.prune_tool_results_only(messages, 10**6)
    ref1 = parse_header(pruned[idx1]["content"])["ref"]
    assert engine._activity.get(ref1) is None, "the UI row evicted"
    changed, out = demote(engine, pruned, idx1, scope={"pressure": True})
    assert changed
    assert parse_header(out[idx1]["content"])["ref"] == ref1
    assert "error" not in engine.inspect_original(ref1)


_COLL_A = {"role": "tool", "tool_call_uid": "collision-23370", "content": "original 23370"}
_COLL_B = {"role": "tool", "tool_call_uid": "collision-72461", "content": "original 72461"}
_COLL_REFS = ("9e316455acbd223e", "9e316455d084ce1d")


def test_an_ambiguous_8_ref_is_refused_by_viewer_and_aliases():
    from hermes_tameru_plugin.recovery import _identity_views
    engine = make_engine(TameruSettings(store_max_entries=2, store_max_chars=10_000))
    row_a, row_b = dict(_COLL_A), dict(_COLL_B)
    assert make_ref(_identity_views(row_a)[0], row_a["content"]) == _COLL_REFS[0]
    assert make_ref(_identity_views(row_b)[0], row_b["content"]) == _COLL_REFS[1]
    engine.bind_session_state(session_db=_Archive([row_a]), session_id="s1")
    engine._store.put(_COLL_REFS[0], row_a["content"])
    engine._store.put("ffffffffffffffff", "sentinel text")
    archive = engine._session_db
    archive.rows.append(row_b)
    prefix = _COLL_REFS[0][:8]
    answer = engine.inspect_original(prefix)
    assert "error" in answer
    assert engine.inspect_original(_COLL_REFS[0])["content"] == "original 23370"
    assert engine.inspect_original(_COLL_REFS[1])["content"] == "original 72461"
    for name in (EXPAND_TOOL_NAME, READ_ORIGINAL_TOOL_NAME):
        out = json.loads(engine.handle_tool_call(name, {"ref": prefix}))
        assert "error" in out and "original 23370" not in json.dumps(out)
    assert engine._store.peek(_COLL_REFS[1]) is None, "the ambiguous archive row is not cached"
    keys = {key: engine._store.peek(key) is not None for key in (_COLL_REFS[0], "ffffffffffffffff")}
    assert keys == {_COLL_REFS[0]: True, "ffffffffffffffff": True}
    engine._store.put("eeeeeeeeeeeeeeee", "newer entry")
    assert engine._store.peek(_COLL_REFS[0]) is None, "A evicts: viewer/short reads did not refresh its LRU"
    assert engine._store.peek("ffffffffffffffff") is not None


def _terminal_with_marker(marker: str) -> str:
    """The pytest fixture result with ``marker`` inside its JSON payload's output."""
    payload = json.loads(pytest_failure_with_passes())
    payload["output"] += "\n" + marker + "\n"
    return json.dumps(payload)


@pytest.mark.parametrize("rejection", ["min_reclaim", "db_failure"])
def test_a_rejected_prune_commits_no_cache_state(rejection):
    class RejectArchive(_Archive):
        def __init__(self, rows=()):
            super().__init__(rows)
            self.reject = False

        def archive_and_compact(self, *args, **kwargs):
            if self.reject:
                raise OSError("simulated persistence failure")
            return super().archive_and_compact(*args, **kwargs)

    engine = make_engine(TameruSettings(store_max_entries=1,
                                        small_window_threshold_tokens=0))
    archive = RejectArchive()
    engine.bind_session_state(session_db=archive, session_id="s1")

    # warm one genuinely committed original before any rejection is armed
    warm_raw = pytest_failure_with_passes()
    warm = Chat(TASK)
    idx_warm = warm.tool("terminal", {"command": "pytest -q"}, warm_raw)
    warm.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    warm_messages = warm.finish()
    pruned, count = engine.prune_tool_results_only(warm_messages, 10**6)
    assert count > 0
    expected_old = parse_payload(warm_raw).inner
    old_ref = make_ref(warm_messages[idx_warm], warm_raw)
    got = json.loads(engine.handle_tool_call(
        EXPAND_TOOL_NAME, {"ref": old_ref, "max_chars": 50_000}))
    assert got["content"] == expected_old

    if rejection == "min_reclaim":
        engine.proactive_prune_min_reclaim_tokens = 10**9
    else:
        archive.reject = True

    # a different eligible output is rejected: nothing may commit anywhere
    fresh_raw = _terminal_with_marker("second run marker")
    fresh = Chat(TASK)
    idx_fresh = fresh.tool("terminal", {"command": "pytest -q"}, fresh_raw)
    fresh.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    messages = fresh.finish()
    expected_new = parse_payload(fresh_raw).inner
    new_ref = make_ref(messages[idx_fresh], fresh_raw)
    snapshot = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    telemetry_before = engine._telemetry.snapshot()["chars"]
    memo_before = len(engine._memo._items)
    res = engine.prune_tool_results_only(messages, 10**6)
    assert res[0] is messages and res[1] == 0, "native decline returns the exact input and zero count"
    assert json.loads(engine.handle_tool_call(
        EXPAND_TOOL_NAME, {"ref": old_ref, "max_chars": 50_000}))["content"] == expected_old
    unavailable = json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": new_ref}))
    assert "error" in unavailable
    assert len(engine._memo._items) == memo_before, "the rejected plan must not populate the memo"
    assert engine._telemetry.snapshot()["chars"] == telemetry_before
    assert json.dumps(messages, sort_keys=True, ensure_ascii=False) == snapshot

    # the retried pass, now committable, publishes only the second attempt's state
    if rejection == "min_reclaim":
        engine.proactive_prune_min_reclaim_tokens = 0
    else:
        archive.reject = False
    res2 = engine.prune_tool_results_only(messages, 10**6)
    assert res2[0] is not messages and res2[1] > 0
    changed = [m for m in res2[0] if m.get("role") == "tool" and "ref=" in str(m.get("content"))]
    assert changed, "the retry must produce an eligible Tameru render"
    committed_ref = parse_header(changed[0]["content"])["ref"]
    assert committed_ref == new_ref
    assert json.loads(engine.handle_tool_call(
        EXPAND_TOOL_NAME, {"ref": new_ref, "max_chars": 50_000}))["content"] == expected_new
    assert engine._store.peek(old_ref) is None, "cap=1: the retry's own state evicts the warm original"


def test_a_reset_during_plan_publishes_nothing(monkeypatch):
    engine = make_engine(TameruSettings(store_max_entries=2,
                                        small_window_threshold_tokens=0))
    engine.bind_session_state(session_db=None, session_id="s1")
    from engine_support import engine_module
    real_compress = engine_module.compress_context
    resets = 0

    def reset_during_extract(*args, **kwargs):
        nonlocal resets
        result = real_compress(*args, **kwargs)
        if not resets:
            resets += 1
            engine.on_session_reset()
        return result

    monkeypatch.setattr(engine_module, "compress_context", reset_during_extract)
    chat = Chat(TASK)
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    chat.user("Current reference:\n" + "context for this turn.\n" * 8_000)
    messages = chat.finish()
    result, count = engine.prune_tool_results_only(messages, 10**6)
    assert resets == 1
    assert len(engine._store) == 0
    assert len(engine._memo) == 0
    assert engine.activity_snapshot()["items"] == []


def test_policy_command_changes_controls_and_reports_memory_storage():
    engine = _bound_engine("s1")
    out = engine._activity_registry.command(
        "policy preserve-detail activation=earlier recent=long", session_id="s1")
    assert "Policy: Adaptive · Preserve detail" in out
    assert "Timing: Earlier · Recent protection: Long" in out
    assert "until this agent restarts" in out
    assert "future cleanup" in out
    controls = engine.get_policy_status()["controls"]
    assert controls == {"mode": "adaptive", "preference": "preserve_detail",
                        "activation": "earlier", "recent": "long"}
    snap = json.loads(engine._activity_registry.command("--json", session_id="s1"))
    assert snap["ok"] and snap["kind"] == "activity"
    assert snap["policy"]["controls"]["preference"] == "preserve_detail"
    assert snap["capabilities"]["policy"] is True


def test_policy_command_json_requires_lifecycle_and_fences():
    engine = _bound_engine("s1")
    command = engine._activity_registry.command
    denied = json.loads(command("--json policy preference=save_space", session_id="s1"))
    assert not denied["ok"] and "Refresh context details" in denied["error"]
    assert engine.get_policy_status()["controls"]["preference"] == "balanced"

    life = json.loads(command("--json policy", session_id="s1"))["lifecycle_id"]
    denied = json.loads(command("--json policy preference=save_space --lifecycle stale",
                                session_id="s1"))
    assert not denied["ok"] and "active session changed" in denied["error"]
    denied = json.loads(command(f"--json policy preference=save_space --lifecycle {life}",
                                session_id="s2"))
    assert not denied["ok"]
    assert engine.get_policy_status()["controls"]["preference"] == "balanced"

    answer = json.loads(command(f"--json policy preference=save_space --lifecycle {life}",
                                session_id="s1"))
    assert answer["ok"] and answer["policy"]["controls"]["preference"] == "save_space"
    life = answer["lifecycle_id"]
    answer = json.loads(command(f"--json policy reset --lifecycle {life}", session_id="s1"))
    assert answer["ok"] and answer["policy"]["controls"]["preference"] == "balanced"
    assert answer["policy"]["controls_overridden"] is False


def test_policy_command_rejects_malformed_args():
    engine = _bound_engine("s1")
    command = engine._activity_registry.command
    life = json.loads(command("--json policy", session_id="s1"))["lifecycle_id"]
    for bad in ("policy preference=save_space preference=balanced",
                "policy reset preference=save_space",
                "policy preference=balanced reset",
                "policy mode=quantum",
                "policy bogus=earlier",
                f"policy --lifecycle {life} --lifecycle {life}",
                "policy --lifecycle",
                "policy activate",
                "policy disable"):
        out = json.loads(command(f"--json {bad}", session_id="s1"))
        assert not out["ok"], bad
    assert engine.get_policy_status()["controls"]["preference"] == "balanced"
    assert engine.get_policy_status()["last_request"] is None


def test_policy_command_preserves_safe_errors_and_never_echoes_history():
    engine = make_engine(TameruSettings(policy_mode="legacy"))
    engine.bind_session_state(session_db=None, session_id="s1")
    command = engine._activity_registry.command
    out = command("policy recent=long", session_id="s1")
    assert "Select Adaptive or Fixed" in out and "Use /tameru" not in out

    engine = _bound_engine("s1")
    chat = Chat(TASK)
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    chat.user("HISTORY_SENTINEL for policy output")
    engine.prune_tool_results_only(chat.finish(), 10**6)
    out = engine._activity_registry.command("policy save-space", session_id="s1")
    assert "HISTORY_SENTINEL" not in out
    assert engine.get_policy_status()["controls"]["preference"] == "save_space"


def test_policy_command_shows_actual_check_basis():
    engine = _bound_engine("s1")
    _pruned_chat(engine)
    out = engine._activity_registry.command("policy", session_id="s1")
    assert "whole request estimate" in out
    assert "message bodies only" not in out

    engine = _bound_engine("s1")
    chat = Chat(TASK)
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    engine.prune_tool_results_only(chat.finish(), current_tokens=None)
    out = engine._activity_registry.command("policy", session_id="s1")
    assert "message bodies only" in out
    assert "whole request estimate" not in out


def test_policy_display_matches_lines():
    engine = _bound_engine("s1")
    snap = engine.activity_snapshot()
    display = snap["policy"]["display"]
    out = engine._activity_registry.command("policy", session_id="s1")
    for line in [display["storage"], display["footnote"]]:
        assert line in out
    for line in display["budget"] + display["check"]:
        assert line in out
