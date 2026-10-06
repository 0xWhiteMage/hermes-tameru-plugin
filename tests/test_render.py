"""``render``: the frozen header grammar, detection, refs, omitted categories and rung rewrites.

Hermes' ``_is_summary_stub`` (real Hermes, or the stub's verbatim copy) judges what a header-only
row must look like: a row that fails it would be re-summarized by every later prune pass.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
from agent.context_compressor import _is_summary_stub

from hermes_tameru_plugin import render
from hermes_tameru_plugin.render import (
    HEADER_RE,
    RUNGS,
    TAMERU_TAG,
    RenderStats,
    build_header,
    classify_render,
    header_only,
    is_tameru_rendered,
    make_ref,
    omitted_categories,
    parse_header,
    retarget_rung,
)
from hermes_tameru_plugin.tameru.compress_context import compress_context

REF = "ab12cd34"
PARENT = "[terminal] ran `pytest -x` -> exit 1, 1 lines output"
LONG_PATH = "/" + "/".join(["very-long-directory-name"] * 80)   # ~2,000 chars


def stats(**overrides) -> RenderStats:
    fields = dict(
        kept_lines=40, total_lines=220, kept_chars=1203, total_chars=15000,
        omitted=(("log", 150), ("text", 30)), rung="extract",
    )
    return RenderStats(**{**fields, **overrides})


def assert_stub(header: str) -> None:
    assert header.startswith("[") and " chars)" in header
    assert "\n" not in header and len(header) < 400
    assert _is_summary_stub(header), "Hermes would re-summarize this row"


# ---- the grammar -------------------------------------------------------------------------------
def test_grammar_is_frozen():
    assert TAMERU_TAG == " [tameru:"
    assert RUNGS == ("fold", "extract", "brief", "header", "superseded")
    assert HEADER_RE.pattern == (
        r"^\[[^\n]*? \[tameru:(fold|extract|brief|header|superseded) [^\n]*? ref=([0-9a-f]{8})\]"
    )


def test_header_text_is_exact():
    assert build_header(PARENT, stats(), REF) == (
        "[terminal] ran `pytest -x` -> exit 1, 1 lines output [tameru:extract kept 40/220 lines "
        "(1,203 of 15,000 chars) omitted: log 150, text 30 ref=ab12cd34]"
    )


def test_header_without_categories_or_note():
    header = build_header(PARENT, stats(omitted=()), REF)
    assert header.endswith("(1,203 of 15,000 chars) ref=ab12cd34]")


def test_superseded_note_follows_the_categories():
    header = build_header(PARENT, stats(rung="superseded", note="superseded by msg 41 (re-read)"), REF)
    assert header.endswith(
        "chars) omitted: log 150, text 30 superseded by msg 41 (re-read) ref=ab12cd34]"
    )
    note_only = build_header(PARENT, stats(rung="superseded", omitted=(), note="superseded by msg 41 (re-read)"), REF)
    assert note_only.endswith("chars) superseded by msg 41 (re-read) ref=ab12cd34]")


@pytest.mark.parametrize("rung", RUNGS)
@pytest.mark.parametrize("omitted", [(), (("log", 3),), (("log", 150), ("text", 30), ("json", 2))])
@pytest.mark.parametrize("note", ["", "superseded by msg 41 (re-read)"])
def test_header_round_trips_through_parse_header(rung, omitted, note):
    header = build_header(PARENT, stats(rung=rung, omitted=omitted, note=note), REF)
    assert parse_header(header) == {"rung": rung, "ref": REF, "kept": 40, "total": 220}
    match = HEADER_RE.match(header)
    assert match and match.group(1) == rung and match.group(2) == REF
    assert classify_render(header) == "v1" and is_tameru_rendered(header)
    assert_stub(header)


def test_parse_header_reads_only_the_first_line_of_a_full_row():
    row = build_header(PARENT, stats(), REF) + '\n{"exit_code":1}\n⟪body⟫'
    assert parse_header(row) == {"rung": "extract", "ref": REF, "kept": 40, "total": 220}


def test_parent_line_quoting_a_header_cannot_hijack_the_parse():
    forged = "[terminal] ran `echo [tameru:fold kept 9/9 lines (1 of 1 chars) ref=00000000]` -> exit 0"
    row = build_header(forged, stats(), REF)
    assert parse_header(row) == {"rung": "extract", "ref": REF, "kept": 40, "total": 220}
    cut = retarget_rung(row, "header")
    assert cut is not None and parse_header(cut)["ref"] == REF and parse_header(cut)["total"] == 220


@pytest.mark.parametrize("content", [
    "", "plain text", PARENT, None, 7, ["x"],
    "[terminal] ran x [tameru:extract kept 1/2 lines (3 of 4 chars) ref=NOTHEX00]",
    "[terminal] ran x [tameru:extract kept 1/2 lines (3 of 4 chars) ref=ab12cd34] trailing",
    "[terminal] ran x [tameru:bogus kept 1/2 lines (3 of 4 chars) ref=ab12cd34]",
])
def test_parse_header_rejects_what_is_not_a_header(content):
    assert parse_header(content) is None


def test_parent_line_without_bracket_is_wrapped():
    header = build_header("ran pytest", stats(), REF)
    assert header.startswith("[ran pytest] [tameru:extract kept 40/220 lines")
    assert_stub(header)
    assert build_header("", stats(), REF).startswith("[] [tameru:")


@pytest.mark.parametrize("parent", [
    "[terminal] ran `a\nb` -> exit 0", "[x]\r\n[y]", "[x]\u2028[y]\x0b[z]", "\n[x]",
])
def test_header_is_always_one_line(parent):
    header = build_header(parent, stats(note="two\nlines"), REF)
    assert len(header.splitlines()) == 1
    assert parse_header(header)["ref"] == REF


# ---- length limits -----------------------------------------------------------------------------
FAILED_TAIL = "from line 1 (12,345 chars) FAILED: File not found"
WORST_PARENTS = {
    "failed read of a 2000-char path": f"[read_file] read {LONG_PATH} {FAILED_TAIL}",
    "2000-char search pattern": f"[search_files] content search for '{'a' * 2000}' in . -> 3 matches",
    "no bracket, 2000 chars": "x" * 2000,
    "multibyte": "[terminal] ran `echo 日本語…`" * 100,
    "empty": "",
}


@pytest.mark.parametrize("parent", list(WORST_PARENTS.values()), ids=list(WORST_PARENTS))
def test_worst_case_parent_lines_fit_and_stay_stubs(parent):
    worst = stats(
        omitted=(("definition", 1234567), ("import", 99999), ("heading", 88888)),
        kept_chars=123_456_789, total_chars=987_654_321, note="superseded by msg 41 (" + "x" * 80 + ")",
    )
    header = build_header(parent, worst, REF)
    assert len(header) <= 380
    assert_stub(header)
    assert parse_header(header) == {"rung": "extract", "ref": REF, "kept": 40, "total": 220}


def test_a_shortened_parent_keeps_its_failure_tail():
    header = build_header(WORST_PARENTS["failed read of a 2000-char path"], stats(), REF)
    assert len(header) == 380
    assert "…" in header and f"{FAILED_TAIL} [tameru:" in header
    assert header.startswith("[read_file] read /very-long-directory-name")


def test_a_short_parent_line_is_untouched():
    assert build_header(PARENT, stats(), REF).startswith(PARENT + " [tameru:")


def test_max_len_is_capped_below_the_stub_limit():
    header = build_header("[x] " + "y" * 3000, stats(), REF, max_len=5000)
    assert len(header) == 399
    assert_stub(header)


def test_small_max_len_shortens_the_parent_then_categories_then_the_note():
    note = "superseded by msg 41 (re-read)"
    cats = "omitted: log 150, text 30, json 2"
    kwargs = dict(note=note, omitted=(("log", 150), ("text", 30), ("json", 2)))
    parent = "[" + "p" * 500
    floor = len(build_header("[" + "p" * 23, stats(omitted=()), REF))   # parent line at its 24-char floor
    # Short by 10 chars: only the parent line shrinks.
    full = len(build_header(parent, stats(**kwargs), REF, max_len=399))
    cut = build_header(parent, stats(**kwargs), REF, max_len=full - 10)
    assert len(cut) == full - 10 and f"{cats} {note} ref=" in cut
    # Room for the floor of the parent line plus everything else but 10 chars: the categories give.
    tight = build_header(parent, stats(**kwargs), REF, max_len=floor + 2 + len(cats) + len(note) - 10)
    assert tight.startswith("[" + "p" * 11 + "…" + "p" * 11 + " [tameru:")
    assert len(tight) == floor + 2 + len(cats) + len(note) - 10
    assert "omitted: log 150, text… " + note + " ref=" in tight
    # The categories at their floor and 6 chars still missing: the note gives too.
    tighter = build_header(parent, stats(**kwargs), REF, max_len=floor + 2 + 8 + len(note) - 6)
    assert len(tighter) == floor + 2 + 8 + len(note) - 6
    assert tighter.endswith("chars) omitted… superseded by msg 41 (r… ref=ab12cd34]")
    assert parse_header(tighter)["ref"] == REF


@pytest.mark.real_hermes
@pytest.mark.parametrize("tool, args, content", [
    ("read_file", {"path": LONG_PATH}, json.dumps({"error": "File not found", "content": ""})),
    ("read_file", {"path": LONG_PATH, "offset": 500}, "x" * 3000),
    ("search_files", {"pattern": "q" * 2000, "path": LONG_PATH}, json.dumps({"total_count": 12})),
    ("web_search", {"query": "w" * 3000}, json.dumps({"success": False, "error": "rate limited"})),
    ("patch", {"path": LONG_PATH, "mode": "replace"}, json.dumps({"success": False, "error": "no match"})),
    ("terminal", {"command": "echo " + "z" * 3000}, json.dumps({"output": "x" * 900, "exit_code": 2})),
    ("web_extract", {"urls": ["https://example.com/" + "u" * 2000]}, "x" * 900),
])
def test_headers_built_on_hermes_own_summaries_are_stubs(tool, args, content):
    """What the engine passes as ``parent_line``: Hermes' summary of the original result."""
    from agent.context_compressor import _summarize_tool_result

    parent = _summarize_tool_result(tool, json.dumps(args), content)
    header = build_header(parent, stats(), REF)
    assert len(header) <= 380
    assert_stub(header)
    if "FAILED" in parent:
        assert "FAILED" in header, "the outcome of a failed call must survive the shortening"


