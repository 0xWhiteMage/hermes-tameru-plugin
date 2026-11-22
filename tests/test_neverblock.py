"""S7 never-block: an engine call is bounded by the pass wall budget, and >8M inners never reach the engine."""
import threading
import time
from types import SimpleNamespace

from conftest import terminal_payload
from engine_support import Chat, make_engine, prune_pass

import hermes_tameru_plugin.engine as eng
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.render import parse_header


def _engine():
    from hermes_tameru_plugin.activity import ActivityState
    e = eng.TameruContextEngine.__new__(eng.TameruContextEngine)
    e._memo = eng._Memo()
    e._pool = eng._EnginePool()
    e._warm = []
    e._wall_fired = 0
    e._settings = eng.TameruSettings()
    e._settings_sha = "t"
    e._transactions = eng._Transactions()
    e._activity = ActivityState()
    e._store = None  # _compress never touches it
    return e


def test_too_large_inner_never_touches_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(eng, "compress_context", lambda *a, **k: calls.append(1))
    e = _engine()
    t0 = time.perf_counter()
    assert e._compress("x" * (eng._TOO_LARGE_CHARS + 1), ["q"], None, None) is None
    assert time.perf_counter() - t0 < 6.0 and not calls


def test_slow_engine_call_bounded_by_wall(monkeypatch):
    release = threading.Event()

    def slow(*a, **k):
        release.wait(30)
        raise AssertionError("must be timed out")

    monkeypatch.setattr(eng, "compress_context", slow)
    e = _engine()
    scope = e._new_scope(None)            # a real pass scope, the way the host builds one
    scope.wall_ms = 1500
    scope.started = time.perf_counter()
    tok = eng._SCOPE.set(scope)
    try:
        t0 = time.perf_counter()
        assert e._compress("y" * 20_000, ["q"], None, None) is None
        elapsed = time.perf_counter() - t0
    finally:
        eng._SCOPE.reset(tok)
        release.set()                     # let the pool thread finish so pytest can exit
    assert elapsed <= 1.5 + 1.0, f"the wall budget bounds the wait: {elapsed:.2f}s"
    assert elapsed < 6.0


def test_pass_returns_within_wall_when_engine_blocks(monkeypatch):
    """A pass over many rows: the wall cuts the pass short once, later rows still get their recovery ref."""
    release = threading.Event()

    def blocking(*a, **k):
        release.wait(30)
        raise AssertionError("must be timed out")

    monkeypatch.setattr(eng, "compress_context", blocking)
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, pass_wall_ms=1500))
    chat = Chat()
    for n in range(30):
        lines = "\n".join(f"row {i} of output {n}" for i in range(1200))
        chat.tool("terminal", {"command": f"cat app{n}.log"}, terminal_payload(lines))
    messages = chat.finish()
    t0 = time.perf_counter()
    try:
        out, _count = prune_pass(engine, messages)
        elapsed = time.perf_counter() - t0
    finally:
        release.set()                     # unblock the pool thread so pytest can exit
    assert elapsed <= 1.5 + 1.0, f"the pass came back inside the bound: {elapsed:.2f}s"
    rungs = {}
    for i, msg in enumerate(out):
        if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
            header = parse_header(msg["content"])
            if header is not None:
                rungs[i] = header["rung"]
                assert "tameru:" in msg["content"], f"row {i} lost its recovery ref"
    assert rungs, "the pass rewrote rows"
    # at most the one row in flight when the wall fired may keep a richer rung; every row after it is a header
    non_header = [rung for rung in rungs.values() if rung != "header"]
    assert len(non_header) <= 1 and set(non_header) <= {"brief"}, f"post-wall rows render header rungs: {sorted(set(rungs.values()))}"
    assert engine._wall_fired == 1, f"one pass was cut short: {engine._wall_fired}"


def test_warm_then_inline_call_hits_content_cache(monkeypatch):
    """Warming a big result at turn end, then the pass's inline ``_compress``, reads the engine's content cache."""
    from hermes_tameru_plugin.tameru.compress_context import cache_stats

    real = eng.compress_context
    calls = []
    monkeypatch.setattr(eng, "compress_context",
                        lambda *a, **k: calls.append((a, k)) or real(*a, **k))
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0))
    chat = Chat()
    inner = "\n".join(f"data line {i} of the metrics report value {i * 7}" for i in range(800))
    chat.tool("terminal", {"command": "cat metrics.txt"}, terminal_payload(inner))
    engine._warm_recent_results(chat.finish())
    assert engine._warm and calls, "the recent big tool result was submitted for warming"
    for fut in engine._warm:
        fut.result(timeout=10)
    warm_inner, warm_kwargs = calls[0][0][0], calls[0][1]
    for key in ("content_hint", "pin_patterns", "gap_marker", "prose_brief", "recursion_markers", "min_savings_ratio"):
        assert key in warm_kwargs, f"the warm call carries the inline option set ({key})"
    hits_before = cache_stats()["content"]["hits"]
    engine._compress(warm_inner, ["q"], warm_kwargs["content_hint"], None)
    assert cache_stats()["content"]["hits"] > hits_before, "the inline call's analyses came from the content cache"


def test_wall_left_ms():
    scope = eng._Pass(budget=1, messages=None, wall_ms=1500, started=time.perf_counter())
    assert 0 < scope.wall_left_ms() <= 1500
    assert eng._Pass(budget=1, messages=None).wall_left_ms() == float("inf")
