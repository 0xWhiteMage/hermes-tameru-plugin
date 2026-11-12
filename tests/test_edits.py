"""Keep and Restore: a request overlay that shows a shortened output in full, and only when it provably should."""
from __future__ import annotations

import copy
import dataclasses
import json
import random

import pytest
from engine_support import Chat, demote, make_engine, pytest_failure_with_passes
from test_autotune_engine import _MetaDB, bind_meta

from hermes_tameru_plugin import engine as engine_module
from hermes_tameru_plugin import engine_edits as edits_module   # the Keep/Restore code lives here
from hermes_tameru_plugin.activity import SURFACE_REFUSAL
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.edits import RESTORE_TURNS, TRIP_AFTER, EditState, held_budget_chars
from hermes_tameru_plugin.payload import parse_payload
from hermes_tameru_plugin.recovery import SECRET_SOURCE
from hermes_tameru_plugin.session_policy import PolicyError


def _build(db=None, **settings):
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **settings))
    bind_meta(engine, db, "s1")
    return engine


def _shortened(db=None, **settings):
    """An engine, the live history with one shortened tool row, that row's index and its original text."""
    engine = _build(db, **settings)
    chat = Chat("why did the gateway time out?")
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    original = parse_payload(messages[idx]["content"]).inner
    engine._archive_row = dict(messages[idx])            # what Hermes' own archive would hold for this row
    changed, out = demote(engine, messages, idx)
    assert changed
    engine._observe_activity(out)
    ref = engine.activity_snapshot()["items"][0]["ref"]
    return engine, out, idx, ref, original


def _cmd(engine, text, **context):
    return json.loads(engine._activity_registry.command("--json " + text, session_id="s1", platform="desktop", source=None, **context))


def _lifecycle(engine):
    return engine._activity.lifecycle_id


def _turn(engine):
    engine._activity_registry.pre_llm_call(session_id="s1", conversation_history=[])


# ---- nothing happens until the next turn -----------------------------------------------------------------
def test_a_restore_waits_for_the_next_turn_and_the_request_is_untouched_until_then():
    engine, out, idx, ref, original = _shortened()
    assert engine.edit_change("restore", ref)["message"].startswith("Restoring.")
    assert engine._edits_overlay(out) is None, "queued, not started: the next request is exactly what Hermes built"
    _turn(engine)
    overlaid = engine._edits_overlay(out)
    assert overlaid is not None and overlaid[idx]["content"] == original


def test_the_overlay_never_changes_the_list_it_was_given_or_the_roles_around_it():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    before = copy.deepcopy(out)
    overlaid = engine._edits_overlay(out)
    assert out == before, "the saved history and the request list stay byte-identical"
    assert overlaid is not out and len(overlaid) == len(out)
    assert [m["role"] for m in overlaid] == [m["role"] for m in out]
    assert [m.get("tool_call_id") for m in overlaid] == [m.get("tool_call_id") for m in out]
    assert [i for i, (a, b) in enumerate(zip(out, overlaid)) if a != b] == [idx]


def test_select_context_returns_the_overlay_and_nothing_when_there_is_no_edit():
    engine, out, idx, ref, original = _shortened()
    assert engine.select_context(out, conversation_messages=out) is None
    engine.edit_change("restore", ref)
    _turn(engine)
    result = engine.select_context(out, conversation_messages=out)
    assert result is not None and result[idx]["content"] == original


# ---- applied means the provider accepted the request carrying it ---------------------------------------------------------
def test_a_state_is_pending_until_a_request_starts_and_applied_after():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    assert engine.activity_snapshot()["edits"][ref]["state"] == "pending"
    _turn(engine)
    engine._edits_overlay(out)
    assert engine.activity_snapshot()["edits"][ref]["state"] == "pending", "building the overlay is not sending it"
    engine._activity_registry.observe_request(session_id="s1", started_at=1.0, api_request_id="t1:api:1")
    assert engine.activity_snapshot()["edits"][ref]["state"] == "pending", "a request that started is not one that was accepted"
    engine._activity_registry.observe_response(session_id="s1", api_request_id="t1:api:1")
    assert engine.activity_snapshot()["edits"][ref]["state"] == "applied"


def test_a_request_with_no_overlay_does_not_flip_anything_to_applied():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    engine._activity_registry.observe_request(session_id="s1", started_at=1.0)
    assert engine.activity_snapshot()["edits"][ref]["state"] == "pending"


# ---- a row counts only if it is provably the one Tameru shortened -----------------------------------------
def test_a_header_copied_onto_another_row_is_never_replaced():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    forged = dict(out[idx], tool_call_id="someone-elses-call")
    request = [*out[:idx], forged, *out[idx + 1:]]
    assert engine._edits_overlay(request) is None
    assert engine.activity_snapshot()["edits"][ref]["state"] == "failed"


