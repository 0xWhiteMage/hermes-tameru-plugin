"""``compress`` and ``prune_tool_results_only`` hand Hermes' own result back, whatever the plugin did."""
from __future__ import annotations

import copy
import json

import pytest
from engine_support import Chat, fake_summary_llm, make_engine, pytest_failure_with_passes, server_log
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin import engine as engine_module
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.payload import parse_payload
from hermes_tameru_plugin.recovery import EXPAND_TOOL_NAME
from hermes_tameru_plugin.render import make_ref


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
    candidate = engine.compress(messages, 200_000, force=True)
    # a prepared compaction is not committed until the host installs the candidate
    events = engine._telemetry.snapshot()["events"]
    assert "render" not in events and "parent_line" not in events
    engine.on_turn_complete(candidate)
    events = engine._telemetry.snapshot()["events"]
    assert "render" in events or "parent_line" in events


def _full_history():
    chat = Chat("Find why tests/test_orders.py::test_reserve_stock_rollback failed.")
    originals = {}
    for seed in range(10, 17):
        raw = pytest_failure_with_passes(seed)
        index = chat.tool("terminal", {"command": f"pytest -v --seed={seed}"}, raw)
        originals[make_ref(chat.messages[index], raw)] = parse_payload(raw).inner
    return chat.finish("What failed?"), originals


@pytest.mark.real_hermes
def test_full_preparation_preserves_originals_until_installed(monkeypatch):
    fake_summary_llm(monkeypatch)
    engine = make_engine(TameruSettings(store_max_entries=1, small_window_threshold_tokens=0))
    warm = Chat("Find why tests/test_orders.py::test_reserve_stock_rollback failed.")
    raw = pytest_failure_with_passes(1)
    index = warm.tool("terminal", {"command": "pytest -v"}, raw)
    old_ref = make_ref(warm.messages[index], raw)
    # Put the old result outside adaptive recency protection without changing
    # the policy or the full-compaction budgets this test exercises.
    warm_messages = warm.finish(
        "Find why tests/test_orders.py::test_reserve_stock_rollback failed.\n"
        + "Current reference material for the next step.\n" * 2_000
    )
    _, count = engine.prune_tool_results_only(warm_messages, 10**6)
    assert count > 0
    messages, originals = _full_history()
    before = copy.deepcopy(messages)
    chars_before = engine.get_status()["tameru"]["telemetry"]["chars"]

    candidate = engine.compress(messages, 200_000, force=True)
    assert candidate is not messages and candidate != messages
    assert messages == before
    assert engine.get_status()["tameru"]["telemetry"]["chars"] == chars_before
    assert json.loads(engine.handle_tool_call(
        EXPAND_TOOL_NAME, {"ref": old_ref, "max_chars": 50_000}))["content"] == parse_payload(raw).inner
    assert all("error" in json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref}))
               for ref in originals)

    # The old installed history is not evidence that the prepared replacement landed.
    engine.on_turn_complete(messages)
    assert engine.get_status()["tameru"]["telemetry"]["chars"] == chars_before
    assert set(item["ref"] for item in engine.activity_snapshot()["items"]) == {old_ref}
    engine.on_turn_complete(candidate)
    recovered = {
        ref: json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": ref, "max_chars": 50_000}))
        for ref in originals
    }
    hits = {ref: value["content"] for ref, value in recovered.items() if "content" in value}
    assert len(hits) == 1
    assert all(text == originals[ref] for ref, text in hits.items())
    assert "error" in json.loads(engine.handle_tool_call(EXPAND_TOOL_NAME, {"ref": old_ref}))
    committed = engine.get_status()["tameru"]["telemetry"]["chars"]
    assert committed["before"] > chars_before["before"]
    engine.on_turn_complete(candidate)
    assert engine.get_status()["tameru"]["telemetry"]["chars"] == committed


@pytest.mark.real_hermes
def test_full_adoption_checks_produced_prefix_and_discards_cloned_or_reset_plans(monkeypatch):
    fake_summary_llm(monkeypatch)
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0))
    messages, _ = _full_history()
    candidate = engine.compress(messages, 200_000, force=True)
    assert candidate != messages and len(candidate) > 1 and candidate[-1]["role"] == "user"

    clone = copy.deepcopy(engine)
    clone.on_turn_complete(copy.deepcopy(candidate))
    assert clone.get_status()["tameru"]["store"]["entries"] == 0
    assert clone.activity_snapshot()["items"] == []

    rejected = copy.deepcopy(candidate)
    rejected[0]["content"] = str(rejected[0].get("content", "")) + "\nDifferent produced prefix."
    engine.on_turn_complete(rejected)
    assert engine.get_status()["tameru"]["store"]["entries"] == 0
    assert engine.activity_snapshot()["items"] == []


    installed = copy.deepcopy(candidate)
    installed[-1]["content"] += "\nCurrent TODO: explain the failed assertion."
    request = copy.deepcopy(installed)
    assert engine.select_context(request, conversation_messages=installed, budget_tokens=128_000) is None
    assert request == installed
    assert engine.get_status()["tameru"]["store"]["entries"] > 0
    assert engine.activity_snapshot()["items"]

    # A fresh reset cannot inherit a late candidate, even with exactly matching bytes.
    engine.on_session_reset()
    messages2, _ = _full_history()
    late = engine.compress(messages2, 200_000, force=True)
    assert late != messages2
    engine.on_session_reset()
    engine.on_turn_complete(late)
    assert engine.get_status()["tameru"]["store"]["entries"] == 0
    assert engine.activity_snapshot()["items"] == []


@pytest.mark.real_hermes
@pytest.mark.parametrize("replacement", ["empty", "unchanged_copy"])
def test_rejected_full_result_cannot_claim_the_installed_history(monkeypatch, replacement):
    fake_summary_llm(monkeypatch)
    native = _base().compress

    def reject_after_planning(self, messages, *args, **kwargs):
        produced = native(self, messages, *args, **kwargs)
        assert produced != messages
        return [] if replacement == "empty" else copy.deepcopy(messages)

    monkeypatch.setattr(_base(), "compress", reject_after_planning)
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0))
    messages, _ = _full_history()
    engine.compress(messages, 200_000, force=True)
    engine.on_turn_complete(messages)
    status = engine.get_status()["tameru"]
    assert status["store"]["entries"] == 0
    assert status["telemetry"]["chars"]["before"] == 0
    assert status["activity"]["items"] == []
