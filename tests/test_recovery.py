"""``recovery``: the original store, ``tameru_expand`` and the session-archive lookup.

The archive lookup is tested against a faithful fake in both modes (its rows have the shape of
``SessionDB.get_messages``: the per-occurrence tool uid is the ``tool_call_uid`` *column*, where the
live message that was ref'd carried ``_tool_call_uid``), and against a real ``SessionDB`` on real Hermes.
"""
from __future__ import annotations

import copy
import json
import threading
import time

import pytest

from hermes_tameru_plugin.recovery import (
    DEFAULT_MAX_CHARS,
    EXPAND_SCHEMA,
    EXPAND_TOOL_NAME,
    MAX_GREP_CHARS,
    MAX_MAX_CHARS,
    MAX_SCAN_ROWS,
    OriginalStore,
    StoredOriginal,
    expand,
    session_lookup_factory,
)
from hermes_tameru_plugin.render import RenderStats, header_only, make_ref

REF = "ab12cd34"
SECRET = "gh" + "p_" + "A1b2C3d4E5" * 3          # shaped like a GitHub token, built at runtime
LINES = [f"line {i}" for i in range(1, 11)]
TEXT = "\n".join(LINES)                            # no trailing newline


def stocked(text: str = TEXT, ref: str = REF) -> OriginalStore:
    store = OriginalStore(10, 10**7)
    assert store.put(ref, text)
    return store


def call(store: OriginalStore, **args) -> dict:
    return json.loads(expand(store, args))


# ---- the store ---------------------------------------------------------------------------------
def test_put_and_get_round_trip_with_tool_and_meta():
    store = OriginalStore(4, 1000)
    assert store.put(REF, "hello", tool="terminal", meta={"exit_code": 1})
    assert store.get(REF) == "hello"
    assert store.entry(REF) == StoredOriginal("hello", "terminal", {"exit_code": 1})
    assert REF in store and "feedface" not in store and len(store) == 1 and store.total_chars == 5
    assert store.get("feedface") is None and store.entry("feedface") is None


def test_the_meta_dict_is_copied_in():
    meta = {"exit_code": 1}
    store = OriginalStore(4, 1000)
    store.put(REF, "x", meta=meta)
    meta["exit_code"] = 0
    assert store.entry(REF).meta == {"exit_code": 1}


def test_putting_a_ref_again_replaces_it_and_recounts_the_chars():
    store = OriginalStore(4, 1000)
    store.put("aaaaaaaa", "x" * 100)
    store.put("aaaaaaaa", "y" * 40)
    assert len(store) == 1 and store.total_chars == 40 and store.get("aaaaaaaa") == "y" * 40


def test_entry_cap_evicts_the_least_recently_used():
    store = OriginalStore(3, 10_000)
    for ref in ("aaaaaaaa", "bbbbbbbb", "cccccccc"):
        store.put(ref, ref)
    assert store.get("aaaaaaaa") == "aaaaaaaa"      # a read makes it the freshest
    store.put("dddddddd", "dddddddd")
    assert "bbbbbbbb" not in store, "the least recently used went"
    assert all(ref in store for ref in ("aaaaaaaa", "cccccccc", "dddddddd"))
    assert len(store) == 3 and store.total_chars == 24


def test_containment_and_length_do_not_refresh_recency():
    store = OriginalStore(2, 10_000)
    store.put("aaaaaaaa", "a")
    store.put("bbbbbbbb", "b")
    assert "aaaaaaaa" in store and len(store) == 2
    store.put("cccccccc", "c")
    assert "aaaaaaaa" not in store


def test_char_cap_evicts_until_the_new_entry_fits():
    store = OriginalStore(100, 100)
    for ref in ("aaaaaaaa", "bbbbbbbb", "cccccccc"):
        store.put(ref, "x" * 40)
    assert "aaaaaaaa" not in store and len(store) == 2 and store.total_chars == 80
    assert store.put("dddddddd", "y" * 100)
    assert len(store) == 1 and store.total_chars == 100


def test_an_entry_larger_than_the_cap_is_refused_and_evicts_nothing():
    store = OriginalStore(10, 100)
    store.put(REF, "x" * 60)
    assert store.put("bbbbbbbb", "y" * 101) is False
    assert store.get(REF) == "x" * 60 and len(store) == 1 and store.total_chars == 60