def test_a_row_whose_bytes_changed_since_tameru_shortened_it_is_left_alone():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    changed = dict(out[idx], content=out[idx]["content"] + "\nsomething was appended")
    assert engine._edits_overlay([*out[:idx], changed, *out[idx + 1:]]) is None


# ---- refusals, each with one short reason -------------------------------------------------------------------
def test_web_content_can_only_be_re_read_never_restored_or_kept():
    engine, out, idx, ref, original = _shortened()
    change = engine._activity.get(ref)
    engine._activity._rows[ref] = dataclasses.replace(change, external=True)
    for verb in ("restore", "keep"):
        with pytest.raises(PolicyError, match="Web output"):
            engine.edit_change(verb, ref)


def test_a_credential_in_the_original_is_refused_as_a_held_secret(monkeypatch):
    engine, out, idx, ref, original = _shortened()
    monkeypatch.setattr(edits_module, "lookup_original", lambda *a, **k: (None, SECRET_SOURCE))
    with pytest.raises(PolicyError, match="Held a secret"):
        engine.edit_change("restore", ref)


def test_an_original_that_is_gone_is_refused_with_the_re_read_hint(monkeypatch):
    engine, out, idx, ref, original = _shortened()
    monkeypatch.setattr(edits_module, "lookup_original", lambda *a, **k: (None, "archive"))
    with pytest.raises(PolicyError, match="Re-Read"):
        engine.edit_change("keep", ref)


@pytest.mark.parametrize("bad", ["", "zz", "0" * 16])
def test_an_unknown_reference_is_refused(bad):
    engine, *_ = _shortened()
    with pytest.raises(PolicyError, match="no shortened output"):
        engine.edit_change("restore", bad)


def test_edits_can_be_switched_off():
    engine, out, idx, ref, original = _shortened(edits=False)
    assert engine.activity_snapshot()["capabilities"]["restore"] is False
    with pytest.raises(PolicyError, match="off"):
        engine.edit_change("restore", ref)
    assert engine._edits_overlay(out) is None


# ---- the memory held back --------------------------------------------------------------------------------
def test_the_held_budget_is_a_share_of_hermes_compaction_point_with_a_ceiling():
    assert held_budget_chars(100_000) == 15_000 * 4
    assert held_budget_chars(10_000_000) == 40_000 * 4
    assert held_budget_chars(0) == held_budget_chars(None) == 10_000 * 4


def test_a_restore_that_does_not_fit_is_refused_and_a_late_overflow_fails_honestly():
    engine, out, idx, ref, original = _shortened()
    engine.threshold_tokens = 10                       # a window so small that the original cannot be held
    with pytest.raises(PolicyError, match="Too big"):
        engine.edit_change("restore", ref)
    engine.threshold_tokens = 200_000
    engine.edit_change("restore", ref)
    _turn(engine)
    engine.threshold_tokens = 10                       # the budget shrank after the edit was queued
    assert engine._edits_overlay(out) is None
    entry = engine.activity_snapshot()["edits"][ref]
    assert entry["state"] == "failed" and entry["reason"] == "Too big"


def test_a_keep_is_refused_with_its_own_wording_when_the_limit_is_reached():
    engine, out, idx, ref, original = _shortened()
    engine.threshold_tokens = 10
    with pytest.raises(PolicyError, match="Keep limit reached"):
        engine.edit_change("keep", ref)


# ---- restores let go, keeps stay ---------------------------------------------------------------------------
def test_a_restore_lasts_a_few_replies_and_then_lets_go():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    for _ in range(RESTORE_TURNS):
        _turn(engine)
        assert engine._edits_overlay(out) is not None
    _turn(engine)
    assert engine._edits_overlay(out) is None
    assert engine.activity_snapshot()["edits"][ref]["state"] == "expired"


def test_a_keep_does_not_expire_and_unkeep_lets_it_go():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("keep", ref)
    for _ in range(RESTORE_TURNS * 3):
        _turn(engine)
    assert engine._edits_overlay(out)[idx]["content"] == original
    assert engine.edit_change("unkeep", ref)["message"] == "Unpinned."
    assert engine._edits_overlay(out) is None


def test_a_keep_survives_a_restart_and_applies_again_once_the_row_is_back():
    db = _MetaDB()
    engine, out, idx, ref, original = _shortened(db)
    engine.edit_change("keep", ref)
    db.get_messages = lambda session_id, include_compacted=False, **kw: [engine._archive_row]
    restarted = _build(db)
    assert restarted._edits.pins() == [ref]
    _turn(restarted)
    result = restarted.select_context(out, conversation_messages=out)
    assert result is not None and result[idx]["content"] == original


