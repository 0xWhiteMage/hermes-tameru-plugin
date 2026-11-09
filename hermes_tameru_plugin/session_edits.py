"""Per-chat persistence of the user's Keeps (a Restore is short-lived and is not saved).

Mirrors ``session_tuner``: the chat's own metadata holds a small versioned record of refs, a rebind or a
host-compression rotation carries it over, and a record that does not parse is ignored. Only short ref strings are
stored. Persisting is best effort: the in-memory state always applies.
"""
from __future__ import annotations

import re
from typing import Any

from .edits import MAX_ENTRIES
from .session_policy import _metadata_methods

EDITS_KEY = "tameru_edits_v1"
_REF = re.compile(r"[0-9a-f]{16}|[0-9a-f]{8}")


def _owner(engine):
    return getattr(engine, "_session_db", None), getattr(engine, "_session_id", "") or ""


def save(engine) -> bool:
    """Write the current Keeps for the bound chat. Never raises: False means 'kept in memory only'."""
    try:
        with engine._activity.lifecycle_lock:
            db, sid = _owner(engine)
            methods = _metadata_methods(db)
            if methods is None or not sid:
                return False
            read, patch, session = methods
            if session(sid) is None:
                return False
            record = {"version": 1, "pins": engine._edits.pins()}
            if read(sid, EDITS_KEY) != record:
                patch(sid, {EDITS_KEY: record})
            return read(sid, EDITS_KEY) == record
    except Exception:
        return False


def bind_edits(engine, *, rotation: bool = False) -> None:
    """Read this chat's saved Keeps, or carry the current ones across a host-compression rotation."""
    try:
        if rotation:
            save(engine)
            return
        db, sid = _owner(engine)
        methods = _metadata_methods(db)
        if methods is None or not sid:
            return
        record = methods[0](sid, EDITS_KEY)
        if not isinstance(record, dict) or record.get("version") != 1:
            return
        pins: Any = record.get("pins")
        if isinstance(pins, list):
            engine._edits.load_pins([p for p in pins if isinstance(p, str) and _REF.fullmatch(p)][:MAX_ENTRIES])
    except Exception:
        return