def test_probable_secrets_are_never_kept():
    store = OriginalStore(10, 10_000)
    assert store.put(REF, f"output\nTOKEN={SECRET}\nmore") is False
    assert store.put(REF, "-----BEGIN RSA PRIVATE KEY-----\nabc") is False
    assert REF not in store and len(store) == 0 and store.total_chars == 0
    store.put(REF, "clean")
    assert store.put(REF, f"now {SECRET}") is False
    assert store.get(REF) == "clean", "a refused put leaves what was there"


def test_non_text_and_zero_capacity_are_refused():
    assert OriginalStore(10, 100).put(REF, None) is False
    assert OriginalStore(10, 100).put(REF, b"bytes") is False
    assert OriginalStore(0, 100).put(REF, "x") is False
    assert OriginalStore(10, 0).put(REF, "x") is False
    assert OriginalStore(10, 0).put(REF, "") is True, "an empty text fits a zero-char cap"


def test_clear_empties_the_store():
    store = stocked()
    store.clear()
    assert len(store) == 0 and store.total_chars == 0 and store.get(REF) is None


def test_copies_are_fresh_empty_stores_with_the_same_caps():
    store = OriginalStore(7, 1234)
    store.put(REF, "x")
    for clone in (copy.copy(store), copy.deepcopy(store)):
        assert isinstance(clone, OriginalStore) and clone is not store
        assert (clone.max_entries, clone.max_chars) == (7, 1234)
        assert len(clone) == 0 and clone.total_chars == 0
        clone.put("bbbbbbbb", "y")
    assert len(store) == 1 and "bbbbbbbb" not in store, "clones do not share entries"


def test_deepcopy_of_an_object_that_holds_a_store_survives_the_lock():
    class Holder:
        def __init__(self):
            self.store = OriginalStore(5, 500)
            self.name = "agent"

    holder = Holder()
    holder.store.put(REF, "x")
    clone = copy.deepcopy(holder)
    assert clone.name == "agent" and len(clone.store) == 0 and clone.store is not holder.store


def test_the_store_stays_consistent_under_threads():
    store = OriginalStore(25, 400)
    refs = [f"{i:08x}" for i in range(60)]

    def work(offset: int) -> None:
        for round_ in range(200):
            ref = refs[(offset + round_) % len(refs)]
            store.put(ref, ref * (1 + round_ % 3))
            store.get(refs[(offset * 7 + round_) % len(refs)])
            assert len(store) <= 25 and store.total_chars <= 400

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    kept = [text for ref in refs if (text := store.get(ref)) is not None]
    assert len(kept) == len(store) <= 25
    assert sum(map(len, kept)) == store.total_chars <= 400


# ---- the tool schema ---------------------------------------------------------------------------
def test_the_schema_is_static_and_describes_the_ref():
    assert EXPAND_TOOL_NAME == EXPAND_SCHEMA["name"] == "tameru_expand"
    assert json.loads(json.dumps(EXPAND_SCHEMA)) == EXPAND_SCHEMA
    assert "ref=" in EXPAND_SCHEMA["description"] and "[tameru:" in EXPAND_SCHEMA["description"]
    params = EXPAND_SCHEMA["parameters"]
    assert params["type"] == "object" and params["required"] == ["ref"]
    assert set(params["properties"]) == {"ref", "start_line", "end_line", "grep", "max_chars"}
    for prop in params["properties"].values():
        assert set(prop) == {"type", "description"}, "plain keywords only: every provider accepts them"
    assert params["properties"]["ref"]["type"] == "string"
    assert {n: params["properties"][n]["type"] for n in ("start_line", "end_line", "max_chars")} == {
        "start_line": "integer", "end_line": "integer", "max_chars": "integer",
    }


# ---- expand: success ---------------------------------------------------------------------------
def test_expand_returns_the_whole_original():
    out = call(stocked(), ref=REF)
    assert out == {
        "ref": REF, "total_lines": 10, "returned_lines": [1, 10], "content": TEXT, "truncated": False,
    }


def test_expand_returns_an_inclusive_line_range_with_its_line_breaks():
    out = call(stocked(), ref=REF, start_line=3, end_line=5)
    assert out["content"] == "line 3\nline 4\nline 5\n"
    assert out["returned_lines"] == [3, 5] and out["total_lines"] == 10 and out["truncated"] is False