# ---- detection ---------------------------------------------------------------------------------
def test_a_row_quoting_a_header_after_the_first_line_is_not_detected():
    header = build_header(PARENT, stats(), REF)
    for content in (f"plain first line\n{header}", f"[note] another stub\n{header}", f"output:\n{header}\nmore"):
        assert classify_render(content) is None and not is_tameru_rendered(content)
        assert parse_header(content) is None


def test_source_code_mentioning_the_header_is_not_detected():
    source = Path(render.__file__).read_text(encoding="utf-8")
    assert "[tameru:" in source and "HEADER_RE" in source
    wrapped = json.dumps({"content": source, "total_lines": source.count("\n") + 1}, ensure_ascii=False)
    terminal = json.dumps({"output": source, "exit_code": 0, "error": None})
    quoted = f"    HEADER_RE = re.compile(r\"^\\[[^\\n]*? \\[tameru:(fold|extract)\")\n{source}"
    for content in (source, wrapped, terminal, quoted, "x = '[tameru:fold kept 1/2 lines (1 of 2 chars) ref=deadbeef]'"):
        assert classify_render(content) is None


def test_a_json_array_result_is_not_detected():
    content = json.dumps([{"title": "a [tameru: b", "n": i} for i in range(200)])
    assert content.startswith("[") and classify_render(content) is None


