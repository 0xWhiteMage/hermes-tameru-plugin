"""Per-chat persistence of the adaptive host-settings offsets (two numbers), mirroring ``session_tuner``.

The plugin's own chat store (``chat_store``) holds a small versioned record, a rebind or a host-compression rotation carries it over, and
a record that does not parse is ignored. Persisting is best effort: the in-memory state always applies.
"""
from __future__ import annotations

PART = "host"


def _owner(engine):
    return getattr(engine, "_chat_store", None), getattr(engine, "_session_id", "") or ""


def save(engine) -> bool:
    """Write the current offsets for the bound chat. Never raises: False means 'kept in memory only'."""
    try:
        with engine._activity.lifecycle_lock:
            store, sid = _owner(engine)
            if store is None or not sid:
                return False
            return store.write(sid, PART, {"version": 1, **engine._host.state()})
    except Exception:
        return False


def bind_host(engine, *, rotation: bool = False) -> None:
    """Read this chat's saved offsets, or carry the current ones across a host-compression rotation."""
    try:
        if rotation:
            save(engine)
            return
        store, sid = _owner(engine)
        if store is None or not sid:
            return
        record = store.read(sid, PART)
        if isinstance(record, dict) and record.get("version") == 1:
            engine._host.load(record)
    except Exception:
        return