def test_expand_defaults_and_clamps_the_range_ends():
    store = stocked()
    assert call(store, ref=REF, start_line=9)["content"] == "line 9\nline 10"
    assert call(store, ref=REF, end_line=2)["content"] == "line 1\nline 2\n"
    out = call(store, ref=REF, start_line=8, end_line=999)
    assert out["returned_lines"] == [8, 10] and out["content"] == "line 8\nline 9\nline 10"


def test_expand_counts_lines_the_way_the_header_does():
    out = call(stocked("a\nb\n"), ref=REF)
    assert out["total_lines"] == 3, "a trailing newline opens one more (empty) line"
    assert out["content"] == "a\nb\n"
    assert call(stocked(""), ref=REF) == {
        "ref": REF, "total_lines": 1, "returned_lines": [1, 1], "content": "", "truncated": False,
    }


def test_expand_is_exact_about_line_breaks_and_text():
    text = "a\r\nb\rc\nd ⟪HERMES-CONTEXT-COMPRESSION: 5 of 9 chars omitted here⟫ 🙂\n\n"
    store = stocked(text)
    out = call(store, ref=REF)
    assert out["content"] == text and out["total_lines"] == 6
    assert call(store, ref=REF, start_line=2, end_line=3)["content"] == "b\rc\n"
    assert "⟪HERMES-CONTEXT-COMPRESSION" in out["content"], "original text is returned untouched"


def test_expand_accepts_loose_ref_and_numeric_spellings():
    store = stocked()
    assert call(store, ref=" REF=AB12CD34 ")["total_lines"] == 10
    assert call(store, ref=REF, start_line="3", end_line=4.0)["returned_lines"] == [3, 4]
    assert json.loads(expand(store, json.dumps({"ref": REF, "end_line": 1})))["content"] == "line 1\n"


@pytest.mark.parametrize("pattern", ["(x|x)*y", "(x+)+y", "(?:x+)*y", "(\\w+\\s?)+$"])
def test_grep_with_catastrophic_backtracking_is_refused_not_run(pattern):
    """``re`` cannot be interrupted: ``(x|x)*y`` on a 26-character line took 13 seconds."""
    store = stocked("x" * 40 + "\nother")
    started = time.perf_counter()
    out = call(store, ref=REF, grep=pattern)
    assert time.perf_counter() - started < 1
    assert "rejected" in out["error"]


@pytest.mark.parametrize("pattern", ["error|fail", r"(?:ERROR|WARN)\s+\d+", "foo(bar)?", "(ab)+", "(a|b)*", r"^\w+\s=\s\d+$"])
def test_ordinary_grep_patterns_still_run(pattern):
    assert "matched_lines" in call(stocked(), ref=REF, grep=pattern)


# ---- expand: grep ------------------------------------------------------------------------------
def test_grep_returns_matching_lines_and_their_numbers():
    out = call(stocked(), ref=REF, grep="line [37]")
    assert out["content"] == "line 3\nline 7\n"
    assert out["matched_lines"] == [3, 7] and out["returned_lines"] == [3, 7]
    assert out["total_lines"] == 10 and out["truncated"] is False


def test_grep_is_case_insensitive_and_honours_the_range():
    store = stocked()
    assert call(store, ref=REF, grep="LINE 3")["matched_lines"] == [3]
    out = call(store, ref=REF, grep="line", start_line=4, end_line=6)
    assert out["matched_lines"] == [4, 5, 6] and out["content"] == "line 4\nline 5\nline 6\n"


def test_grep_without_a_match_returns_nothing_and_an_empty_grep_filters_nothing():
    store = stocked()
    out = call(store, ref=REF, grep="absent")
    assert out["content"] == "" and out["matched_lines"] == [] and out["returned_lines"] == [0, 0]
    assert out["truncated"] is False
    out = call(store, ref=REF, grep="")
    assert out["content"] == TEXT and "matched_lines" not in out


def test_grep_matches_the_text_of_a_line_not_its_break():
    store = stocked("alpha\r\nbeta\r\n")
    out = call(store, ref=REF, grep="^beta$")
    assert out["matched_lines"] == [2] and out["content"] == "beta\r\n"


