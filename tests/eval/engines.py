"""Engine factories for the session replay.

``ENGINES`` maps a name to a zero-argument factory returning a fresh context engine. Hermes is imported
inside the factories, so importing this module never needs it.

Phase 2 adds the new engine with one line, without touching the harness::

    register("tameru_1_4", lambda: hermes_tameru_plugin.ExtractiveContextEngine())

(or from a conftest). ``tameru_1_3`` is the 1.3.0 plugin as vendored today; once the plugin itself moves
to 1.4 the factory below keeps returning the plugin's current engine, ``plugin_engine_version()`` reports
what it is, and the replay test skips the frozen 1.3.0 baseline comparison.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

ENGINES: dict[str, Callable[[], Any]] = {}

#: Baselines the replay test records and (for ``stock``) enforces. Phase 2 may append ``tameru_1_4``.
BASELINE_ENGINES = ("stock", "tameru_1_3")


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
    from hermes_tameru_plugin.tameru import compress_context

    return str(getattr(compress_context, "ENGINE_VERSION", "1.3.0"))


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
