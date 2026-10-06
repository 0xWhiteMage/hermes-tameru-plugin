"""Hermes hands every agent its own copy of the registered engine; a copy shares no per-agent state."""
from __future__ import annotations

import copy
import sys
import types

import pytest
from engine_support import Chat, demote, make_engine, pytest_failure, pytest_failure_with_passes

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.engine import TameruContextEngine
from hermes_tameru_plugin.render import parse_header


def _loaded_engine():
    engine = make_engine(TameruSettings(retained_extract_budget_chars=12_345), threshold_percent=0.42)
    chat = Chat("tests/test_orders.py::test_reserve_stock_rollback failed")
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    _, out = demote(engine, chat.finish(), idx)
    ref = parse_header(out[idx]["content"])["ref"]
    assert ref in engine._store and len(engine._memo) and engine._telemetry.snapshot()["events"]
    return engine, ref


@pytest.mark.parametrize("how", ["deepcopy", "clone_for_agent"])
def test_a_copy_starts_with_a_fresh_store_memo_and_telemetry(how):
    engine, ref = _loaded_engine()
    clone = copy.deepcopy(engine) if how == "deepcopy" else engine.clone_for_agent()
    assert isinstance(clone, TameruContextEngine) and clone is not engine
    assert ref not in clone._store and len(clone._store) == 0 and len(clone._memo) == 0
    assert clone._telemetry.snapshot()["events"] == {}
    assert clone._store is not engine._store and clone._memo is not engine._memo
    assert ref in engine._store, "the original keeps its state"


def test_a_copy_keeps_settings_caps_and_runtime_fields():
    engine, _ = _loaded_engine()
    engine.compression_count = 3
    clone = engine.clone_for_agent()
    assert clone._settings == engine._settings and clone._settings.retained_extract_budget_chars == 12_345
    assert (clone._store.max_entries, clone._store.max_chars) == (
        engine._store.max_entries, engine._store.max_chars,
    )
    assert clone.threshold_percent == engine.threshold_percent == 0.42
    assert clone.compression_count == 3 and clone._seams == engine._seams
    assert clone.proactive_prune_tokens == engine.proactive_prune_tokens


def test_clones_do_not_share_mutable_state():
    engine, _ = _loaded_engine()
    clone = engine.clone_for_agent()
    clone.update_model("other-model", 32_000)
    assert engine.context_length != 32_000 and clone.context_length == 32_000
    clone._store.put("aaaaaaaa", "x" * 10)
    assert "aaaaaaaa" not in engine._store
    clone._warnings.append("w")
    assert "w" not in engine._warnings


def test_a_clone_works_as_an_engine():
    engine, _ = _loaded_engine()
    clone = engine.clone_for_agent()
    chat = Chat("tests/test_orders.py::test_reserve_stock_rollback failed")
    idx = chat.tool("terminal", {"command": "pytest -v"}, pytest_failure_with_passes())
    changed, out = demote(clone, chat.finish(), idx)
    assert changed and parse_header(out[idx]["content"])["rung"] == "extract"
    assert len(clone._store) == 1 and len(engine._store) == 1


@pytest.mark.real_hermes
def test_hermes_select_context_engine_clones_the_registered_instance(monkeypatch):
    """``agent_init._select_context_engine``: the plugin system's single instance, cloned per agent."""
    from agent import agent_init

    registered, _ = _loaded_engine()
    monkeypatch.setitem(sys.modules, "plugins.context_engine", types.SimpleNamespace(
        load_context_engine=lambda name: None))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", types.SimpleNamespace(
        get_plugin_context_engine=lambda: registered))
    chosen = agent_init._select_context_engine({"context": {"engine": "tameru"}})
    assert isinstance(chosen, TameruContextEngine)
    assert chosen is not registered and len(chosen._store) == 0
