"""Regression tests for the 1.4.1 fix-all round (each fails on d8fd2d6, the red-team head, before its fix)."""
from __future__ import annotations

import json
import time

import pytest

from hermes_tameru_plugin.recovery import OriginalStore, expand

REF = "ab12cd34"


def _store(text: str) -> OriginalStore:
    store = OriginalStore(10, 10**7)
    assert store.put(REF, text)
    return store


# ---- R4: tameru_expand grep runs under a hard timeout ------------------------------------------
@pytest.mark.parametrize("pattern", [".*.*.*x", "a*a*a*a*x", r"\w*\w*\w*!"])
def test_a_polynomial_grep_times_out_instead_of_hanging(pattern):
    """Red-team R4: ``.*.*.*x`` passes the nested-repeat pre-check and ran >275 s on a 3,000-char line."""
    store = _store("a" * 3000 + "\n" + "b" * 10)
    started = time.perf_counter()
    out = json.loads(expand(store, {"ref": REF, "grep": pattern}, grep_timeout=1.0))
    elapsed = time.perf_counter() - started
    assert elapsed < 4.0, elapsed
    assert "timed out" in out["error"] and "simplify" in out["error"]


def test_greps_still_match_after_a_timeout_and_through_the_child():
    store = _store("\n".join(f"line {i} " + ("error" if i % 7 == 0 else "ok") for i in range(1, 200)))
    assert json.loads(expand(store, {"ref": REF, "grep": "err.*r"}))["matched_lines"] == list(range(7, 200, 7))
    assert json.loads(expand(store, {"ref": REF, "grep": "ERROR"}))["matched_lines"] == list(range(7, 200, 7))
    assert json.loads(expand(store, {"ref": REF, "grep": "(?:ok|error)$", "end_line": 3}))["matched_lines"] == [1, 2, 3]


def test_a_long_line_is_scanned_up_to_the_cap_and_says_so():
    store = _store("x" * 50 + "needle" + "y" * 100 + "\n" + "z" * 200 + "needle")
    out = json.loads(expand(store, {"ref": REF, "grep": "needle"}, grep_line_chars=100))
    assert out["matched_lines"] == [1] and out["lines_capped"] == 2


def test_the_engine_passes_its_grep_settings_to_expand():
    from engine_support import make_engine

    from hermes_tameru_plugin.config import TameruSettings

    engine = make_engine(TameruSettings(expand_grep_timeout=0.5))
    engine._store.put(REF, "a" * 3000 + "\n" + "b" * 30)
    out = json.loads(engine.handle_tool_call("tameru_expand", {"ref": REF, "grep": ".*.*.*x"}))
    assert "after 0.5 s" in out["error"]
    engine = make_engine(TameruSettings(expand_grep_line_chars=10))
    engine._store.put(REF, "a" * 3000 + "\n" + "b" * 30)
    out = json.loads(engine.handle_tool_call("tameru_expand", {"ref": REF, "grep": "b"}))
    assert out["matched_lines"] == [2] and out["lines_capped"] == 2
