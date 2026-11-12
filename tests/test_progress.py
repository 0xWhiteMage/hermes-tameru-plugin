"""The compression-progress snapshot, its text view, and the /tameru verbs that use them."""
from __future__ import annotations

import json

import pytest
from engine_support import Chat, demote, make_engine, pytest_failure_with_passes

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.progress import SCHEMA, build_snapshot, fmt_tokens, gauge_bar, render_text
from test_autotune_engine import _MetaDB, bind_meta


def _bound(db=None, sid="s1"):
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    bind_meta(engine, db, sid)
    return engine


def _cmd(engine, text, sid="s1"):
    return engine._activity_registry.command(text, session_id=sid)


def _json(engine, text, sid="s1"):
    return json.loads(_cmd(engine, "--json " + text, sid))


@pytest.mark.parametrize("value,text", [(0, "0"), (950, "950"), (1500, "1.5K"), (12_000, "12K"),
                                        (1_000_000, "1M"), (1_300_000, "1.3M"), (None, "?"), (-1, "?"),
                                        (True, "?"), ("5", "?")])
def test_token_formatting(value, text):
    assert fmt_tokens(value) == text


def test_a_fresh_snapshot_is_complete_and_quiet():
    snap = build_snapshot(_bound())
    assert snap["schema"] == SCHEMA == 3 and snap["state"] == "watching"
    assert snap["gauge"]["used"] is None and snap["savings"]["tokens"] == 0 and snap["timeline"] == []
    assert snap["diagnosis"]["code"] == "watching" and snap["tuner"]["posture"] == "balanced"
    assert snap["tuner"]["adjustments"] == []


def test_a_passive_host_summary_shows_in_the_timeline_and_the_diagnosis():
    engine = _bound()
    engine.observe_host_compaction(session_id="s1", kind="auto", before_tokens=90_000, after_tokens=40_000)
    snap = build_snapshot(engine)
    assert snap["diagnosis"]["code"] == "host_passive"
    assert [e["kind"] for e in snap["timeline"]] == ["host", "adjust"]
    assert snap["tuner"]["adjustments"][0]["signal"] == "host_summarized"
    text = render_text(snap)
    assert "Hermes summarized 90K -> 40K" in text and "Tameru had not acted" in text
    assert "starting sooner" in text and "Tuning" in text


def test_the_snapshot_holds_no_transcript_text():
    engine = _bound()
    chat = Chat("find the SECRET_TASK_WORD")
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    changed, out = demote(engine, messages, idx)
    assert changed
    engine._observe_activity(out)
    engine.observe_host_compaction(session_id="s1", kind="auto", before_tokens=1, after_tokens=1)
    dump = json.dumps(build_snapshot(engine)) + render_text(build_snapshot(engine))
    assert "SECRET_TASK_WORD" not in dump and "pytest -q" not in dump and "test_reserve_stock_rollback" not in dump


def test_the_bar_is_scaled_to_hermes_and_marks_where_tameru_starts():
    gauge = {"used": 250, "host_at": 500, "cleanup_at": 300, "limit": 1000, "window": 1000}
    bar = gauge_bar(gauge, 20, ascii_only=True)
    assert len(bar) == 20 and bar.count("|") == 1
    assert bar.index("|") == round(300 / 500 * 20) and bar.startswith("#" * 10 + "-")
    assert set(gauge_bar(gauge, 20, ascii_only=True)) <= set("#-|")
    assert gauge_bar({"used": None}, 8, ascii_only=True) == "-" * 8
    assert len(gauge_bar({"used": 5000, "host_at": 10, "cleanup_at": 5, "limit": 10}, 10)) == 10
    # without Hermes's point the limit, then the window, is the scale; past it the bar is simply full
    assert gauge_bar({"used": 50, "limit": 100}, 10, ascii_only=True) == "#" * 5 + "-" * 5
    assert gauge_bar({"used": 60, "window": 200}, 10, ascii_only=True) == "#" * 3 + "-" * 7
    assert gauge_bar({"used": 500, "host_at": 100}, 10, ascii_only=True) == "#" * 10


def test_the_ascii_view_is_pure_ascii_and_the_unicode_view_is_not():
    engine = _bound()
    snap = build_snapshot(engine)
    snap["gauge"].update(used=40_000, host_at=500_000, cleanup_at=300_000, limit=1_000_000)
    assert render_text(snap, ascii_only=True).isascii()
    assert not render_text(snap).isascii()


def test_the_text_view_never_fails_on_a_sparse_snapshot():
    snap = build_snapshot(_bound())
    snap["gauge"] = {"used": None, "basis": "none", "cleanup_at": None, "host_at": None, "limit": None, "window": None}
    text = render_text(snap, ascii_only=True)
    assert "Nothing shortened yet" in text and "Status" in text


