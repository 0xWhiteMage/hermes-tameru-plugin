"""The active Hermes home folder, the way Hermes itself finds it.

``hermes_constants.get_hermes_home()`` follows the active profile and Hermes' per-platform default (``%LOCALAPPDATA%\hermes`` on Windows), so the
plugin's own files (the stats ledger, the telemetry log's allowed folder, ``config.yaml`` for the stub) sit beside Hermes' data. Without Hermes
importable (a directory install under test, the stub) it falls back to ``$HERMES_HOME`` and then ``~/.hermes``.
"""
from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.path.expandvars(os.environ.get("HERMES_HOME", "").strip() or "~/.hermes")).expanduser()
