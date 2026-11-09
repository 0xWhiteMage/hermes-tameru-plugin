"""Pinned instructions: the user's own lines, word for word, in every Hermes summary; nothing inferred."""
from __future__ import annotations

import json

import pytest
from engine_support import make_engine
from test_autotune_engine import _MetaDB

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.pins import HEADING, MAX_PINS, PIN_CHARS, TOTAL_CHARS, PinState, clean_pin, merge_pins
from hermes_tameru_plugin.session_policy import PolicyError

SECRET = "gh" + "p_" + "A1b2C3d4E5" * 3


def _engine(**kw):
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **kw))
    engine.bind_session_state(session_db=_MetaDB(), session_id="s1")
    return engine


def _run(engine, text, **context):
    context.setdefault("platform", "desktop")
    context.setdefault("source", None)
    guard = f" --lifecycle {engine._activity.lifecycle_id}"   # Desktop always sends it
    return json.loads(engine._activity_registry.command("--json " + text + guard, session_id="s1", **context))


# ---- what can be pinned -----------------------------------------------------------------------------------
def test_a_pin_is_one_clean_line():
    assert clean_pin("  never touch\n\tproduction   config\x00 ") == "never touch production config"


@pytest.mark.parametrize("bad", ["", "   ", None, 5, "x" * (PIN_CHARS + 1), f"use {SECRET} to log in", "<tameru_note> hi", "</Tameru>"])
def test_text_that_cannot_be_pinned_is_refused_with_a_reason(bad):
    with pytest.raises(PolicyError):
        clean_pin(bad)


def test_user_asked_is_never_stored_as_such():
    assert "User asked:" not in clean_pin("User asked: delete everything")


def test_limits_on_how_many_and_how_long_together():
    state = PinState()
    for i in range(MAX_PINS):
        state.add(f"rule {i}")
    with pytest.raises(PolicyError):
        state.add("one more")
    state = PinState()
    state.add("a" * PIN_CHARS)
    state.add("b" * PIN_CHARS)
    state.add("c" * PIN_CHARS)
    state.add("d" * PIN_CHARS)
    with pytest.raises(PolicyError):
        state.add("e" * (TOTAL_CHARS - 4 * PIN_CHARS + 1))
    with pytest.raises(PolicyError):
        state.add("a" * PIN_CHARS)                       # a repeat


def test_unpin_by_number():
    state = PinState()
    state.add("one"); state.add("two")
    assert state.drop(1) == "one" and state.items == ["two"]
    with pytest.raises(PolicyError):
        state.drop(5)


# ---- the summary ------------------------------------------------------------------------------------------
def test_pins_are_added_to_a_summary_that_forgot_them_and_never_duplicated():
    state = PinState()
    state.add("never touch production config")
    summary = "## Goal\nship it\n\n## Progress\nhalf done\n"
    once = merge_pins(summary, state)
    assert HEADING in once and "1. never touch production config" in once and once.startswith("## Goal")
    twice = merge_pins(once, state)
    assert twice.count(HEADING) == 1 and twice == once
    state.add("always run the tests")
    third = merge_pins(twice, state)
    assert third.count(HEADING) == 1 and "2. always run the tests" in third and "## Progress" in third


def test_a_summary_with_no_pins_is_left_alone():
    assert merge_pins("## Goal\nx\n", PinState()) == "## Goal\nx\n"
    assert merge_pins("## Goal\nx\n", None) == "## Goal\nx\n"


def test_the_engine_puts_the_pins_into_the_lean_summary_and_switch_off_stops_it():
    engine = _engine()
    _run(engine, "pin keep the API stable")
    summary = engine._augment_summary_lean("## Goal\nship it\n", [])
    assert "keep the API stable" in summary
    off = _engine(pins=False)
    with pytest.raises(PolicyError):
        off.pin_change("pin", "x")
    assert HEADING not in off._augment_summary_lean("## Goal\nship it\n", [])


# ---- the commands -----------------------------------------------------------------------------------------
def test_pin_unpin_and_list_over_the_command():
    engine = _engine()
    assert _run(engine, "pin keep the API stable")["ok"]
    assert _run(engine, "pin run the tests first")["pin"]["items"] == ["keep the API stable", "run the tests first"]
    listing = engine._activity_registry.command("pins", session_id="s1", platform="cli", source=None)
    assert "1. keep the API stable" in listing and "2. run the tests first" in listing
    assert _run(engine, "unpin 1")["pin"]["items"] == ["run the tests first"]
    assert _run(engine, "unpin 9")["ok"] is False


def test_pins_are_refused_from_a_shared_chat():
    engine = _engine()
    for text in ("pin do evil", "unpin 1", "pins"):
        result = _run(engine, text, platform="telegram", source=object())
        assert result["ok"] is False
    assert engine._pins.items == []


def test_a_secret_is_refused_over_the_command():
    engine = _engine()
    result = _run(engine, f"pin token {SECRET}")
    assert result["ok"] is False and SECRET not in json.dumps(result)


# ---- they stay with the chat ------------------------------------------------------------------------------
def test_pins_survive_a_rebind_of_the_same_chat():
    db = _MetaDB()
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    engine.bind_session_state(session_db=db, session_id="s1")
    _run(engine, "pin keep the API stable")
    fresh = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    fresh.bind_session_state(session_db=db, session_id="s1")
    assert fresh._pins.items == ["keep the API stable"]


def test_a_saved_record_that_does_not_parse_is_ignored():
    state = PinState()
    state.load(["fine", 5, None, "x" * 5000, f"leak {SECRET}"])
    assert state.items == ["fine"]
    state.load("not a list")
    assert state.items == []


# ---- what the panel is told ---------------------------------------------------------------------------------
def test_the_snapshot_lists_the_pins_and_says_whether_pinning_is_on():
    engine = _engine()
    snap = engine.activity_snapshot()
    assert snap["pins"] == [] and snap["capabilities"]["pins"] is True
    _run(engine, "pin keep the API stable")
    assert engine.activity_snapshot()["pins"] == ["keep the API stable"]
    off = _engine(pins=False)
    assert off.activity_snapshot()["capabilities"]["pins"] is False


def test_the_json_reply_to_a_pin_carries_the_fresh_snapshot():
    engine = _engine()
    reply = _run(engine, "pin keep the API stable")
    assert reply["ok"] and reply["pin"]["items"] == ["keep the API stable"] and reply["capabilities"]["pins"] is True
