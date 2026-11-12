"""``chat_store``: per-chat records in the plugin's own durable state, with retention, and never in Hermes' session storage."""
from __future__ import annotations

import hashlib

import pytest

from engine_support import FakeState, make_engine
from test_autotune_engine import _MetaDB

from hermes_tameru_plugin import chat_store as cs
from hermes_tameru_plugin.chat_store import MAX_AGE_DAYS, MAX_CHATS, ChatStore, chat_key
from hermes_tameru_plugin.config import TameruSettings


class Clock:
    def __init__(self, day=20_000):
        self.day = day

    def __call__(self):
        return self.day


def _store(state=None, clock=None):
    clock = clock or Clock()
    return ChatStore(state if state is not None else FakeState(), today=clock), clock


# ---- keys --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ["s1", "local-" + "a1" * 32, "20261106_223028_cf1304", "agent:main:local:dm:x"])
def test_an_ordinary_session_id_is_the_key(sid):
    assert chat_key(sid) == f"chat:{sid}"


@pytest.mark.parametrize("sid", ["has space", "slash/in/it", "x" * 200, "dots..inside", "naïve", "tab\tid"])
def test_an_id_that_is_not_a_legal_key_is_hashed_and_still_legal(sid):
    key = chat_key(sid)
    assert key == "chat:h:" + hashlib.sha256(sid.encode()).hexdigest()[:40]
    FakeState().set(key, 1)                                  # the stand-in enforces the real key rules
    assert chat_key(sid) == key                              # stable


def test_an_empty_or_non_string_id_has_no_key():
    assert chat_key("") is None and chat_key(None) is None and chat_key(5) is None


# ---- reads and writes ------------------------------------------------------------------------------------
def test_a_part_round_trips_and_other_parts_and_other_chats_are_untouched():
    store, _ = _store()
    assert store.write("s1", "pins", {"version": 1, "pins": ["never touch prod"]})
    assert store.write("s1", "tuner", {"version": 1, "paused": True})
    assert store.write("s2", "pins", {"version": 1, "pins": ["other chat"]})
    assert store.read("s1", "pins") == {"version": 1, "pins": ["never touch prod"]}
    assert store.read("s1", "tuner") == {"version": 1, "paused": True}
    assert store.read("s2", "pins")["pins"] == ["other chat"]
    assert store.read("s1", "edits") is None and store.read("nobody", "pins") is None


def test_a_part_can_be_cleared_and_clearing_a_missing_part_is_fine():
    store, _ = _store()
    store.write("s1", "policy", {"version": 1})
    assert store.write("s1", "policy", None) and store.read("s1", "policy") is None
    assert store.write("s1", "policy", None) and store.write("never-written", "policy", None)


def test_an_unknown_part_or_chat_is_refused_without_raising():
    store, _ = _store()
    assert store.write("s1", "mystery", {}) is False and store.read("s1", "mystery") is None
    assert store.write("", "pins", {}) is False and store.read("", "pins") is None


def test_a_write_the_state_did_not_keep_is_reported_as_failed():
    class Forgetful(FakeState):
        def set(self, key, value):
            if key.startswith("chat:"):
                return                                    # accepted and silently dropped
            super().set(key, value)

    store, _ = _store(Forgetful())
    assert store.write("s1", "pins", {"version": 1}) is False


def test_a_state_that_raises_never_raises_through_the_store():
    class Broken:
        def get(self, key, default=None):
            raise RuntimeError("PRIVATE_SENTINEL")

        def set(self, key, value):
            raise RuntimeError("PRIVATE_SENTINEL")

    store = ChatStore(Broken(), today=Clock())
    assert store.write("s1", "pins", {}) is False and store.read("s1", "pins") is None


def test_an_unchanged_write_is_not_written_again():
    state = FakeState()
    store, _ = _store(state)
    store.write("s1", "pins", {"version": 1, "pins": ["a"]})
    before = state.writes
    assert store.write("s1", "pins", {"version": 1, "pins": ["a"]}) and state.writes == before


def test_a_record_of_another_version_is_ignored():
    state = FakeState()
    state.set("chat:s1", {"v": 99, "day": 20_000, "pins": {"version": 1}})
    store, _ = _store(state)
    assert store.read("s1", "pins") is None
    assert store.write("s1", "pins", {"version": 1}) and store.read("s1", "pins") == {"version": 1}


# ---- retention -------------------------------------------------------------------------------------------
def test_a_chat_unused_for_ninety_days_is_retired_and_a_recent_one_is_kept():
    state = FakeState()
    store, clock = _store(state)
    store.write("old", "pins", {"version": 1})
    clock.day += MAX_AGE_DAYS + 1
    store.write("new", "pins", {"version": 1})                # the first write of a new day sweeps
    assert state.get("chat:old", "absent") is None and store.read("old", "pins") is None
    assert store.read("new", "pins") == {"version": 1}
    assert "chat:old" not in state.get("chats") and "chat:new" in state.get("chats")


