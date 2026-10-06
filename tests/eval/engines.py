"""Engine factories for the session replay.

``ENGINES`` maps a name to a zero-argument factory returning a fresh context engine. Hermes is imported
inside the factories, so importing this module never needs it.

``tameru_1_4`` is the seam-based plugin engine with the SAME Hermes prune gates as ``stock`` (an equal-gates
comparison); ``tameru_1_4_product`` is the plugin as shipped, with its own gate defaults. ``tameru_1_3`` is kept only as the name of the frozen 1.3.0
baseline: its factory returns whatever engine the plugin ships now, ``plugin_engine_version()`` reports what
that is, and the replay test compares the saved 1.3.0 baseline only while the vendored engine is 1.3.x.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

ENGINES: dict[str, Callable[[], Any]] = {}

#: Engines the replay test runs and compares exactly against ``baselines/<name>.json``.
BASELINE_ENGINES = ("stock", "tameru_1_4", "tameru_1_4_product")


def register(name: str, factory: Callable[[], Any] | None = None):
    """Register ``factory`` under ``name`` (usable as a decorator when ``factory`` is omitted)."""
    def _add(fn: Callable[[], Any]) -> Callable[[], Any]:
        ENGINES[name] = fn
        return fn

    return _add(factory) if factory is not None else _add


def make_engine(name: str) -> Any:
    try:
        return ENGINES[name]()
    except KeyError:
        raise KeyError(f"unknown engine {name!r}; registered: {sorted(ENGINES)}") from None


def plugin_engine_version() -> str:
    """Engine version of the vendored Tameru modules (``1.3.x`` before ENGINE_VERSION existed)."""
    import importlib

    # importlib: ``hermes_tameru_plugin.tameru.compress_context`` is also a function re-exported by the package
    module = importlib.import_module("hermes_tameru_plugin.tameru.compress_context")
    return str(getattr(module, "ENGINE_VERSION", "1.3.0"))


@register("stock")
def stock() -> Any:
    """Real Hermes ``ContextCompressor`` with the proactive tool-result prune enabled at 48K tokens."""
    from agent.context_compressor import ContextCompressor

    return ContextCompressor(model="test-model", proactive_prune_tokens=48_000, quiet_mode=True)


@register("tameru_1_3")
def tameru_1_3() -> Any:
    """The plugin's current ``ExtractiveContextEngine`` (1.3.0: prune-and-post-check around the parent)."""
    import hermes_tameru_plugin

    return hermes_tameru_plugin.ExtractiveContextEngine(quiet_mode=True)


@register("tameru_1_4")
def tameru_1_4() -> Any:
    """The seam-based plugin engine with the SAME Hermes prune gates as ``stock``.

    The plugin's own defaults (``default_proactive_prune_min_result_chars`` = 2000) are product tuning, not an
    equal-gates comparison, so the min-result gate is pinned to Hermes' 8000 here.
    """
    from hermes_tameru_plugin.engine import TameruContextEngine

    return TameruContextEngine(proactive_prune_tokens=48_000, proactive_prune_min_result_chars=8000, quiet_mode=True)


@register("tameru_1_4_product")
def tameru_1_4_product() -> Any:
    """The plugin exactly as shipped (its own prune-gate defaults: 48K tokens, 2000-char minimum result)."""
    from hermes_tameru_plugin.engine import TameruContextEngine

    return TameruContextEngine(quiet_mode=True)