# ---- expand: max_chars -------------------------------------------------------------------------
def test_max_chars_cuts_at_a_line_boundary_and_says_so():
    text = "\n".join(f"row {i:04d}" for i in range(1000))     # 8 chars + a break per line
    out = call(stocked(text), ref=REF, max_chars=100)
    assert out["truncated"] is True
    assert out["content"] == text[: len(out["content"])] and len(out["content"]) == 99
    assert out["returned_lines"] == [1, 11], "eleven whole lines: a twelfth would pass 100 chars"
    nxt = call(stocked(text), ref=REF, start_line=out["returned_lines"][1] + 1, max_chars=100)
    assert nxt["content"].startswith("row 0011")


def test_one_oversized_line_is_cut_so_the_call_still_answers():
    out = call(stocked("x" * 500 + "\nnext"), ref=REF, max_chars=100)
    assert out["content"] == "x" * 100 and out["returned_lines"] == [1, 1] and out["truncated"] is True


def test_max_chars_defaults_and_is_capped():
    assert (DEFAULT_MAX_CHARS, MAX_MAX_CHARS) == (12_000, 50_000)
    text = "\n".join("z" * 9_999 for _ in range(8))            # 10,000 chars per line with its break
    store = stocked(text)
    assert call(store, ref=REF)["returned_lines"] == [1, 1], "12,000 fits one line"
    assert call(store, ref=REF, max_chars=10**9)["returned_lines"] == [1, 5], "capped at 50,000"
    assert call(store, ref=REF, max_chars=25_000)["returned_lines"] == [1, 2]
    assert call(stocked(TEXT), ref=REF, max_chars=10**9)["truncated"] is False


# ---- expand: misses and the archive ------------------------------------------------------------
def test_a_miss_points_at_session_search():
    out = json.loads(expand(OriginalStore(5, 500), {"ref": REF}, session_id="sess-9"))
    assert set(out) == {"error", "hint"}
    assert REF in out["error"] and "not found" in out["error"]
    assert out["hint"] == "session_search(query=..., role_filter='tool', session_id='sess-9')"
    anonymous = json.loads(expand(OriginalStore(5, 500), {"ref": REF}))
    assert anonymous["hint"] == "session_search(query=..., role_filter='tool', session_id='<id>')"


def test_a_store_miss_falls_back_to_the_session_lookup_and_caches_the_hit():
    store = OriginalStore(5, 10_000)
    seen: list[str] = []

    def lookup(ref: str):
        seen.append(ref)
        return TEXT if ref == REF else None

    first = json.loads(expand(store, {"ref": REF, "end_line": 2}, session_lookup=lookup))
    second = json.loads(expand(store, {"ref": REF, "start_line": 9}, session_lookup=lookup))
    assert first["content"] == "line 1\nline 2\n" and second["content"] == "line 9\nline 10"
    assert seen == [REF], "the second expand was served from the store"
    assert store.get(REF) == TEXT
    missing = json.loads(expand(store, {"ref": "00000000"}, session_lookup=lookup))
    assert "error" in missing and "hint" in missing


def test_the_lookup_is_not_consulted_when_the_store_has_the_ref():
    def lookup(ref: str):
        raise AssertionError("must not be called")

    assert json.loads(expand(stocked(), {"ref": REF}, session_lookup=lookup))["total_lines"] == 10


def test_a_failing_lookup_is_a_miss():
    def lookup(ref: str):
        raise RuntimeError("db closed")

    out = json.loads(expand(OriginalStore(5, 500), {"ref": REF}, session_lookup=lookup))
    assert "not found" in out["error"]


def test_an_archived_original_with_a_secret_is_returned_but_not_cached():
    store = OriginalStore(5, 10_000)
    text = f"token {SECRET}\nline 2"
    out = json.loads(expand(store, {"ref": REF}, session_lookup=lambda ref: text))
    assert out["content"] == text
    assert REF not in store, "the store never keeps a probable secret"


# ---- expand: bad arguments ---------------------------------------------------------------------
@pytest.mark.parametrize("args", [
    None, 5, [], "not json", '["ab12cd34"]', "{}", {}, {"ref": ""}, {"ref": None}, {"ref": 12345678},
    {"ref": "xyz"}, {"ref": "ab12cd3"}, {"ref": "ab12cd345"}, {"ref": "ab12cd3g"}, {"ref": [REF]},
])
def test_bad_refs_and_shapes_give_an_error(args):
    out = json.loads(expand(stocked(), args))
    assert list(out) == ["error"] and out["error"]