def test_reading_a_chat_keeps_it_alive():
    state = FakeState()
    store, clock = _store(state)
    store.write("kept", "pins", {"version": 1})
    for _ in range(4):                                         # read every 60 days: never idle for 90
        clock.day += 60
        assert store.read("kept", "pins") == {"version": 1}
    assert state.get("chat:kept") is not None


def test_only_the_newest_chats_beyond_the_cap_survive():
    state = FakeState()
    store, clock = _store(state)
    for i in range(MAX_CHATS + 5):
        clock.day = 20_000 + i // 50                           # a few chats per day, so some are newer than others
        store.write(f"c{i:04d}", "pins", {"version": 1})
    clock.day += 1
    store.sweep()
    assert len(state.get("chats")) == MAX_CHATS
    gone = [f"chat:c{i:04d}" for i in range(MAX_CHATS + 5) if state.get(f"chat:c{i:04d}") is None]
    assert gone == [f"chat:c{i:04d}" for i in range(5)], "the five oldest go, oldest first"
    assert store.read("c0000", "pins") is None and store.read(f"c{MAX_CHATS + 4:04d}", "pins") == {"version": 1}


def test_the_sweep_runs_at_most_once_a_day():
    state = FakeState()
    store, clock = _store(state)
    store.write("a", "pins", {"version": 1})
    clock.day += MAX_AGE_DAYS + 1
    store.write("b", "pins", {"version": 1})                   # sweeps (a goes)
    store.write("a", "pins", {"version": 1})                   # same day: a is recreated and not swept again today
    assert store.read("a", "pins") == {"version": 1}


# ---- the engine --------------------------------------------------------------------------------------
def test_state_without_get_and_set_means_memory_only():
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    assert engine.attach_chat_state(object()) is False and engine._chat_store is None
    assert engine.attach_chat_state(None) is False
    assert engine.attach_chat_state(FakeState()) is True and engine._chat_store is not None


def test_nothing_is_written_to_hermes_session_storage():
    """Pins, Keeps, the tuner, the policy: every per-chat write goes to the plugin's state, none to the session db."""
    from hermes_tameru_plugin.session_tuner import set_posture

    class Spy(_MetaDB):
        calls: list = []

        def patch_session_model_config(self, sid, patch):
            Spy.calls.append(("patch", sid, patch))
            raise AssertionError("the plugin wrote Hermes' session storage")

        def get_session_model_config_value(self, sid, key):
            Spy.calls.append(("read", sid, key))
            raise AssertionError("the plugin read Hermes' session model_config")

    db = Spy()
    Spy.calls = []
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    state = FakeState()
    engine.attach_chat_state(state)
    engine.bind_session_state(session_db=db, session_id="s1")
    life = engine._activity.lifecycle_id
    engine.pin_change("pin", "never touch the production config")
    set_posture(engine, session_id="s1", lifecycle_id=life, bias=0.5, paused=True)
    engine.set_policy_controls({"preference": "preserve_detail"}, session_id="s1", lifecycle_id=life)
    assert Spy.calls == []
    saved = state.get("chat:s1")
    assert saved["pins"]["pins"] == ["never touch the production config"]
    assert saved["tuner"]["paused"] is True and saved["policy"]["controls"]["preference"] == "preserve_detail"
    # and a new engine bound to the same chat restores all three
    again = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    again.attach_chat_state(state)
    again.bind_session_state(session_db=db, session_id="s1")
    assert again._pins.items == ["never touch the production config"] and again._policy.paused is True
    assert again.get_policy_status()["controls"]["preference"] == "preserve_detail"
    assert Spy.calls == []


def test_a_rotation_carries_the_records_to_the_new_session_id():
    from hermes_tameru_plugin.session_pins import bind_pins

    state = FakeState()
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    engine.attach_chat_state(state)
    engine.bind_session_state(session_db=None, session_id="s1")
    engine.pin_change("pin", "keep me")
    engine._session_id = "s1-child"
    bind_pins(engine, rotation=True)                           # what a host-compression rotation does
    assert ChatStore(state).read("s1-child", "pins")["pins"] == ["keep me"]


def test_a_clone_shares_the_store_instead_of_copying_it():
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    clone = engine.clone_for_agent()
    assert clone._chat_store is engine._chat_store and clone._chat_store is not None


def test_register_hands_the_context_state_to_the_engine():
    from unittest.mock import MagicMock

    import hermes_tameru_plugin as plugin

    ctx = MagicMock()
    ctx.state = FakeState()
    plugin.register(ctx)
    engine = ctx.register_context_engine.call_args[0][0]
    assert engine._chat_store is not None
    bare = MagicMock(spec=["register_context_engine", "register_command", "register_hook", "plugin_id", "post_message"])
    plugin.register(bare)                                       # no ctx.state at all: memory only, no error
    assert bare.register_context_engine.call_args[0][0]._chat_store is None
