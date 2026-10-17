import json

from hermes_tameru_plugin.recovery import OriginalStore, expand


def test_grep_matched_lines_counted_against_max_chars():
    store = OriginalStore(16, 1_000_000)
    ref = "0123456789abcdef"
    store.put(ref, "\n" * 200000 + "end")

    answer = expand(
        store,
        {"ref": ref, "grep": "^$", "max_chars": 2000},
    )
    parsed = json.loads(answer)
    content = parsed["content"]
    matched_lines = parsed["matched_lines"]

    assert len(json.dumps(matched_lines)) + len(content) <= 2010


def test_no_grep_output_unchanged():
    store = OriginalStore(16, 1_000_000)
    ref = "fedcba9876543210"
    store.put(ref, "a\nb")

    answer = expand(store, {"ref": ref})

    assert answer == (
        '{"ref": "fedcba9876543210", "total_lines": 2, '
        '"returned_lines": [1, 2], "content": "a\\nb", "truncated": false}'
    )

    capped = expand(store, {"ref": ref, "max_chars": 2})
    assert capped == (
        '{"ref": "fedcba9876543210", "total_lines": 2, '
        '"returned_lines": [1, 1], "content": "a\\n", "truncated": true}'
    )


def test_grep_few_matches_returns_same_lines():
    store = OriginalStore(16, 1_000_000)
    ref = "abcdef0123456789"
    store.put(ref, "x\ny\nx\nz")

    answer = expand(store, {"ref": ref, "grep": "x", "max_chars": 1000})
    parsed = json.loads(answer)

    assert parsed["matched_lines"] == [1, 3]
    assert parsed["content"] == "x\nx\n"


def test_grep_tiny_cap_still_returns_text():
    store = OriginalStore(16, 10_000_000)
    ref = "aaaaaaaaaaaaaaaa"
    store.put(ref, "\n" * 99999 + "needle-long-line\n")

    answer = expand(
        store,
        {"ref": ref, "grep": "needle", "max_chars": 3},
    )
    parsed = json.loads(answer)

    assert parsed["matched_lines"] == [100000]
    assert parsed["truncated"] is True
    assert parsed["content"] == "n"


def test_grep_many_hits_bounded_default_cap():
    store = OriginalStore(16, 10_000_000)
    ref = "0123456789abcdef"
    store.put(ref, "\n" * 200000 + "end")

    answer = expand(
        store,
        {"ref": ref, "grep": "^$"},
    )
    parsed = json.loads(answer)

    assert len(answer) < 16000
    assert parsed["truncated"] is True


def test_grep_unicode_lines():
    store = OriginalStore(16, 10_000_000)
    ref = "bbbbbbbbbbbbbbbb"
    store.put(ref, "é漢\n" * 50000)

    answer = expand(
        store,
        {"ref": ref, "grep": "漢", "max_chars": 2000},
    )
    parsed = json.loads(answer)

    assert len(parsed["content"]) + len(json.dumps(parsed["matched_lines"])) <= 2010
    assert "漢" in parsed["content"]


def test_no_grep_cap_length_unchanged():
    store = OriginalStore(16, 10_000_000)
    ref = "0123456789abcdef"
    store.put(ref, "\n" * 200000 + "end")

    answer = expand(
        store,
        {"ref": ref, "max_chars": 2000},
    )
    parsed = json.loads(answer)

    assert len(parsed["content"]) <= 2000
    assert "matched_lines" not in parsed