def test_legacy_rows_of_plugin_1_3_0_are_recognised():
    """The 1.3.0 plugin joined the kept blocks with a bare ``[…]`` line (the engine's default gap)."""
    funcs = "\n".join(f"def handler_{i}(request):\n    return request.get('k{i}') * {i}\n" for i in range(60))
    row = compress_context(funcs, "what does handler_30 return?", ccr=False, citations=False).compressed_text
    assert "\n[…]\n" in row
    assert classify_render(row) == "legacy" and is_tameru_rendered(row)
    assert parse_header(row) is None


@pytest.mark.parametrize("content", [
    "def a():\n    pass\n[…]\ndef b():\n    pass",
    "[…]\nfirst",
    "last\n[…]",
])
def test_legacy_signature_is_a_line_equal_to_the_gap(content):
    assert classify_render(content) == "legacy"


@pytest.mark.parametrize("content", [
    '{\n  "a": 1,\n[…]\n}',                 # starts with "{": a JSON tool result
    "text with a gap […] in the middle",
    "[…] leading",
    "trailing […]",
    " […]",
    "[...]",
    "",
    "plain text",
])
def test_legacy_signature_needs_the_exact_line(content):
    assert classify_render(content) is None


def test_a_current_row_that_also_holds_gap_lines_is_current():
    row = build_header(PARENT, stats(), REF) + "\nkept\n[…]\nkept"
    assert classify_render(row) == "v1"


