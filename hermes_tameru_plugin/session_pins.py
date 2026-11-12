"""Per-chat persistence of the user's pinned instructions, mirroring ``session_edits``.

The plugin's own chat store (``chat_store``) holds a small versioned record of the pinned lines (at most ``MAX_PINS`` of ``PIN_CHARS``
characters), a rebind or a host-compression rotation carries it over, and a record that does not parse is ignored.
Persisting is best effort: the in-memory pins always apply.
"""
from __future__ import annotations

PART = "pins"


def _owner(engine):
    return getattr(engine, "_chat_store", None), getattr(engine, "_session_id", "") or ""


def save(engine) -> bool:
    """Write the current pins for the bound chat. Never raises: False means 'kept in memory only'."""
    try:
        with engine._activity.lifecycle_lock:
            store, sid = _owner(engine)
            if store is None or not sid:
                return False
            return store.write(sid, PART, {"version": 1, "pins": list(engine._pins.items)})
    except Exception:
        return False


def bind_pins(engine, *, rotation: bool = False) -> None:
    """Read this chat's saved pins, or carry the current ones across a host-compression rotation."""
    try:
        if rotation:
            save(engine)
            return
        store, sid = _owner(engine)
        if store is None or not sid:
            return
        record = store.read(sid, PART)
        if isinstance(record, dict) and record.get("version") == 1:
            engine._pins.load(record.get("pins"))
    except Exception:
        return
