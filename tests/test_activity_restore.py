"""Activity rows are rebuilt after a restart, and only from rows an archived original vouches for."""
from __future__ import annotations

from engine_support import Chat, demote, make_engine, pytest_failure_with_passes

from hermes_tameru_plugin.payload import parse_payload

TASK = "why did the payment gateway time out?"


class _Archive:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def get_messages(self, session_id, include_compacted=False, **kwargs):
        return list(self.rows)

    def archive_and_compact(self, *args, **kwargs):
        return True


def _shortened_chat():
    """A session whose tool row Tameru shortened, with the archive row that holds the original."""
    engine = make_engine()
    chat = Chat(TASK)
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    archive_row = dict(messages[idx])
    engine.bind_session_state(session_db=_Archive([archive_row]), session_id="s1")
    changed, out = demote(engine, messages, idx)
    assert changed
    return idx, out, archive_row


def _restarted(archive_rows):
    engine = make_engine()
    engine.bind_session_state(session_db=_Archive(archive_rows), session_id="s1")
    return engine


def test_a_restart_rebuilds_the_table_from_archived_originals():
    idx, out, archive_row = _shortened_chat()
    engine = _restarted([archive_row])
    assert engine.activity_snapshot()["items"] == []
    engine._observe_activity(out)
    items = engine.activity_snapshot()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["recovered_after_restart"] is True and item["tool"] == "terminal"
    assert item["kept_lines"] < item["before_lines"] and item["live"] is True
    assert item["original"] in ("memory", "archive", "unchecked")
    original = parse_payload(archive_row["content"]).inner
    assert engine.inspect_original(item["ref"])["content"].startswith(original[:40])


def test_a_header_copied_onto_another_row_proves_nothing():
    idx, out, archive_row = _shortened_chat()
    forged = dict(out[idx], tool_call_id="someone-elses-call")
    engine = _restarted([archive_row])
    engine._observe_activity([*out[:idx], forged, *out[idx + 1:]])
    assert engine.activity_snapshot()["items"] == []


def test_a_header_with_no_archived_original_proves_nothing():
    idx, out, _ = _shortened_chat()
    engine = _restarted([])
    engine._observe_activity(out)
    assert engine.activity_snapshot()["items"] == []


def test_the_rebuild_waits_for_the_archive_and_runs_once_it_exists():
    idx, out, archive_row = _shortened_chat()
    engine = make_engine()
    engine._observe_activity(out)                       # no session archive bound yet
    assert engine.activity_snapshot()["items"] == []
    engine.bind_session_state(session_db=_Archive([archive_row]), session_id="s1")
    engine._observe_activity(out)
    assert len(engine.activity_snapshot()["items"]) == 1


def test_a_restored_row_is_not_announced_in_chat():
    idx, out, archive_row = _shortened_chat()
    engine = _restarted([archive_row])
    posted = []
    engine._activity_registry.post_message = lambda *a, **k: posted.append((a, k))
    engine._observe_activity(out)
    assert posted == []


class _Stored:
    """A chat's stored conversation: the live (shortened) rows, plus the archived originals behind them."""

    def __init__(self, live, archived):
        self.live, self.archived = list(live), list(archived)

    def get_messages(self, session_id, include_compacted=False, **kwargs):
        return [*self.archived, *self.live] if include_compacted else list(self.live)

    def archive_and_compact(self, *args, **kwargs):
        return True


def test_a_fresh_process_shows_the_earlier_changes_before_any_turn():
    idx, out, archive_row = _shortened_chat()
    live = [dict(row) for row in out]
    engine = make_engine()
    engine.bind_session_state(session_db=_Stored(live, [archive_row]), session_id="s1")
    snapshot = engine.activity_snapshot()              # what /tameru and the Desktop bar ask, with no turn run yet
    assert len(snapshot["items"]) == 1 and snapshot["items"][0]["recovered_after_restart"] is True
    assert snapshot["progress"]["savings"]["shortened"] == 1
    assert "1 outputs shortened" in __import__("hermes_tameru_plugin.progress", fromlist=["x"]).render_text(snapshot["progress"])


