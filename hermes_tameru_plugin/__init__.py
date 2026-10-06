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


def register(ctx: Any) -> None:
    """Register the engine with the settings of ``plugins.entries.tameru`` (never raises)."""
    try:
        settings, warnings = load_settings(ctx)
        for warning in warnings:
            logger.warning("tameru: %s", warning)
        engine = TameruContextEngine(settings=settings)
    except Exception as exc:
        logger.warning("tameru: engine setup failed (%r); registering it with defaults", exc)
        try:
            engine = TameruContextEngine(settings=TameruSettings())
        except Exception as fallback_exc:
            logger.warning("tameru: engine unavailable: %r", fallback_exc)
            return
    try:
        ctx.register_context_engine(engine)
    except Exception as exc:
        logger.warning("tameru: could not register the engine: %r", exc)