@pytest.mark.parametrize("content", [None, 5, b"gap", ["[…]"], {"a": 1}])
def test_non_strings_are_never_rendered(content):
    assert classify_render(content) is None and not is_tameru_rendered(content)


# ---- refs --------------------------------------------------------------------------------------
def expected_ref(uid: str, original: str) -> str:
    inner = hashlib.sha256(original.encode()).hexdigest()
    return hashlib.sha256(f"{uid}|{inner}".encode()).hexdigest()[:8]


def test_ref_formula_is_pinned():
    msg = {"_tool_call_uid": "uid-1", "message_uid": "m" * 32, "tool_call_id": "call_1"}
    ref = make_ref(msg, "original text")
    assert ref == expected_ref("uid-1", "original text")
    assert len(ref) == 8 and ref == ref.lower() and int(ref, 16) >= 0


def test_ref_is_stable():
    msg = {"tool_call_id": "call_1"}
    assert make_ref(msg, "x" * 5000) == make_ref(dict(msg), "x" * 5000)
    assert make_ref(msg, "a") != make_ref(msg, "b")
    assert make_ref({"tool_call_id": "call_2"}, "a") != make_ref(msg, "a")


def test_ref_uid_precedence():
    msg = {"_tool_call_uid": "t", "message_uid": "m", "tool_call_id": "c"}
    assert make_ref(msg, "o") == expected_ref("t", "o")
    del msg["_tool_call_uid"]
    assert make_ref(msg, "o") == expected_ref("m", "o")
    del msg["message_uid"]
    assert make_ref(msg, "o") == expected_ref("c", "o")
    del msg["tool_call_id"]
    assert make_ref(msg, "o") == expected_ref("", "o")


@pytest.mark.parametrize("empty", ["", None])
def test_ref_skips_empty_uids(empty):
    msg = {"_tool_call_uid": empty, "message_uid": "m", "tool_call_id": "c"}
    assert make_ref(msg, "o") == expected_ref("m", "o")


def test_ref_ignores_everything_but_the_ids():
    base = {"tool_call_id": "c"}
    noisy = {**base, "role": "tool", "content": "something else", "tool_name": "terminal"}
    assert make_ref(base, "o") == make_ref(noisy, "o")


def test_ref_survives_odd_text():
    assert len(make_ref({"tool_call_id": 123}, "lone surrogate \ud800 and 日本語")) == 8


# ---- omitted categories ------------------------------------------------------------------------
LOG = "2024-01-01T00:00:01 INFO started worker"
TRACE = "Traceback (most recent call last):"
TEXT = "some plain words here"
JSON_LINE = '"key": "value"'


def test_omitted_categories_count_dropped_lines_by_kind():
    original = "\n".join([LOG, LOG + " 2", LOG + " 3", TRACE, TEXT, JSON_LINE, TEXT + " too"])
    kept = "\n".join([LOG, TRACE])
    assert omitted_categories(original, kept) == (("log", 2), ("text", 2), ("json", 1))


def test_omitted_categories_order_by_count_then_name():
    original = "\n".join([TEXT, TRACE, LOG, JSON_LINE])
    assert omitted_categories(original, "") == (("json", 1), ("log", 1), ("text", 1))
    assert omitted_categories(original, "", limit=2) == (("json", 1), ("log", 1))
    assert omitted_categories(original, "", limit=0) == ()


def test_omitted_categories_ignore_blank_lines():
    assert omitted_categories(f"\n\n{TEXT}\n   \n\t\n", "") == (("text", 1),)
    assert omitted_categories("\n\n\n", "") == ()


def test_omitted_categories_match_lines_as_a_multiset():
    assert omitted_categories("\n".join([TEXT] * 4), TEXT) == (("text", 3),)
    assert omitted_categories(TEXT, "\n".join([TEXT] * 4)) == ()


def test_omitted_categories_ignore_marker_lines_and_whitespace_differences():
    original = f"{TEXT}\n{LOG}\n"
    kept = f"{TEXT}   \n⟪HERMES-CONTEXT-COMPRESSION: 40 of 80 chars omitted here by Hermes's context compressor.⟫"
    assert omitted_categories(original, kept) == (("log", 1),)


