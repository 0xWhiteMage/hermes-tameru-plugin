"""The plugin's proactive-prune trigger follows the model's window (``default_proactive_prune_ratio``).

A fixed 48K trigger pruned a 200K window long before it filled: in the 30-turn replay stock Hermes never
compacted and kept 17/17 gold facts while the plugin kept 12/17. The plugin-supplied trigger is now
``max(default_proactive_prune_tokens, ratio * compaction threshold)``; a user's own value is never touched.
"""
from __future__ import annotations

import copy

import pytest

import hermes_tameru_plugin.engine as engine_mod
from hermes_tameru_plugin.config import TameruSettings, scaled_prune_tokens
from hermes_tameru_plugin.engine import TameruContextEngine


@pytest.fixture
def user_config(monkeypatch):
    """``serve(compression=...)``: the user's ``compression`` section as the engine reads it."""
    def serve(compression=None):
        merged = {"compression": dict(compression or {})}
        raw = {"compression": dict(compression or {})} if compression else {}
        monkeypatch.setattr(engine_mod, "read_hermes_config", lambda: (merged, raw))

    serve()
    return serve


def _engine(settings=None, **kwargs) -> TameruContextEngine:
    return TameruContextEngine(settings=settings or TameruSettings(), quiet_mode=True, **kwargs)


def _expected(engine: TameruContextEngine, settings: TameruSettings | None = None) -> int:
    settings = settings or TameruSettings()
    return max(settings.default_proactive_prune_tokens, round(settings.default_proactive_prune_ratio * engine.threshold_tokens))


@pytest.mark.parametrize("window", [32_000, 64_000, 128_000, 200_000, 1_000_000])
def test_the_plugin_trigger_follows_the_compaction_threshold(user_config, window):
    engine = _engine()
    engine.update_model("test-model", window)
    assert engine.threshold_tokens > 0
    assert engine.proactive_prune_tokens == _expected(engine)
    assert engine.proactive_prune_tokens >= 48_000


def test_small_windows_keep_the_fixed_trigger_and_large_ones_prune_later(user_config):
    engine = _engine()
    engine.update_model("test-model", 64_000)
    small = engine.proactive_prune_tokens
    engine.update_model("test-model", 200_000)   # a model switch to a bigger window rescales
    large = engine.proactive_prune_tokens
    assert small == 48_000 or small == _expected(engine) and engine.threshold_tokens * 0.75 > 48_000
    assert large == _expected(engine)
    if engine.threshold_tokens * 0.75 > 48_000:
        assert large > small


def test_a_user_value_is_never_rescaled(user_config):
    user_config({"proactive_prune_tokens": 30_000})
    engine = _engine()
    engine.update_model("test-model", 200_000)
    assert engine.proactive_prune_tokens == 30_000


def test_an_explicit_argument_is_never_rescaled(user_config):
    engine = _engine(proactive_prune_tokens=48_000)
    engine.update_model("test-model", 200_000)
    assert engine.proactive_prune_tokens == 48_000


def test_ratio_zero_keeps_the_fixed_trigger(user_config):
    settings = TameruSettings(default_proactive_prune_ratio=0.0)
    engine = _engine(settings)
    engine.update_model("test-model", 200_000)
    assert engine.proactive_prune_tokens == 48_000


def test_the_kill_switch_stays_stock(user_config):
    engine = _engine(TameruSettings(enabled=False))
    engine.update_model("test-model", 200_000)
    assert engine.proactive_prune_tokens == 0   # Hermes' own default: the proactive prune is opt-in


def test_a_clone_keeps_scaling(user_config):
    engine = _engine()
    clone = engine.clone_for_agent()
    clone.update_model("test-model", 200_000)
    assert clone.proactive_prune_tokens == _expected(clone)
    assert copy.deepcopy(clone).proactive_prune_tokens == clone.proactive_prune_tokens


@pytest.mark.parametrize(("threshold", "expected"), [
    (0, 48_000), (None, 48_000), ("junk", 48_000), (54_400, 48_000), (108_800, 81_600), (170_000, 127_500),
])
def test_scaled_prune_tokens(threshold, expected):
    assert scaled_prune_tokens(TameruSettings(), threshold) == expected
    assert scaled_prune_tokens(TameruSettings(default_proactive_prune_tokens=0), threshold) == 0