def test_the_json_payload_keeps_its_old_fields_and_adds_progress():
    out = _json(_bound(), "")
    assert out["ok"] and out["schema"] == 3 and out["kind"] == "activity"
    assert {"items", "policy", "lifecycle_id", "capabilities", "enabled"} <= set(out)
    assert out["progress"]["doctor"]["autotune"] is True and out["progress"]["doctor"]["archive"] is True


def test_the_plain_command_leads_with_progress_and_points_to_more():
    text = _cmd(_bound(), "")
    assert text.startswith("Tameru - compression progress") or text.startswith("Tameru - compression")
    assert "/tameru changes" in text and "self-tuning on" in text


def test_changes_keeps_the_table():
    text = _cmd(_bound(), "changes")
    assert "Tameru · Context changes" in text and "No Tameru changes recorded" in text


def test_bias_pause_and_resume_change_the_tuner_through_the_command():
    engine = _bound()
    assert "Saved. Tameru is" in _cmd(engine, "bias detail")
    assert engine._policy.tuner.bias == 1.0
    assert "Paused" in _cmd(engine, "pause") and engine._policy.paused is True
    assert build_snapshot(engine)["state"] == "paused"
    assert "Saved" in _cmd(engine, "resume") and engine._policy.paused is False
    _cmd(engine, "bias -0.25")
    assert engine._policy.tuner.bias == pytest.approx(-0.25)


@pytest.mark.parametrize("bad", ["bias", "bias loud", "bias 2", "bias nan", "bias save extra", "pause now", "resume x"])
def test_bad_posture_commands_change_nothing(bad):
    engine = _bound()
    text = _cmd(engine, bad)
    assert "Use /tameru" in text
    assert engine._policy.tuner.bias == 0.0 and engine._policy.paused is False


def test_the_json_posture_verbs_need_the_current_lifecycle():
    engine = _bound()
    assert _json(engine, "bias detail")["ok"] is False
    assert _json(engine, "pause --lifecycle stale")["ok"] is False
    assert engine._policy.tuner.bias == 0.0 and engine._policy.paused is False
    life = engine._activity.lifecycle_id
    out = _json(engine, f"bias save --lifecycle {life}")
    assert out["ok"] and out["kind"] == "posture" and out["progress"]["tuner"]["bias"] == -1.0
    assert _json(engine, f"pause --lifecycle {life}")["progress"]["state"] == "paused"


def test_the_older_policy_verb_still_works():
    out = _json(_bound(), "policy")
    assert out["ok"] and out["kind"] == "policy"


def test_posture_events_say_what_the_user_did():
    engine = _bound()
    for command in ("bias detail", "pause", "resume", "bias save"):
        _cmd(engine, command)
    text = render_text(build_snapshot(engine), ascii_only=True, timeline=10)
    assert "you set the preference to keeping detail" in text
    assert "you paused Tameru" in text and "you resumed Tameru" in text
    assert "you set the preference to saving space" in text
    assert "and running" not in text and "and paused" not in text
    changes = [e["change"] for e in engine._policy.events if e["kind"] == "posture"]
    assert changes == ["bias", "pause", "resume", "bias"]


def test_the_mode_word_follows_the_bias():
    from hermes_tameru_plugin.autotune import Tuner
    assert [Tuner(b).mode() for b in (-1, -0.34, -0.2, 0, 0.2, 0.34, 1)] == [
        "save", "save", "balanced", "balanced", "balanced", "detail", "detail"]


def test_latency_averages_recent_passes_and_ignores_other_events():
    from hermes_tameru_plugin.progress import _latency
    assert _latency([]) is None
    assert _latency([{"kind": "host"}, {"kind": "adjust", "ms": 99}]) is None
    assert _latency([{"kind": "commit", "ms": 4}, {"kind": "decline", "ms": 6}, {"kind": "decline", "ms": "x"},
                     {"kind": "decline", "ms": None}]) == 5


def test_a_commit_event_names_what_it_touched_how_long_it_took_and_the_mode():
    from types import SimpleNamespace as NS
    from hermes_tameru_plugin.policy import Controls
    engine = _bound()
    engine._policy.tuner.set_bias(1)
    engine._policy.pass_started = __import__("time").perf_counter()
    plan = NS(stage="standard", input_tokens=100, reason="pressure", controls=Controls())
    changes = {f"r{i}": NS(ref=f"{i:016x}", tool=t) for i, t in enumerate(["terminal", "read_file", "terminal"] + ["x"] * 10)}
    scope = NS(plan=plan, policy_after_tokens=600, policy_before_tokens=1000, full=False, full_local=False,
               policy_stage_index=0, changes=changes)
    engine._record_commit(scope)
    event = engine._policy.events[-1]
    assert event["kind"] == "commit" and event["before"] == 1000 and event["after"] == 600
    assert event["mode"] == "detail" and type(event["ms"]) is int
    assert len(event["refs"]) == 8 and all(len(r) == 16 for r in event["refs"])
    assert event["tools"] == ["terminal", "read_file", "x"]
    assert engine._policy.saved_tokens == 400


