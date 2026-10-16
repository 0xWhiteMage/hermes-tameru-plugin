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
