"""Per-chat persistence of the user's pinned instructions, mirroring ``session_edits``.

The chat's own metadata holds a small versioned record of the pinned lines (at most ``MAX_PINS`` of ``PIN_CHARS``
characters), a rebind or a host-compression rotation carries it over, and a record that does not parse is ignored.
Persisting is best effort: the in-memory pins always apply.
"""
from __future__ import annotations

from .session_policy import _metadata_methods

PINS_KEY = "tameru_pins_v1"


def _owner(engine):
    return getattr(engine, "_session_db", None), getattr(engine, "_session_id", "") or ""


def save(engine) -> bool:
    """Write the current pins for the bound chat. Never raises: False means 'kept in memory only'."""
    try:
        with engine._activity.lifecycle_lock:
            db, sid = _owner(engine)
            methods = _metadata_methods(db)
            if methods is None or not sid:
                return False
            read, patch, session = methods
            if session(sid) is None:
                return False
            record = {"version": 1, "pins": list(engine._pins.items)}
            if read(sid, PINS_KEY) != record:
                patch(sid, {PINS_KEY: record})
            return read(sid, PINS_KEY) == record
    except Exception:
        return False


def bind_pins(engine, *, rotation: bool = False) -> None:
    """Read this chat's saved pins, or carry the current ones across a host-compression rotation."""
    try:
        if rotation:
            save(engine)
            return
        db, sid = _owner(engine)
        methods = _metadata_methods(db)
        if methods is None or not sid:
            return
        record = methods[0](sid, PINS_KEY)
        if isinstance(record, dict) and record.get("version") == 1:
            engine._pins.load(record.get("pins"))
    except Exception:
        return