@pytest.mark.parametrize("key, value", [
    ("start_line", 0), ("start_line", -3), ("start_line", "x"), ("start_line", 1.5), ("start_line", True),
    ("start_line", []), ("end_line", 0), ("end_line", "-1"), ("end_line", {}), ("max_chars", 0),
    ("max_chars", "lots"), ("max_chars", False), ("max_chars", object()),
])
def test_bad_numbers_give_an_error_naming_the_argument(key, value):
    out = json.loads(expand(stocked(), {"ref": REF, key: value}))
    assert key in out["error"]


def test_inconsistent_ranges_give_an_error():
    store = stocked()
    backwards = call(store, ref=REF, start_line=5, end_line=2)
    assert "end_line" in backwards["error"] and backwards["total_lines"] == 10
    past = call(store, ref=REF, start_line=11)
    assert "start_line" in past["error"] and past["total_lines"] == 10


def test_bad_regexes_give_an_error():
    store = stocked()
    assert "valid regex" in call(store, ref=REF, grep="(unclosed")["error"]
    assert "valid regex" in call(store, ref=REF, grep="[z-a]")["error"]
    assert str(MAX_GREP_CHARS) in call(store, ref=REF, grep="a" * (MAX_GREP_CHARS + 1))["error"]
    assert call(store, ref=REF, grep="a" * MAX_GREP_CHARS)["matched_lines"] == []
    assert "grep" in call(store, ref=REF, grep=5)["error"]
    assert "grep" in call(store, ref=REF, grep=["x"])["error"]


def test_expand_never_raises():
    class Broken:
        def get(self, ref):
            raise RuntimeError("boom")

    out = json.loads(expand(Broken(), {"ref": REF}))
    assert "RuntimeError" in out["error"]
    assert "error" in json.loads(expand(object(), {"ref": REF}))


