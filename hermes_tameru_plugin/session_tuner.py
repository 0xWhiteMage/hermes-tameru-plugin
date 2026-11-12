"""Per-chat persistence of the self-tuner and the user's two choices: bias and pause.

Mirrors ``session_policy``: the plugin's own chat store (``chat_store``) holds a small versioned record, a rebind or a
host-compression rotation carries it over, and a record that does not parse is ignored (defaults apply).
Only scalars are stored. Persisting is best effort: the in-memory state always applies.
"""
from __future__ import annotations

from typing import Any

from .session_policy import PolicyError, _binding, _current

PART = "tuner"


def _record(state) -> dict[str, Any]:
    return {"version": 1, "paused": bool(state.paused), "tuner": state.tuner.state()}


def _write(store, sid, record) -> bool:
    return store is not None and store.write(sid, PART, record)


def save(engine) -> bool:
    """Persist the current tuner state for the bound chat. Never raises: False means 'kept in memory only'."""
    try:
        with engine._activity.lifecycle_lock:
            state, db, sid, _life = _binding(engine)
            return bool(sid) and _write(db, sid, _record(state))
    except Exception:
        return False


def set_posture(engine, *, session_id: str, lifecycle_id: str, bias=None, paused=None) -> dict:
    """Apply the user's bias (-1 save space ... +1 preserve detail) and/or pause for this chat."""
    with engine._activity.lifecycle_lock:
        owner = _binding(engine)
        state, db, sid, life = owner
        if not sid or sid != session_id or life != lifecycle_id:
            raise PolicyError("The active session changed. Open context details again.")
        if bias is None and paused is None:
            raise PolicyError("Choose a bias or pause.")
        if paused is not None and type(paused) is not bool:
            raise PolicyError("Pause must be on or off.")
        if bias is not None:
            try:
                state.tuner.set_bias(bias)
            except ValueError:
                raise PolicyError("Bias must be a number from -1 (save space) to 1 (preserve detail).") from None
        if paused is not None:
            state.paused = paused
        if bias is not None:
            engine._event("posture", change="bias", bias=round(state.tuner.bias, 2), paused=state.paused)
        if paused is not None:
            engine._event("posture", change="pause" if paused else "resume",
                          bias=round(state.tuner.bias, 2), paused=state.paused)
        persisted = _write(db, sid, _record(state)) if _current(engine, owner) else False
        status = engine.get_policy_status()
        status["persisted"] = persisted
        return status


def reset_chat(engine, *, session_id: str, lifecycle_id: str) -> dict:
    """Put this chat's self-tuning, bias, pause and Hermes-setting offsets back to neutral (the user's ``/tameru reset``)."""
    with engine._activity.lifecycle_lock:
        owner = _binding(engine)
        state, db, sid, life = owner
        if not sid or sid != session_id or life != lifecycle_id:
            raise PolicyError("The active session changed. Open context details again.")
        state.tuner.reset()
        state.paused = False
        engine._event("posture", change="reset", bias=0.0, paused=False)
        persisted = _write(db, sid, _record(state)) if _current(engine, owner) else False
    try:
        host = getattr(engine, "_host", None)
        if host is not None and (host.offset or host.scale != 1.0):
            engine.reset_host_settings()                      # its own lock; the configured values come back
    except Exception:
        engine._telemetry.count("error", where="reset_host")
    status = engine.get_policy_status()
    status["persisted"] = persisted
    return status


def bind_tuner(engine, *, rotation: bool = False) -> None:
    """Read the owning chat's saved tuner, or carry the current one across a host-compression rotation."""
    owner = _binding(engine)
    state, db, sid, _life = owner
    if not sid:
        return
    try:
        if rotation:
            _write(db, sid, _record(state))
            return
        if (getattr(engine._settings, "autotune", False) and getattr(engine._settings, "learned_priors", False)
                and state.tuner.pristine()):
            from . import learned                            # a new chat starts from what earlier chats learned
            prior = learned.seed()
            if prior is not None:
                state.tuner.seed(prior)                      # a saved record for this chat still overrides it below
        if db is None:
            return
        record = db.read(sid, PART)
        if not _current(engine, owner) or not isinstance(record, dict) or record.get("version") != 1:
            return
        paused = record.get("paused")
        if state.tuner.load(record.get("tuner")) and type(paused) is bool:
            state.paused = paused
    except Exception:
        return
