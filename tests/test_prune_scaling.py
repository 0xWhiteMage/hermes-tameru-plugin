"""The plugin's proactive-prune trigger follows the model's window (``default_proactive_prune_ratio``).

A fixed 48K trigger pruned a 200K window long before it filled: in the 30-turn replay stock Hermes never
compacted and kept 17/17 gold facts while the plugin kept 12/17. The plugin-supplied trigger is now
``max(default_proactive_prune_tokens, ratio * compaction threshold)``; a user's own value is never touched. Where that is
not below the threshold (a window under ~57K: 48,000 against 27,200 at 32K) the prune could never run, the window
overflowing first, so ``small_window_prune_ratio * threshold`` is used instead.
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
    trigger = max(settings.default_proactive_prune_tokens, round(settings.default_proactive_prune_ratio * engine.threshold_tokens))
    if trigger >= engine.threshold_tokens:   # a fixed trigger the prompt could never reach: it is dead code
        trigger = round(settings.small_window_prune_ratio * engine.threshold_tokens)
    return trigger


@pytest.mark.parametrize("window", [32_000, 64_000, 128_000, 200_000, 1_000_000])
def test_the_plugin_trigger_follows_the_compaction_threshold(user_config, window):
    engine = _engine()
    engine.update_model("test-model", window)
    assert engine.threshold_tokens > 0
    assert engine.proactive_prune_tokens == _expected(engine)
    assert engine.proactive_prune_tokens < engine.threshold_tokens   # reachable, on every window
    if engine.threshold_tokens > 48_000:   # (the stub's thresholds are lower than Hermes')
        assert engine.proactive_prune_tokens >= 48_000


@pytest.mark.parametrize(("threshold", "trigger"), [
    (27_200, 16_320),     # a 32K window: 0.60 of the threshold (the fixed 48,000 is above the window)
    (34_000, 20_400),     # 40K
    (40_800, 24_480),     # 48K
    (47_600, 28_560),     # 56K: the threshold is still under 48,000
    (49_300, 48_000),     # 58K: the fixed trigger is below the threshold again
    (54_400, 48_000),     # 64K, and up to the window where 0.75 of the threshold exceeds it
    (72_000, 54_000),     # 96K
    (150_000, 112_500),   # 200K
])
def test_the_trigger_for_each_threshold(threshold, trigger):
    assert scaled_prune_tokens(TameruSettings(), threshold) == trigger


def test_the_trigger_is_below_the_threshold_wherever_one_is_asked_for():
    settings = TameruSettings()
    for threshold in range(10_000, 400_000, 700):
        assert scaled_prune_tokens(settings, threshold) < threshold


def test_the_small_window_ratio_is_configurable_and_zero_keeps_the_fixed_trigger():
    assert scaled_prune_tokens(TameruSettings(small_window_prune_ratio=0.5), 27_200) == 13_600
    assert scaled_prune_tokens(TameruSettings(small_window_prune_ratio=0), 27_200) == 48_000
    assert scaled_prune_tokens(TameruSettings(small_window_prune_ratio=0.5), 54_400) == 48_000   # not a small window


@pytest.mark.real_hermes   # the thresholds of 32K, 64K and 200K windows are Hermes'
def test_the_engine_prunes_early_on_a_small_window_and_late_on_a_large_one(user_config):
    engine = _engine()
    engine.update_model("test-model", 32_000)
    small = engine.proactive_prune_tokens
    engine.update_model("test-model", 64_000)
    middle = engine.proactive_prune_tokens
    engine.update_model("test-model", 200_000)   # a model switch to a bigger window rescales
    assert small < middle
    assert engine.proactive_prune_tokens == _expected(engine) > middle


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