def test_cancel_drops_what_has_not_started_but_not_what_is_running():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    assert engine.edit_change("cancel")["message"] == "Cancelled 1 pending change."
    assert engine.activity_snapshot()["edits"] == {}
    engine.edit_change("restore", ref)
    _turn(engine)
    assert engine.edit_change("cancel")["message"] == "Cancelled 0 pending changes."
    assert engine._edits_overlay(out) is not None


def test_a_keep_outranks_a_restore_of_the_same_output():
    state = EditState()
    state.request("a" * 16, "keep", 10)
    state.request("a" * 16, "restore", 10)
    assert state.entries["a" * 16]["kind"] == "keep"


# ---- failing safe ------------------------------------------------------------------------------------------
def test_an_error_while_building_the_overlay_leaves_the_request_alone_and_trips_after_three(monkeypatch):
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    def boom(change):
        raise RuntimeError("archive exploded")
    monkeypatch.setattr(engine, "_edit_original", boom)
    for _ in range(TRIP_AFTER):
        assert engine._edits_overlay(out) is None
    assert engine._edits.tripped and engine.edits_enabled() is False
    assert engine.activity_snapshot()["capabilities"]["restore"] is False
    assert engine.select_context(out, conversation_messages=out) is None


def test_a_missing_row_is_reported_as_gone_not_applied():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    assert engine._edits_overlay([m for i, m in enumerate(out) if i != idx]) is None
    entry = engine.activity_snapshot()["edits"][ref]
    assert entry["state"] == "failed" and "history" in entry["reason"]


def test_randomised_conversations_keep_the_overlay_safe():
    rng = random.Random(7)
    for _ in range(25):
        engine = _build()
        engine.threshold_tokens = 1_000_000
        chat = Chat("investigate")
        indexes = [chat.tool("terminal", {"command": f"cmd {n}"}, pytest_failure_with_passes()) for n in range(rng.randint(1, 3))]
        messages = chat.finish()
        shown = messages
        for idx in indexes:
            changed, shown = demote(engine, shown, idx)
        engine._observe_activity(shown)
        refs = [item["ref"] for item in engine.activity_snapshot()["items"]]
        for ref in rng.sample(refs, rng.randint(0, len(refs))):
            engine.edit_change(rng.choice(["keep", "restore"]), ref)
        _turn(engine)
        before = copy.deepcopy(shown)
        overlaid = engine._edits_overlay(shown)
        assert shown == before
        if overlaid is not None:
            assert len(overlaid) == len(shown) and [m["role"] for m in overlaid] == [m["role"] for m in shown]
            assert engine._edits_overlay(overlaid) is None, "applying twice changes nothing more"


# ---- the commands -------------------------------------------------------------------------------------------
def test_the_verbs_work_from_the_desktop_with_a_lifecycle_guard():
    engine, out, idx, ref, original = _shortened()
    result = _cmd(engine, f"restore {ref} --lifecycle {_lifecycle(engine)}")
    assert result["ok"] is True and result["kind"] == "edit" and result["edits"][ref]["state"] == "pending"
    assert "next message" in result["edit"]["message"]
    stale = _cmd(engine, f"keep {ref} --lifecycle not-the-current-one")
    assert stale["ok"] is False and "session changed" in stale["error"]
    missing = _cmd(engine, f"keep {ref}")
    assert missing["ok"] is False and "Refresh" in missing["error"]


def test_a_refusal_comes_back_as_one_short_reason():
    engine, out, idx, ref, original = _shortened(edits=False)
    result = _cmd(engine, f"restore {ref} --lifecycle {_lifecycle(engine)}")
    assert result["ok"] is False and result["error"] == "Keep and Restore are off for this chat."


@pytest.mark.parametrize("verb", ["keep", "unkeep", "restore", "cancel"])
def test_a_messaging_platform_cannot_change_what_the_model_sees(verb):
    engine, out, idx, ref, original = _shortened()
    arg = verb if verb == "cancel" else f"{verb} {ref}"
    result = json.loads(engine._activity_registry.command("--json " + arg, session_id="s1", platform="telegram", source=object()))
    assert result["ok"] is False and result["error"] == SURFACE_REFUSAL
    assert engine._edits.entries == {}


def test_the_text_reply_says_what_happened():
    engine, out, idx, ref, original = _shortened()
    text = engine._activity_registry.command(f"restore {ref}", session_id="s1", platform="cli", source=None)
    assert text.startswith("Restoring.")


