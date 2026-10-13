"""Hermes context-engine plugin for Tameru deterministic compaction.

Two install paths, same code:

* Directory plugin: copy this directory to ``~/.hermes/plugins/tameru/``
* Pip plugin: ``pip install`` this repo; it registers through the
  ``hermes_agent.plugins`` entry-point group.

Select it with ``context.engine: tameru`` in ``~/.hermes/config.yaml``. The engine lives in
``engine.py``; this module only registers it.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import TameruSettings, load_settings
from .engine import TameruContextEngine

ExtractiveContextEngine = TameruContextEngine  # name of the 1.3.0 plugin

__all__ = ["ExtractiveContextEngine", "TameruContextEngine", "register"]

logger = logging.getLogger(__name__)


# Hermes sizes an engine it loads from a plugin after the fact: it calls ``update_model`` with the real window right
# after it builds the engine. The engine is built before that without a model, and its constructor reads the window
# once (the prune trigger follows it); left to itself that read looks the placeholder model "pending" up in the
# model catalogue, a network call of up to 15 s per agent start when the catalogue cannot be reached, plus a
# "could not determine context length" warning. This window stands in until ``update_model`` replaces it.
_PLACEHOLDER_CONTEXT_LENGTH = 128_000


def _build_engine(settings: TameruSettings) -> TameruContextEngine:
    """The engine for ``settings``, built without a model catalogue lookup when this Hermes allows it."""
    try:
        return TameruContextEngine(settings=settings, config_context_length=_PLACEHOLDER_CONTEXT_LENGTH)
    except TypeError:   # a Hermes whose compressor takes no such argument
        return TameruContextEngine(settings=settings)


def register(ctx: Any) -> None:
    """Register the engine with the settings of ``plugins.entries.tameru`` (never raises)."""
    try:
        settings, warnings = load_settings(ctx)
        for warning in warnings:
            logger.warning("tameru: %s", warning)
        engine = _build_engine(settings)
    except Exception as exc:
        logger.warning("tameru: engine setup failed (%r); registering it with defaults", exc)
        try:
            engine = _build_engine(TameruSettings())
        except Exception as fallback_exc:
            logger.warning("tameru: engine unavailable: %r", fallback_exc)
            return
    try:
        ctx.register_context_engine(engine)
    except Exception as exc:
        logger.warning("tameru: could not register the engine: %r", exc)