def test_a_fresh_process_without_an_archive_shows_nothing_and_keeps_trying():
    idx, out, archive_row = _shortened_chat()
    live = [dict(row) for row in out]
    engine = make_engine()
    engine.bind_session_state(session_db=_Stored(live, []), session_id="s1")      # originals missing
    assert engine.activity_snapshot()["items"] == []
    assert engine._activity.restore_due(), 'an ask-time read that found nothing must leave the turn-time restore available'


def test_the_stored_rows_are_read_once_per_lifecycle():
    idx, out, archive_row = _shortened_chat()
    stored = _Stored([dict(row) for row in out], [archive_row])
    reads = []
    original = stored.get_messages
    stored.get_messages = lambda *a, **k: (reads.append(1), original(*a, **k))[1]
    engine = make_engine()
    engine.bind_session_state(session_db=stored, session_id="s1")
    for _ in range(5):
        engine.activity_snapshot()
    first = len(reads)
    for _ in range(5):
        engine.activity_snapshot()
    assert len(reads) == first


def test_an_ask_time_miss_does_not_stop_the_turn_time_restore():
    idx, out, archive_row = _shortened_chat()
    engine = make_engine()
    engine.bind_session_state(session_db=_Archive([archive_row]), session_id="s1")   # stored rows lack the live ones
    assert engine.activity_snapshot()["items"] == []
    engine._observe_activity(out)                       # the live messages arrive with the next turn
    assert len(engine.activity_snapshot()["items"]) == 1


def test_a_stored_row_with_more_ids_than_the_pruned_one_still_matches_its_own_original():
    """Hermes returns rows with _tool_call_uid, message_uid and tool_call_id; the ref used one of them."""
    engine = make_engine()
    chat = Chat(TASK)
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    messages[idx]["message_uid"] = "m-uid-1"                       # what the in-memory row carried at prune time
    archive_row = dict(messages[idx])
    engine.bind_session_state(session_db=_Archive([archive_row]), session_id="s1")
    changed, out = demote(engine, messages, idx)
    assert changed
    stored = [dict(row) for row in out]
    stored[idx]["_tool_call_uid"] = "tool-call-uid-1"              # the stored copy carries an extra id
    fresh = _restarted([archive_row])
    fresh._observe_activity(stored)
    assert len(fresh.activity_snapshot()["items"]) == 1


def test_a_header_copied_onto_a_row_with_other_ids_proves_nothing_even_with_several_ids():
    engine = make_engine()
    chat = Chat(TASK)
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    messages[idx]["message_uid"] = "m-uid-1"
    archive_row = dict(messages[idx])
    engine.bind_session_state(session_db=_Archive([archive_row]), session_id="s1")
    changed, out = demote(engine, messages, idx)
    forged = dict(out[idx], message_uid="m-uid-2", tool_call_id="other", _tool_call_uid="tcu-other")
    fresh = _restarted([archive_row])
    fresh._observe_activity([*out[:idx], forged, *out[idx + 1:]])
    assert fresh.activity_snapshot()["items"] == []


def test_the_first_turn_after_a_restart_still_tells_the_model_what_is_shortened():
    """Nothing has opened the panel yet, so the note builder must rebuild the rows itself."""
    idx, out, archive_row = _shortened_chat()
    engine = _restarted([archive_row])
    assert engine._activity.rows() == []
    note = engine.manifest_for_turn(out)
    assert note and "Shortened tool results in this chat: 1" in note


def test_a_header_copied_onto_another_row_gives_the_model_no_note():
    idx, out, archive_row = _shortened_chat()
    forged = dict(out[idx], tool_call_id="someone-elses-call")
    engine = _restarted([archive_row])
    assert engine.manifest_for_turn([*out[:idx], forged, *out[idx + 1:]]) is None
