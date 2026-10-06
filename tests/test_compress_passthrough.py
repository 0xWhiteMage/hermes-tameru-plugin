"""``compress`` and ``prune_tool_results_only`` hand Hermes' own result back, whatever the plugin did."""
from __future__ import annotations

import pytest
from engine_support import Chat, fake_summary_llm, make_engine, server_log
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin import engine as engine_module
from hermes_tameru_plugin.config import TameruSettings


def _base():
    return engine_module._Base


class _Recorder:
    """Stands in for ``ContextCompressor.compress``: records the call and what scope was active."""

    def __init__(self, returns):
        self.returns, self.calls, self.scopes = returns, [], []

    def __call__(self, engine, messages, *args, **kwargs):
        self.calls.append((messages, args, kwargs))
        self.scopes.append(engine_module._SCOPE.get())
        return self.returns


def test_bypass_cooldown_and_unknown_kwargs_reach_super(monkeypatch):
    sentinel = object()
    recorder = _Recorder(sentinel)
    monkeypatch.setattr(_base(), "compress", lambda self, *a, **kw: recorder(self, *a, **kw))
    engine = make_engine()
    messages = [{"role": "user", "content": "hi"}]
    out = engine.compress(messages, 123, "topic", True, "mem", bypass_cooldown=True, future_flag=7)
    assert out is sentinel
    (got, args, kwargs), = recorder.calls
    assert got is messages and args == ()
    assert kwargs == {
        "current_tokens": 123, "focus_topic": "topic", "force": True, "memory_context": "mem",
        "bypass_cooldown": True, "future_flag": 7,
    }


def test_return_object_is_supers_even_when_it_is_the_input(monkeypatch):
    messages = [{"role": "user", "content": "hi"}]
    monkeypatch.setattr(_base(), "compress", lambda self, msgs, *a, **kw: msgs)
    assert make_engine().compress(messages) is messages


def test_the_pass_scope_is_set_only_during_the_call(monkeypatch):
    recorder = _Recorder([])
    monkeypatch.setattr(_base(), "compress", lambda self, *a, **kw: recorder(self, *a, **kw))
    engine = make_engine()
    messages = [{"role": "user", "content": "hi"}]
    assert engine_module._SCOPE.get() is None
    engine.compress(messages, focus_topic="the payment gateway")
    scope, = recorder.scopes
    assert scope.focus == "the payment gateway" and scope.messages is messages
    assert scope.budget == engine._settings.pass_char_budget
    assert engine_module._SCOPE.get() is None


def test_the_scope_is_reset_when_super_raises(monkeypatch):
    def boom(self, *a, **kw):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(_base(), "compress", boom)
    with pytest.raises(RuntimeError):
        make_engine().compress([{"role": "user", "content": "hi"}])
    assert engine_module._SCOPE.get() is None


def test_disabled_engine_calls_super_without_a_scope(monkeypatch):
    recorder = _Recorder("stock")
    monkeypatch.setattr(_base(), "compress", lambda self, *a, **kw: recorder(self, *a, **kw))
    assert make_engine(TameruSettings(enabled=False)).compress([], bypass_cooldown=True) == "stock"
    assert recorder.scopes == [None] and recorder.calls[0][2]["bypass_cooldown"] is True


def test_there_is_no_post_check_the_compressed_list_is_what_hermes_returned(monkeypatch):
    """End to end on the real or stub compressor: the engine adds nothing after ``super().compress``."""
    chat = Chat("why did the payment gateway time out?")
    for i in range(4):
        chat.tool("terminal", {"command": f"tail -n {100 + i} app.log"}, server_log(i))
    messages = chat.finish("what failed?")
    fake_summary_llm(monkeypatch)
    engine = make_engine()
    seen = []
    parent = _base().compress

    def spy(self, *a, **kw):
        out = parent(self, *a, **kw)
        seen.append(out)
        return out

    monkeypatch.setattr(_base(), "compress", spy)
    out = engine.compress(messages, 200_000, bypass_cooldown=True)
    assert out is seen[0]


def test_compress_runs_the_ladder_on_the_rows_it_prunes(monkeypatch):
    chat = Chat("why did the payment gateway time out?")
    for i in range(4):
        chat.tool("terminal", {"command": f"tail -n {100 + i} app.log"}, server_log(i))
    chat.tool("terminal", {"command": "pytest"}, hp.terminal_result("x" * 100))
    messages = chat.finish("what failed?")
    fake_summary_llm(monkeypatch)
    engine = make_engine()
    engine.compress(messages, 200_000, force=True)
    events = engine._telemetry.snapshot()["events"]
    assert "render" in events or "parent_line" in events