# ---- never be the thing that tips a chat over Hermes' compaction point ------------------------------------
def test_a_restore_is_refused_when_it_would_push_the_chat_past_hermes_compaction_point(monkeypatch):
    engine, out, idx, ref, original = _shortened()
    monkeypatch.setattr(edits_module, "held_budget_chars", lambda _threshold: 10**9)    # isolate the pressure check
    engine.threshold_tokens = 100_000
    engine.last_real_prompt_tokens = 99_000                  # the last real reply already used almost all of it
    with pytest.raises(PolicyError, match="Memory nearly full"):
        engine.edit_change("restore", ref)
    assert engine._edits.entries == {}
    engine.last_real_prompt_tokens = 20_000                  # plenty of room
    assert engine.edit_change("restore", ref)["message"].startswith("Restoring.")


def test_unknown_sizes_never_block_a_restore():
    engine, out, idx, ref, original = _shortened()
    engine.last_real_prompt_tokens = None
    assert engine.edit_change("keep", ref)["message"].startswith("Kept.")
    assert engine._edit_would_crowd(10**9) is False or engine.threshold_tokens > 0


def test_the_request_overlay_backs_off_when_the_chat_is_too_full_and_leaves_the_request_alone(monkeypatch):
    engine, out, idx, ref, original = _shortened()
    monkeypatch.setattr(edits_module, "held_budget_chars", lambda _threshold: 10**9)
    engine.edit_change("restore", ref)
    _turn(engine)
    base = engine_module.message_budget_tokens(out)
    engine.threshold_tokens = base + 5                       # no room for the original on top of what is already sent
    before = copy.deepcopy(out)
    assert engine._edits_overlay(out) is None and out == before
    entry = engine.activity_snapshot()["edits"][ref]
    assert entry["state"] == "failed" and entry["reason"] == "Memory nearly full"


def test_the_overlay_applies_again_when_there_is_room(monkeypatch):
    engine, out, idx, ref, original = _shortened()
    monkeypatch.setattr(edits_module, "held_budget_chars", lambda _threshold: 10**9)
    engine.edit_change("restore", ref)
    _turn(engine)
    engine.threshold_tokens = engine_module.message_budget_tokens(out) * 20
    assert engine._edits_overlay(out)[idx]["content"] == original


# ---- the provider's answer decides: accepted, retried, failed, never answered -------------------------------
def _sent(engine, out, rid="t1:api:1"):
    engine._activity_registry.observe_request(session_id="s1", started_at=1.0, api_request_id=rid, request_messages=out)


def test_a_failed_request_that_will_be_retried_leaves_the_edit_pending():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    request = engine._edits_overlay(out)
    _sent(engine, request)
    engine._activity_registry.observe_request_error(session_id="s1", api_request_id="t1:api:1", retryable=True)
    assert engine.activity_snapshot()["edits"][ref]["state"] == "pending"
    request = engine._edits_overlay(out)
    _sent(engine, request, "t1:api:1")
    engine._activity_registry.observe_response(session_id="s1", api_request_id="t1:api:1")
    assert engine.activity_snapshot()["edits"][ref]["state"] == "applied"


def test_a_request_that_failed_for_good_reads_failed_not_sent():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    _sent(engine, engine._edits_overlay(out))
    engine._activity_registry.observe_request_error(session_id="s1", api_request_id="t1:api:1", retryable=False)
    state = engine.activity_snapshot()["edits"][ref]
    assert state["state"] == "failed" and state["reason"] == "Not sent"


def test_an_edit_missing_from_the_request_hermes_built_is_never_applied():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    engine._edits_overlay(out)
    _sent(engine, out)                                       # Hermes sent the shortened rows, not the overlay
    engine._activity_registry.observe_response(session_id="s1", api_request_id="t1:api:1")
    state = engine.activity_snapshot()["edits"][ref]
    assert state["state"] == "failed" and state["reason"] == "Not in the request"


def test_a_response_for_another_request_does_not_apply_this_one():
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    _sent(engine, engine._edits_overlay(out), "t1:api:2")
    engine._activity_registry.observe_response(session_id="s1", api_request_id="t1:api:1")
    assert engine.activity_snapshot()["edits"][ref]["state"] == "pending"


def test_unconfirmed_edits_are_counted_without_content():
    from hermes_tameru_plugin.edits import UNCONFIRMED
    engine, out, idx, ref, original = _shortened()
    engine.edit_change("restore", ref)
    _turn(engine)
    before = UNCONFIRMED["count"]
    _sent(engine, engine._edits_overlay(out))
    engine.begin_turn_edits()                                # the turn ended and the provider never answered
    assert UNCONFIRMED["count"] == before + 1
