"""What the summarizer and Hermes' anchor index read of a Tameru row (``render.for_summary``).

A header-only row is cut to Hermes' own one-line summary; every other rung keeps its header minus the ``ref``.
Before, each header-only row was two to four times as long as Hermes' line in the summarizer prompt, and every
row's 16-hex ref landed in the summary's "commits:" anchor list, ahead of the session's real commit ids.
"""
from __future__ import annotations


import pytest
from engine_support import Chat, make_engine, prune_pass, pytest_failure_with_passes, server_log

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.render import for_summary, parse_header

PARENT = "[terminal] ran `helm -n shop list` -> exit 0, 17 lines output"
REF = "0123456789abcdef"
HEADER_ONLY = f"{PARENT} [tameru:header kept 0/17 lines (0 of 1,645 chars) omitted: text 16 ref={REF}]"
EXTRACT = (
    f"{PARENT} [tameru:extract kept 4/17 lines (210 of 1,645 chars) omitted: text 13 ref={REF}]\n"
    "line one\nline two"
)


def test_a_header_only_row_is_cut_to_hermes_line():
    assert for_summary(HEADER_ONLY) == PARENT


def test_a_row_with_a_body_loses_only_its_ref():
    seen = for_summary(EXTRACT)
    assert REF not in seen
    assert seen == EXTRACT.replace(f" ref={REF}]", "]")
    assert "[tameru:extract kept 4/17 lines (210 of 1,645 chars) omitted: text 13]" in seen
    assert seen.endswith("\nline one\nline two")


def test_a_superseded_header_keeps_its_note_without_the_ref():
    row = f"{PARENT} [tameru:superseded kept 0/17 lines (0 of 1,645 chars) superseded by msg 12 (terminal) ref={REF}]"
    seen = for_summary(row)
    assert "superseded by msg 12 (terminal)" in seen and REF not in seen


@pytest.mark.parametrize("content", [
    "plain tool output\nwith lines", "", "[terminal] ran `ls` -> exit 0, 1 lines output",
    "[tameru: not a header ref=0123456789abcdef]", 12, None, ["part"],
    # a row that merely quotes a header on a later line is not a rendered row
    f"intro\n{HEADER_ONLY}",
])
def test_anything_else_is_returned_as_is(content):
    assert for_summary(content) is content


def test_a_header_only_row_with_trailing_lines_is_not_cut():
    row = HEADER_ONLY + "\nstray line"
    assert for_summary(row) == row.replace(f" ref={REF}]", "]", 1)


def _rendered_session(engine):
    chat = Chat("why did the payment gateway time out?")
    idxs = [chat.tool("terminal", {"command": f"tail -n {400 + i} app.log"}, server_log(i)) for i in range(4)]
    idxs.append(chat.tool("terminal", {"command": "pytest -v"}, pytest_failure_with_passes()))
    out, count = prune_pass(engine, chat.finish())
    assert count
    return out, idxs


def test_the_summary_view_hides_every_ref_and_leaves_the_transcript_alone():
    engine = make_engine()
    messages, idxs = _rendered_session(engine)
    before = [dict(m) for m in messages]
    refs = {parse_header(messages[i]["content"])["ref"] for i in idxs}
    assert len(refs) == len(idxs)
    view = engine._summary_view(messages)
    assert messages == before, "the transcript is never rewritten"
    assert len(view) == len(messages)
    seen = "\n".join(m["content"] for m in view if m.get("role") == "tool" and isinstance(m.get("content"), str))
    assert not any(ref in seen for ref in refs)
    # rows with no ref in them are the same objects
    assert all(view[i] is messages[i] for i in range(len(messages)) if i not in idxs)


def test_the_anchor_index_has_no_refs():
    engine = make_engine()
    messages, idxs = _rendered_session(engine)
    refs = {parse_header(messages[i]["content"])["ref"] for i in idxs}
    out = engine._augment_summary_lean("## Goal\nx", messages)
    assert not any(ref in out for ref in refs)


@pytest.mark.real_hermes
def test_real_hermes_anchor_index_lists_commits_but_not_refs():
    """Hermes reads any 9 to 40 hex chars as a commit: the refs used to fill its cap of 40."""
    engine = make_engine()
    messages, idxs = _rendered_session(engine)
    refs = {parse_header(messages[i]["content"])["ref"] for i in idxs}
    commit = "9fceb02d0ae598e9"
    messages = [*messages, {
        "role": "tool", "tool_call_id": "call_git", "tool_name": "terminal",
        "content": f"[terminal] ran `git log -1` -> exit 0, 1 lines output\ncommit {commit}",
    }]
    out = engine._augment_summary_lean("## Goal\nx", messages)
    assert commit in out
    assert not any(ref in out for ref in refs)


@pytest.mark.real_hermes
def test_real_hermes_summarizer_records_use_the_view():
    engine = make_engine()
    messages, idxs = _rendered_session(engine)
    refs = {parse_header(messages[i]["content"])["ref"] for i in idxs}
    records = engine._serialize_records_for_summary(messages)
    assert records and not any(ref in "\n".join(records) for ref in refs)
    plain = make_engine(TameruSettings(enabled=False))._serialize_records_for_summary(messages)
    assert any(ref in "\n".join(plain) for ref in refs), "disabled: stock records, untouched"
