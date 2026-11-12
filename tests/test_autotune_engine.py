"""The self-tuner inside the engine: signals, timeline, posture controls, persistence, hook wiring."""
from __future__ import annotations

import copy
import json

import pytest

from engine_support import Chat, FakeState, make_engine, pytest_failure_with_passes

from hermes_tameru_plugin.autotune import astuple
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.policy import Controls, PlanningInput, Retention, RequestBudget, plans
from hermes_tameru_plugin.autotune import Tuner
from hermes_tameru_plugin.session_policy import PolicyError
from hermes_tameru_plugin.chat_store import ChatStore


class _MetaDB:
    """Minimal session store: archive hooks plus the three metadata calls the plugin persists through."""

    def __init__(self):
        self.config = {}
        self.state = FakeState()          # the plugin's own durable state: per-chat records go here, never into the session db
        self.sessions = {"s1", "s2"}

    def get_messages(self, session_id, include_compacted=False, **kwargs):
        return []

    def archive_and_compact(self, *args, **kwargs):
        return True

    def get_session(self, sid):
        return {"id": sid} if sid in self.sessions else None

    def get_session_model_config_value(self, sid, key):
        return self.config.get((sid, key))

    def patch_session_model_config(self, sid, patch):
        for key, value in patch.items():
            self.config[(sid, key)] = json.loads(json.dumps(value))


def bind_meta(engine, db=None, sid="s1"):
    """Bind ``engine`` to ``sid`` with ``db`` as the session db and ``db.state`` as the plugin's durable state."""
    db = db or _MetaDB()
    engine.attach_chat_state(db.state)
    engine.bind_session_state(session_db=db, session_id=sid)
    return db


def saved(db, sid="s1", part="tuner"):
    """The record the plugin saved for one part of a chat."""
    return ChatStore(db.state).read(sid, part)


def _engine(**settings):
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **settings))
    return engine


def _bound(db=None, sid="s1", **settings):
    engine = _engine(**settings)
    bind_meta(engine, db, sid)
    return engine


def _planning(tokens, window=1_000_000):
    return PlanningInput(
        budget=RequestBudget(window_tokens=window, output_reserve_tokens=0, input_limit_tokens=None, source="host"),
        current_tokens=tokens, input_complete=True, host_threshold_tokens=int(window * 0.5),
        retention=Retention(6000, 1200, 6000, 8000, 1600, 20_000), minimum_reclaim_tokens=0,
        minimum_result_chars=400)


def test_a_fresh_tuning_plans_exactly_like_the_fixed_table():
    controls = Controls()
    for tokens in (50_000, 400_000, 700_000, 900_000):
        assert plans(controls, _planning(tokens), Tuner().tuning()) == plans(controls, _planning(tokens))


def test_a_tuned_plan_differs_where_the_tuning_differs():
    controls = Controls()
    eager = Tuner()
    eager.signal("host_summarized")
    assert plans(controls, _planning(290_000), eager.tuning())[0].consider is True
    assert plans(controls, _planning(290_000))[0].consider is False
    assert (plans(controls, _planning(290_000), eager.tuning())[0].activation_tokens
            < plans(controls, _planning(290_000))[0].activation_tokens)


def test_a_host_summary_that_tameru_did_not_precede_retunes_and_is_logged():
    engine = _bound()
    before = astuple(engine._policy.tuner.tuning())
    assert engine.observe_host_compaction(session_id="s1", kind="auto", before_tokens=900, after_tokens=300) is True
    after = astuple(engine._policy.tuner.tuning())
    assert after[0] < before[0]
    kinds = [e["kind"] for e in engine._policy.events]
    assert kinds == ["host", "adjust"]
    host = engine._policy.events[0]
    assert host["before"] == 900 and host["after"] == 300 and host["passive"] is True


def test_a_host_summary_after_a_tameru_commit_is_not_passive():
    engine = _bound()
    engine._policy.commits_since_host = 1
    assert engine.observe_host_compaction(session_id="s1", kind="auto") is False
    assert astuple(engine._policy.tuner.tuning()) == astuple(Tuner().tuning())
    assert engine._policy.commits_since_host == 0


def test_garbage_in_a_host_event_is_stored_as_none_not_trusted():
    engine = _bound()
    engine.observe_host_compaction(session_id="s1", kind="x" * 500, before_tokens="lots", after_tokens=-5)
    event = engine._policy.events[0]
    assert event["before"] is None and event["after"] is None and len(event["compaction"]) <= 24


def test_autotune_off_ignores_signals():
    engine = _bound(autotune=False)
    engine.observe_host_compaction(session_id="s1", kind="auto")
    assert engine._tuning() is None
    assert astuple(engine._policy.tuner.tuning()) == astuple(Tuner().tuning())


