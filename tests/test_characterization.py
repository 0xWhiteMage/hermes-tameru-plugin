"""Regression tests for the plugin 1.3.0 integration defects (gap ids from the research plan).

They began as ``xfail(strict=True)`` characterizations of the 1.3.0 behaviour; the seam-based 1.4 engine fixes
them, so each now asserts the fixed behaviour. They run against real Hermes only (``real_hermes``).

* G1  - ``prune_tool_results_only`` returns the INPUT object when Hermes itself no-ops.
* G2  - a committed prune is stable: pruning the pruned list changes nothing (the ladder tests cover it in depth).
* G3  - ``compress`` returns exactly what ``super().compress`` returned (no post-check swap).
* G4  - ``compress`` accepts and forwards ``bypass_cooldown``.
* G11 - the user's ``compression.*`` config reaches a plugin-provided engine.
"""
from __future__ import annotations

import inspect
import random
import shutil
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_hermes

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "hermes_tameru_plugin"


def _log(seed: int = 1, lines: int = 300) -> str:
    rng = random.Random(seed)
    rows = [
        f"2024-01-01T00:00:{i % 60:02d} INFO worker-{rng.randint(1, 9)} handled request "
        f"id={rng.randint(10000, 99999)} path=/api/v1/items/{rng.randint(1, 500)} status=200"
        for i in range(lines)
    ]
    rows.append("2024-01-01T00:05:00 ERROR payment gateway timeout order=ORD-77213 after 30s")
    return "\n".join(rows)


def _session(make_tool_turn, payloads, rounds: int = 4):
    """system, user, <rounds> big terminal results (distinct commands: no stuck-loop gate), user."""
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "look at the logs"}]
    for i in range(rounds):
        a, t = make_tool_turn(
            "terminal", {"command": f"tail -n {100 + i} app.log"}, payloads.terminal(_log(i)), f"call_{i}",
        )
        msgs += [a, t]
    msgs.append({"role": "user", "content": "why did ORD-77213 fail?"})
    return msgs


def _plugin_engine_class():
    import hermes_tameru_plugin

    return hermes_tameru_plugin.ExtractiveContextEngine


def test_g1_prune_returns_input_object_when_hermes_noops(make_tool_turn, payloads):
    """Hermes returns ``(messages, 0)`` (same object) with ``proactive_prune_tokens == 0``; so must we.

    1.3.0 pruned with Tameru *first* and handed Hermes the new list, so every call rewrote bytes
    and broke the prompt cache even when Hermes' own gates said "do nothing".
    """
    engine = _plugin_engine_class()(proactive_prune_tokens=0, quiet_mode=True, config_context_length=64_000)
    messages = _session(make_tool_turn, payloads)
    out, count = engine.prune_tool_results_only(messages, current_tokens=10)
    assert out is messages and count == 0


def test_g4_compress_accepts_bypass_cooldown():
    """Hermes passes ``bypass_cooldown=True`` on provider-proven overflow recovery."""
    params = inspect.signature(_plugin_engine_class().compress).parameters
    assert "bypass_cooldown" in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


def test_g4_bypass_cooldown_reaches_the_parent(monkeypatch, make_tool_turn, payloads):
    from agent.context_compressor import ContextCompressor

    seen: dict = {}

    def parent(self, messages, *a, **kw):
        seen.update(kw)
        return messages

    monkeypatch.setattr(ContextCompressor, "compress", parent)
    engine = _plugin_engine_class()(quiet_mode=True, config_context_length=64_000)
    engine.compress(_session(make_tool_turn, payloads), current_tokens=10**6, bypass_cooldown=True)
    assert seen.get("bypass_cooldown") is True


def test_g3_compress_returns_what_parent_returned(monkeypatch, make_tool_turn, payloads):
    """1.3.0 post-checked the parent's summary AFTER Hermes mutated its state, then swapped in its own list."""
    from agent.context_compressor import ContextCompressor

    parent_result = [{"role": "user", "content": "a summary that dropped every tool fact"}]
    monkeypatch.setattr(ContextCompressor, "compress", lambda self, messages, *a, **kw: parent_result)
    engine = _plugin_engine_class()(quiet_mode=True, config_context_length=64_000)
    result = engine.compress(_session(make_tool_turn, payloads), current_tokens=10**6)
    assert result is parent_result


def test_g2_a_committed_prune_is_stable(make_tool_turn, payloads):
    """Pruning the already pruned list changes nothing: committed rows keep their bytes."""
    engine = _plugin_engine_class()(
        quiet_mode=True, config_context_length=64_000, proactive_prune_tokens=1,
        proactive_prune_min_reclaim_tokens=0, proactive_prune_min_result_chars=200, protect_last_n=4,
    )
    messages = _session(make_tool_turn, payloads, rounds=8)
    once, n = engine.prune_tool_results_only(messages, current_tokens=500_000)
    assert once is not messages and n > 0
    twice, n2 = engine.prune_tool_results_only(once, current_tokens=500_000)
    assert twice == once and n2 == 0


def test_g11_user_compression_config_reaches_the_engine(hermes_home):
    """``compression.*`` of the user's config.yaml reaches the plugin engine (G11; 1.3.0 passed it only
    ``model_thresholds``).

    Drives Hermes' real path: config.yaml -> ``_select_context_engine`` (user plugin dir) ->
    ``_build_context_engine``.
    """
    from agent import agent_init
    from hermes_cli.config import load_config

    plugins = hermes_home / "plugins"
    plugins.mkdir()
    shutil.copytree(PLUGIN_DIR, plugins / "tameru", ignore=shutil.ignore_patterns("__pycache__"))
    (hermes_home / "config.yaml").write_text(
        "context:\n  engine: tameru\ncompression:\n  protect_last_n: 7\n  target_ratio: 0.35\n",
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg["compression"]["protect_last_n"] == 7  # the config really is what Hermes loaded

    agent = types.SimpleNamespace(
        model="test-model", provider="", base_url="", api_key="", api_mode="chat_completions",
        quiet_mode=True, session_id="s1", _compression_threshold_autoraised=None,
    )
    try:
        settings = agent_init._parse_compression_config(agent, cfg)
        agent_init._build_context_engine(agent, cfg, settings, [], 100_000, None)
        engine = agent.context_compressor
        assert engine.name == "tameru"
        assert (engine.protect_last_n, engine.summary_target_ratio) == (7, 0.35)
    finally:
        for name in [m for m in sys.modules if m.startswith("_hermes_user_context_engine")]:
            del sys.modules[name]