def test_omitted_categories_normalise_newlines():
    assert omitted_categories(f"{TEXT}\r\n{LOG}\r\n", f"{TEXT}\n{LOG}") == ()
    assert omitted_categories(f"{TEXT}\r{LOG}", f"{TEXT}\n") == (("log", 1),)


def test_nothing_dropped_means_no_categories():
    assert omitted_categories(f"{TEXT}\n{LOG}", f"{LOG}\n{TEXT}") == ()


# ---- composing and rewriting rows --------------------------------------------------------------
def test_render_layout():
    header = build_header(PARENT, stats(), REF)
    meta = '{"exit_code":1,"error":null}'
    assert render.render(PARENT, stats(), REF) == header
    assert render.render(PARENT, stats(), REF, meta_line=meta) == f"{header}\n{meta}"
    assert render.render(PARENT, stats(), REF, body="a\nb") == f"{header}\na\nb"
    assert render.render(PARENT, stats(), REF, meta_line=meta, body="a\nb") == f"{header}\n{meta}\na\nb"
    assert classify_render(render.render(PARENT, stats(), REF, meta_line=meta, body="a")) == "v1"


def test_header_only_from_a_parent_line_is_the_header():
    assert header_only(PARENT, stats(), REF) == build_header(PARENT, stats(), REF)
    assert header_only("ran pytest", stats(rung="header"), REF).startswith("[ran pytest] [tameru:header ")


def test_header_only_reuses_the_parent_line_of_a_rendered_row():
    row = render.render(PARENT, stats(), REF, meta_line='{"exit_code":1}', body="body")
    out = header_only(row, stats(rung="superseded", note="superseded by msg 3 (repeated-call)"), "00000000")
    assert out.startswith(PARENT + " [tameru:superseded ")
    assert "\n" not in out and out.endswith("superseded by msg 3 (repeated-call) ref=00000000]")


def test_header_only_takes_the_first_line_of_other_text():
    assert header_only("[a] first\nsecond", stats(), REF).startswith("[a] first [tameru:")


def test_retarget_rung_cuts_a_row_down_to_its_header():
    row = render.render(PARENT, stats(), REF, meta_line='{"exit_code":1}', body="line1\nline2")
    out = retarget_rung(row, "header")
    assert out == build_header(PARENT, stats(rung="header"), REF)
    assert parse_header(out) == {"rung": "header", "ref": REF, "kept": 40, "total": 220}
    assert_stub(out)


def test_retarget_rung_keeps_stats_note_and_ref():
    row = render.render(PARENT, stats(rung="superseded", note="superseded by msg 41 (re-read)"), REF, body="x")
    out = retarget_rung(row, "header")
    assert out == build_header(PARENT, stats(rung="header", note="superseded by msg 41 (re-read)"), REF)


def test_retarget_rung_is_idempotent():
    row = render.render(PARENT, stats(), REF, body="line")
    once = retarget_rung(row, "header")
    assert retarget_rung(once, "header") == once


def test_retarget_rung_to_a_longer_rung_stays_within_the_limit():
    worst = stats(omitted=(("definition", 1234567), ("import", 99999), ("heading", 88888)), note="x" * 100)
    header = build_header(WORST_PARENTS["failed read of a 2000-char path"], worst, REF)
    assert len(header) == 380
    out = retarget_rung(header + "\nbody", "superseded")
    assert len(out) <= 380
    assert parse_header(out) == {"rung": "superseded", "ref": REF, "kept": 40, "total": 220}
    assert_stub(out)


@pytest.mark.parametrize("content", [
    "plain", "", None, 5, "def a():\n    pass\n[…]\ndef b():\n    pass",
    f"first line\n{build_header(PARENT, stats(), REF)}",
])
def test_retarget_rung_refuses_what_it_cannot_parse(content):
    assert retarget_rung(content, "header") is None


def test_retarget_rung_refuses_unknown_rungs():
    row = build_header(PARENT, stats(), REF)
    assert retarget_rung(row, "bogus") is None
    assert retarget_rung(row, "") is None


def test_render_stats_is_frozen_and_note_is_optional():
    s = RenderStats(1, 2, 3, 4, (), "fold")
    assert s.note == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.rung = "extract"