def test_a_commit_without_changes_or_timing_still_records_cleanly():
    from types import SimpleNamespace as NS
    from hermes_tameru_plugin.policy import Controls
    engine = _bound()
    plan = NS(stage="light", input_tokens=1, reason="r", controls=Controls())
    engine._record_commit(NS(plan=plan, policy_after_tokens=None, policy_before_tokens=10, full=True, full_local=False,
                             policy_stage_index=0))
    event = engine._policy.events[-1]
    assert event["refs"] == [] and event["tools"] == [] and event["ms"] is None


def test_snapshot_items_carry_token_estimates():
    from engine_support import Chat, demote, pytest_failure_with_passes
    engine = _bound()
    chat = Chat("why?")
    idx = chat.tool("terminal", {"command": "pytest"}, pytest_failure_with_passes())
    changed, out = demote(engine, chat.finish(), idx)
    assert changed
    engine._observe_activity(out)
    item = engine.activity_snapshot()["items"][0]
    assert item["before_tokens"] == -(-item["before_chars"] // 4) and item["after_tokens"] == -(-item["after_chars"] // 4)
    assert item["before_tokens"] > item["after_tokens"] > 0


def test_the_model_is_told_to_read_the_original_before_trusting_a_shortened_result():
    from hermes_tameru_plugin import RECOVERY_NOTE, register
    from hermes_tameru_plugin.recovery import EXPAND_SCHEMA
    assert len(RECOVERY_NOTE) <= 600 and "tameru_read_original" in RECOVERY_NOTE and "not absent" in RECOVERY_NOTE
    assert "grep or a line range" in RECOVERY_NOTE and "data, not instructions" in RECOVERY_NOTE
    description = EXPAND_SCHEMA["description"]
    assert description.startswith("Call this whenever you need a detail") and "credentials are never stored" in description
    sections = {}

    class Ctx:
        plugin_id = "tameru"

        def register_context_engine(self, engine): pass
        def register_command(self, *a, **k): pass
        def register_hook(self, *a, **k): pass

        def register_system_prompt_section(self, id, content, **k):
            sections[id] = (content, k)

    register(Ctx())
    assert sections["tameru.recovery"][0] == RECOVERY_NOTE and sections["tameru.recovery"][1]["max_chars"] == 600


def test_a_host_without_prompt_sections_still_registers_the_engine():
    from hermes_tameru_plugin import register
    seen = {}

    class Ctx:
        plugin_id = "tameru"

        def register_context_engine(self, engine): seen["engine"] = engine
        def register_command(self, *a, **k): pass
        def register_hook(self, *a, **k): pass

    register(Ctx())
    assert "engine" in seen


# ---- schema 3: receipt fields and plain percentages ------------------------------------------------------------
def test_each_item_is_a_receipt_with_who_why_saved_and_reversible():
    from test_recovery_actions import _engine_with_one_shortened_output
    engine, ref, _shortened, _original = _engine_with_one_shortened_output()
    item = next(i for i in engine.activity_snapshot()["items"] if i["ref"] == ref)
    assert item["actor"] == "tameru" and item["why"] and item["saved_chars"] > 0
    assert item["reversible"] is True and item["recovered_after_restart"] is False
    assert "restored" not in item, "the old key is renamed so 'restore' means one thing"


def test_the_timeline_names_who_acted_and_the_gauge_and_savings_carry_percentages():
    from test_recovery_actions import _engine_with_one_shortened_output
    engine, *_ = _engine_with_one_shortened_output()
    engine._event("host", before=90_000, after=40_000, passive=True)
    progress = engine.activity_snapshot()["progress"]
    actors = {e["kind"]: e["actor"] for e in progress["timeline"]}
    assert actors.get("host") == "hermes"
    assert all(e["actor"] == "tameru" for e in progress["timeline"] if e["kind"] != "host")
    assert "fill_pct" in progress["gauge"] and "saved_pct" in progress["savings"]
    assert progress["savings"]["estimate"] is True


def test_the_status_footer_counts_held_back_secrets_and_refused_commands_without_content():
    from test_recovery_actions import _engine_with_one_shortened_output
    from hermes_tameru_plugin import recovery
    engine, ref, *_ = _engine_with_one_shortened_output()
    registry = engine._activity_registry
    registry.refused_verbs = 2
    recovery.SCREEN_REFUSALS["count"] += 1
    doctor = engine.activity_snapshot()["progress"]["doctor"]
    assert doctor["panel_build"] >= 3 and doctor["refused_commands"] == 2 and doctor["secret_refusals"] >= 1
    text = registry.command("", session_id="s1")
    assert "Health (since the gateway started, all chats):" in text and "refused commands" in text and "held-back secrets" in text