def test_the_event_log_is_bounded():
    engine = _bound()
    for _ in range(500):
        engine.observe_host_compaction(session_id="s1", kind="auto")
    assert len(engine._policy.events) <= 40


def test_posture_is_validated_and_bound_to_the_live_session():
    engine = _bound()
    life = engine._activity.lifecycle_id
    from hermes_tameru_plugin.session_tuner import set_posture
    with pytest.raises(PolicyError):
        set_posture(engine, session_id="s1", lifecycle_id="stale", bias=0.5)
    with pytest.raises(PolicyError):
        set_posture(engine, session_id="other", lifecycle_id=life, bias=0.5)
    with pytest.raises(PolicyError):
        set_posture(engine, session_id="s1", lifecycle_id=life)
    for bad in ("high", float("nan"), None):
        if bad is not None:
            with pytest.raises(PolicyError):
                set_posture(engine, session_id="s1", lifecycle_id=life, bias=bad)
    with pytest.raises(PolicyError):
        set_posture(engine, session_id="s1", lifecycle_id=life, paused="yes")
    assert engine._policy.tuner.bias == 0.0 and engine._policy.paused is False


def test_posture_persists_and_a_new_engine_restores_it():
    from hermes_tameru_plugin.session_tuner import set_posture
    db = _MetaDB()
    engine = _bound(db)
    set_posture(engine, session_id="s1", lifecycle_id=engine._activity.lifecycle_id, bias=-0.6, paused=True)
    assert saved(db)["paused"] is True
    restarted = _bound(db)
    assert restarted._policy.tuner.bias == pytest.approx(-0.6) and restarted._policy.paused is True
    other = _bound(db, sid="s2")
    assert other._policy.tuner.bias == 0.0 and other._policy.paused is False


def test_an_adjustment_is_saved_and_restored():
    db = _MetaDB()
    engine = _bound(db)
    engine.observe_host_compaction(session_id="s1", kind="auto")
    restarted = _bound(db)
    assert astuple(restarted._policy.tuner.tuning()) == pytest.approx(astuple(engine._policy.tuner.tuning()))


def test_a_corrupt_saved_record_leaves_defaults():
    db = _MetaDB()
    ChatStore(db.state).write("s1", "tuner", {"version": 1, "paused": "maybe", "tuner": {"version": 1, "bias": 9, "offsets": [1]}})
    engine = _bound(db)
    assert engine._policy.tuner.bias == 0.0 and engine._policy.paused is False


def test_a_clone_keeps_the_choices_but_not_the_observations():
    from hermes_tameru_plugin.session_tuner import set_posture
    engine = _bound()
    set_posture(engine, session_id="s1", lifecycle_id=engine._activity.lifecycle_id, bias=0.5, paused=True)
    engine.observe_host_compaction(session_id="s1", kind="auto")
    clone = copy.deepcopy(engine)
    assert clone._policy.tuner.bias == pytest.approx(0.5) and clone._policy.paused is True
    assert len(clone._policy.events) == 0 and clone._policy.commits_since_host == 0


def test_the_hook_is_registered_and_routes_to_the_session_engine():
    from hermes_tameru_plugin import register
    calls = {}

    class Ctx:
        plugin_id = "tameru"

        def register_context_engine(self, engine):
            calls["engine"] = engine

        def register_command(self, *a, **k):
            pass

        def register_hook(self, name, fn, **k):
            calls[name] = fn

    register(Ctx())
    assert "post_compaction" in calls and "pre_api_request" in calls
    engine = calls["engine"]
    bind_meta(engine, None, "s1")
    calls["post_compaction"](session_id="s1", old_session_id="s0", kind="auto", reason="r",
                             before_tokens=10, after_tokens=5, messages_removed=3)
    assert [e["kind"] for e in engine._policy.events][:1] == ["host"]


def test_a_reread_of_a_shortened_output_keeps_more_detail():
    from engine_support import demote
    engine = _bound()
    chat = Chat("why did the gateway time out?")
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    changed, out = demote(engine, messages, idx)
    assert changed
    engine._observe_activity(out)
    ref = engine.activity_snapshot()["items"][0]["ref"]
    before = astuple(engine._policy.tuner.tuning())
    engine.handle_tool_call("tameru_read_original", {"ref": ref})
    after = astuple(engine._policy.tuner.tuning())
    assert after[1] > before[1] and after[2] > before[2]
    engine2 = _bound()
    start = astuple(engine2._policy.tuner.tuning())
    engine2.handle_tool_call("tameru_read_original", {"ref": "0" * 16})
    assert astuple(engine2._policy.tuner.tuning()) == start, "a reference that is not a shortened output sends no signal"
