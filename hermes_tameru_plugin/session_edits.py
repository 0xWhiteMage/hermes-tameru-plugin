"""Per-chat persistence of the user's Keeps (a Restore is short-lived and is not saved).

Mirrors ``session_tuner``: the plugin's own chat store (``chat_store``) holds a small versioned record of refs, a rebind or a
host-compression rotation carries it over, and a record that does not parse is ignored. Only short ref strings are
stored. Persisting is best effort: the in-memory state always applies.
"""
from __future__ import annotations

import re
from typing import Any

from .edits import MAX_ENTRIES

PART = "edits"
_REF = re.compile(r"[0-9a-f]{16}|[0-9a-f]{8}")


def _owner(engine):
    return getattr(engine, "_chat_store", None), getattr(engine, "_session_id", "") or ""


def save(engine) -> bool:
    """Write the current Keeps for the bound chat. Never raises: False means 'kept in memory only'."""
    try:
        with engine._activity.lifecycle_lock:
            store, sid = _owner(engine)
            if store is None or not sid:
                return False
            return store.write(sid, PART, {"version": 1, "pins": engine._edits.pins()})
    except Exception:
        return False


def bind_edits(engine, *, rotation: bool = False) -> None:
    """Read this chat's saved Keeps, or carry the current ones across a host-compression rotation."""
    try:
        if rotation:
            save(engine)
            return
        store, sid = _owner(engine)
        if store is None or not sid:
            return
        record = store.read(sid, PART)
        if not isinstance(record, dict) or record.get("version") != 1:
            return
        pins: Any = record.get("pins")
        if isinstance(pins, list):
            engine._edits.load_pins([p for p in pins if isinstance(p, str) and _REF.fullmatch(p)][:MAX_ENTRIES])
    except Exception:
        return
