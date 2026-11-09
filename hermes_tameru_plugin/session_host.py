"""Per-chat persistence of the adaptive host-settings offsets (two numbers), mirroring ``session_tuner``.

The chat's own metadata holds a small versioned record, a rebind or a host-compression rotation carries it over, and
a record that does not parse is ignored. Persisting is best effort: the in-memory state always applies.
"""
from __future__ import annotations

from .session_policy import _metadata_methods

HOST_KEY = "tameru_host_v1"


def _owner(engine):
    return getattr(engine, "_session_db", None), getattr(engine, "_session_id", "") or ""


def save(engine) -> bool:
    """Write the current offsets for the bound chat. Never raises: False means 'kept in memory only'."""
    try:
        with engine._activity.lifecycle_lock:
            db, sid = _owner(engine)
            methods = _metadata_methods(db)
            if methods is None or not sid:
                return False
            read, patch, session = methods
            if session(sid) is None:
                return False
            record = {"version": 1, **engine._host.state()}
            if read(sid, HOST_KEY) != record:
                patch(sid, {HOST_KEY: record})
            return read(sid, HOST_KEY) == record
    except Exception:
        return False


def bind_host(engine, *, rotation: bool = False) -> None:
    """Read this chat's saved offsets, or carry the current ones across a host-compression rotation."""
    try:
        if rotation:
            save(engine)
            return
        db, sid = _owner(engine)
        methods = _metadata_methods(db)
        if methods is None or not sid:
            return
        record = methods[0](sid, HOST_KEY)
        if isinstance(record, dict) and record.get("version") == 1:
            engine._host.load(record)
    except Exception:
        return