# ---- the session-archive lookup (fake db, both modes) --------------------------------------------
class FakeDB:
    """``get_messages`` as SessionDB answers it: dict rows, content decoded, uid *columns*."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple] = []

    def get_messages(self, session_id, include_compacted=False, **kwargs):
        self.calls.append((session_id, include_compacted, kwargs))
        return list(self.rows)


def live(content: str, *, call_uid="t-1", msg_uid="m-1", call_id="call_1") -> dict:
    """The live message a ref is minted from (Hermes names the tool uid ``_tool_call_uid``)."""
    msg = {"role": "tool", "content": content, "tool_call_id": call_id, "message_uid": msg_uid}
    if call_uid:
        msg["_tool_call_uid"] = call_uid
    return msg


def db_row(content: str, *, call_uid="t-1", msg_uid="m-1", call_id="call_1", role="tool") -> dict:
    """The same message as ``SessionDB.get_messages`` returns it: the uid is the column name."""
    row = {"role": role, "content": content, "tool_call_id": call_id, "message_uid": msg_uid,
           "tool_call_uid": call_uid, "active": 0, "compacted": 1}
    return row


def stats() -> RenderStats:
    return RenderStats(2, 40, 120, 4000, (("log", 38),), "extract")


def test_the_lookup_finds_an_archived_original_by_its_ref():
    original = "line a\nline b\nline c"
    ref = make_ref(live(original), original)
    db = FakeDB([db_row("other output", call_uid="t-0", msg_uid="m-0"), db_row(original)])
    lookup = session_lookup_factory(db, "sess", make_ref)
    assert lookup(ref) == original
    assert lookup("00000000") is None


def test_the_tool_uid_column_is_read_as_the_live_key():
    original = "payload " * 40
    ref = make_ref(live(original, call_uid="t-77", msg_uid="m-5"), original)
    row = db_row(original, call_uid="t-77", msg_uid="m-5")
    assert "_tool_call_uid" not in row
    assert make_ref(row, original) != ref, "make_ref on the raw row would miss: it reads the live key"
    assert session_lookup_factory(FakeDB([row]), "sess", make_ref)(ref) == original


def test_a_ref_minted_before_the_first_flush_still_matches():
    """The live message had no ``_tool_call_uid`` yet (flush stamps it later); the row has one now."""
    original = "early output " * 30
    for minted_from in (live(original, call_uid=None, msg_uid="m-9"),                    # message_uid
                        {"role": "tool", "content": original, "tool_call_id": "call_1"}):   # tool_call_id only
        ref = make_ref(minted_from, original)
        row = db_row(original, call_uid="t-9", msg_uid="m-9")
        assert session_lookup_factory(FakeDB([row]), "sess", make_ref)(ref) == original


def test_a_row_without_any_uid_matches_a_ref_minted_the_same_way():
    original = "no ids"
    row = {"role": "tool", "content": original}
    assert session_lookup_factory(FakeDB([row]), "s", make_ref)(make_ref({}, original)) == original


def test_tameru_rendered_rows_are_not_originals():
    original = "the real output " * 20
    ref = make_ref(live(original), original)
    rendered = header_only("[terminal] ran ls -> exit 0", stats(), ref)
    legacy = "alpha\n[…]\nomega"
    rows = [db_row(rendered), db_row(legacy, msg_uid="m-2", call_uid="t-2")]
    lookup = session_lookup_factory(FakeDB(rows), "s", make_ref)
    assert lookup(ref) is None, "a render is not the text its ref names"
    assert lookup(make_ref(live(legacy, call_uid="t-2", msg_uid="m-2"), legacy)) is None
    rows.append(db_row(original))
    assert session_lookup_factory(FakeDB(rows), "s", make_ref)(ref) == original


def test_only_text_tool_rows_are_considered():
    text = "wanted " * 20
    ref = make_ref(live(text), text)
    rows = [
        db_row(text, role="assistant"),
        db_row(text, role="user"),
        {**db_row(text), "content": [{"type": "text", "text": text}]},
        {**db_row(text), "content": None},
        "not a dict",
        None,
    ]
    assert session_lookup_factory(FakeDB(rows), "s", make_ref)(ref) is None
    assert session_lookup_factory(FakeDB([*rows, db_row(text)]), "s", make_ref)(ref) == text


def test_the_scan_is_read_once_per_lookup_callable():
    text = "cached " * 20
    ref = make_ref(live(text), text)
    db = FakeDB([db_row(text)])
    lookup = session_lookup_factory(db, "sess-1", make_ref)
    assert db.calls == [], "nothing is read before the first lookup"
    assert lookup(ref) == text and lookup(ref) == text and lookup("00000000") is None
    assert lookup("00000000") is None
    assert db.calls == [("sess-1", True, {})], "one read, with the compaction-archived rows included"


def test_the_scan_is_bounded_to_the_newest_rows():
    texts = [f"out {i}" for i in range(MAX_SCAN_ROWS + 5)]
    rows = [db_row(t, call_uid=f"t-{i}", msg_uid=f"m-{i}") for i, t in enumerate(texts)]
    lookup = session_lookup_factory(FakeDB(rows), "s", make_ref)
    newest = len(texts) - 1
    assert lookup(make_ref(live(texts[newest], call_uid=f"t-{newest}", msg_uid=f"m-{newest}"), texts[newest])) == texts[newest]
    oldest = make_ref(live(texts[0], call_uid="t-0", msg_uid="m-0"), texts[0])
    assert lookup(oldest) is None, "the 5 oldest rows are beyond the scan bound"
    edge = 5
    assert lookup(make_ref(live(texts[edge], call_uid=f"t-{edge}", msg_uid=f"m-{edge}"), texts[edge])) == texts[edge]


def test_the_lookup_never_raises():
    class Failing:
        def get_messages(self, *args, **kwargs):
            raise RuntimeError("database is locked")

    assert session_lookup_factory(Failing(), "s", make_ref)(REF) is None
    assert session_lookup_factory(FakeDB(None), "s", make_ref)(REF) is None
    assert session_lookup_factory(None, "s", make_ref)(REF) is None
    assert session_lookup_factory(FakeDB([db_row("x")]), "s", lambda msg, text: 1 / 0)(REF) is None
    assert session_lookup_factory(FakeDB([db_row("x")]), "s", make_ref)(None) is None


def test_a_failed_read_is_retried_by_the_next_lookup():
    text = "second try " * 10
    ref = make_ref(live(text), text)

    class Flaky(FakeDB):
        def get_messages(self, *args, **kwargs):
            if not self.calls:
                self.calls.append(args)
                raise RuntimeError("locked")
            return super().get_messages(*args, **kwargs)

    lookup = session_lookup_factory(Flaky([db_row(text)]), "s", make_ref)
    assert lookup(ref) is None
    assert lookup(ref) == text


def test_expand_recovers_a_pruned_original_through_the_lookup():
    original = "\n".join(f"event {i}: ok" for i in range(1, 51)) + "\nevent 51: ERROR boom"
    ref = make_ref(live(original), original)
    pruned = header_only("[terminal] ran job -> exit 1", stats(), ref)
    db = FakeDB([db_row(original), {**db_row(pruned, msg_uid="m-new", call_uid="t-1"), "active": 1, "compacted": 0}])
    empty_store = OriginalStore(10, 10**6)           # after a restart, or a gateway eviction
    out = json.loads(expand(
        empty_store, {"ref": ref, "grep": "error"},
        session_lookup=session_lookup_factory(db, "sess", make_ref), session_id="sess",
    ))
    assert out["content"] == "event 51: ERROR boom" and out["matched_lines"] == [51]
    assert out["total_lines"] == 51


# ---- the session-archive lookup (real SessionDB) -----------------------------------------------
@pytest.fixture
def session_db(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    db.create_session("sess-1", "cli")
    yield db
    db.close()


def _flush_a_tool_turn(db, original: str) -> dict:
    """Persist ``user, assistant(tool call), tool`` and return the live tool message."""
    db.append_message("sess-1", "user", "run the job")
    db.append_messages_batch("sess-1", [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "terminal", "arguments": "{}"}},
        ]},
        {"role": "tool", "content": original, "tool_name": "terminal", "tool_call_id": "call_1"},
    ])
    return db.get_messages_as_conversation("sess-1")[-1]


@pytest.mark.real_hermes
def test_a_real_session_db_hands_back_the_original_after_compaction(session_db):
    original = "\n".join(f"row {i}: fine" for i in range(1, 200)) + "\nrow 200: FAILED hard"
    tool = _flush_a_tool_turn(session_db, original)
    assert tool.get("_tool_call_uid") and tool.get("message_uid"), "Hermes stamps both identities"
    ref = make_ref(tool, original)

    rows = session_db.get_messages("sess-1", include_compacted=True)
    assert "tool_call_uid" in rows[-1] and "_tool_call_uid" not in rows[-1], "the column has no underscore"

    # Hermes commits the prune: the original row is archived, the rendered row becomes the live one.
    conversation = session_db.get_messages_as_conversation("sess-1")
    pruned = header_only("[terminal] ran job -> exit 1", stats(), ref)
    conversation[-1] = {**conversation[-1], "content": pruned}
    session_db.archive_and_compact(
        "sess-1", conversation, watermark=session_db.get_active_message_watermark("sess-1"),
    )
    active = session_db.get_messages("sess-1")
    assert active[-1]["content"] == pruned, "the live history now holds the render"

    lookup = session_lookup_factory(session_db, "sess-1", make_ref)
    assert lookup(ref) == original
    out = json.loads(expand(
        OriginalStore(10, 10**6), {"ref": ref, "start_line": 200}, session_lookup=lookup, session_id="sess-1",
    ))
    assert out["content"] == "row 200: FAILED hard" and out["total_lines"] == 200
    assert session_lookup_factory(session_db, "sess-1", make_ref)("00000000") is None
    assert session_lookup_factory(session_db, "another-session", make_ref)(ref) is None


@pytest.mark.real_hermes
def test_a_reopened_session_db_still_resolves_the_ref(tmp_path):
    from hermes_state import SessionDB

    path = tmp_path / "resume.db"
    db = SessionDB(path)
    db.create_session("sess-1", "cli")
    original = "resume me " * 50
    tool = _flush_a_tool_turn(db, original)
    ref = make_ref(tool, original)
    db.close()

    reopened = SessionDB(path)               # a new process: no store, same archive
    try:
        assert session_lookup_factory(reopened, "sess-1", make_ref)(ref) == original
    finally:
        reopened.close()
