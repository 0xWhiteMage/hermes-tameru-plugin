"""Regression tests for the 1.4.1 fix-all round (each fails on d8fd2d6, the red-team head, before its fix)."""
from __future__ import annotations

import json
import time

import pytest

from engine_support import make_engine, prune_pass

from hermes_tameru_plugin.config import TameruSettings
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


# ---- R5: the pass-2 reservation covers briefs too --------------------------------------------------------------
def test_r5_an_old_brief_does_not_starve_the_newest_rows():
    """Red-team R5: 1.4.1 reserved room for later rows against an old row's *extract* only; its brief still
    took the room, so with a tight budget the oldest rows kept briefs and the newest fell to headers."""
    from test_retained_budget import ROWS, _bodies, _rungs, _session

    messages, idxs = _session()
    budget = 3000
    out, _ = prune_pass(make_engine(TameruSettings(retained_extract_budget_chars=budget)), messages)
    rungs = _rungs(out, idxs)
    assert rungs[-1] != "header" and rungs[0] == "header", rungs
    assert _bodies(out, idxs) <= budget
    assert len(rungs) == ROWS


def test_r5_brief_estimate_is_deterministic_and_bounded():
    engine = make_engine(TameruSettings())
    plain = "x" * 20_000
    failing = "Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: bad\n" + "y" * 20_000
    a, b = engine._brief_estimate(plain), engine._brief_estimate(failing)
    assert a == engine._brief_estimate(plain)
    assert a >= engine._settings.brief_chars and b > a
    assert engine._brief_estimate("z" * 900) <= int((1 - engine._settings.min_savings) * 900)


# ---- R3: env / YAML lines with identifier-like credentials are not kept --------------------------------------------
def test_r3_env_file_passphrases_are_not_kept_for_expand():
    """Red-team R3: the code exclusions let ``DB_PASSWORD=correct_horse_battery_staple`` (an unquoted snake_case
    passphrase) through, so the recovery store kept a ``cat .env`` original holding it. Engine 91f00e0 flags
    whole-line env / YAML assignments again; code that reads a password is still kept."""
    store = OriginalStore(16, 1_000_000)
    filler = "\n".join(f"LOG_LEVEL_{i}=info" for i in range(20))
    assert not store.put("0123abcd", filler + "\nDB_PASSWORD=correct_horse_battery_staple\n")
    assert not store.put("0123abce", "db:\n  host: db\n  secret: correct_horse_battery_staple\n")
    assert not store.put("0123abcf", filler + "\nAUTH_TOKEN=team.alpha.deploy.token.x\n")
    assert store.put("0123abd0", "def f(args):\n    run(\n        user_password=args.user_password,\n    )\n" + filler)
