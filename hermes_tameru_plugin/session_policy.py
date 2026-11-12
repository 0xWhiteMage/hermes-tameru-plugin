from __future__ import annotations
from dataclasses import asdict, replace
from .policy import Controls

PART = 'policy'
_FIELDS = frozenset(('mode', 'preference', 'activation', 'recent'))

class PolicyError(ValueError):
    """A safe user-facing policy-control error; never include database payloads."""


def _binding(engine):
    """(policy state, the chat store or None, session id, lifecycle id)."""
    return (engine._policy, getattr(engine, '_chat_store', None),
            getattr(engine, '_session_id', '') or '', engine._activity.lifecycle_id)


def _current(engine, owner):
    state, db, sid, life = owner
    return (engine._policy is state and getattr(engine, '_chat_store', None) is db
            and (getattr(engine, '_session_id', '') or '') == sid
            and engine._activity.lifecycle_id == life)


def _record(controls):
    return {'version': 1, 'controls': asdict(controls)}


def _decode(record):
    if not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != 1:
        return None
    values = record.get('controls')
    if not isinstance(values, dict) or set(values) != _FIELDS:
        return None
    try:
        return Controls(**values)
    except (ValueError, TypeError):
        return None


def _persist(store, sid, record):
    """True when the chat's policy is saved (``None`` clears it); False with no store; PolicyError when the store did not confirm it."""
    if store is None:
        return False
    if not store.write(sid, PART, record):
        raise PolicyError('Preferences could not be confirmed. Refresh before retrying.')
    return True


def set_controls(engine, updates, *, session_id, lifecycle_id, reset=False):
    with engine._activity.lifecycle_lock:
        owner = _binding(engine)
        state, db, sid, life = owner
        if not sid or sid != session_id or life != lifecycle_id:
            raise PolicyError('The active session changed. Open context details again.')
        if not (engine._settings.enabled and engine._seams.demote):
            raise PolicyError('Tameru policy controls are not active for this context.')
        if (not isinstance(updates, dict) or set(updates) - _FIELDS
                or type(reset) is not bool or (reset and updates) or (not reset and not updates)):
            raise PolicyError('Choose a supported Tameru preference, timing, recent protection or policy mode.')
        try:
            candidate = state.defaults if reset else replace(state.controls, **updates)
        except (ValueError, TypeError):
            raise PolicyError('Choose a supported Tameru preference, timing, recent protection or policy mode.') from None
        if (not reset and candidate.mode == 'legacy'
                and any(key != 'mode' and getattr(candidate, key) != getattr(state.controls, key)
                        for key in updates)):
            raise PolicyError('Select Adaptive or Fixed before changing adaptive preferences.')
        try:
            durable = _persist(db, sid, None if reset else _record(candidate))
        except PolicyError:
            raise
        except Exception:
            raise PolicyError('Preferences could not be confirmed. Refresh before retrying.') from None
        if not _current(engine, owner):
            raise PolicyError('The active session changed. Open context details again.')
        state.controls = candidate
        state.has_override = not reset
        state.controls_source = ('default' if reset else 'session') if durable else 'memory'
        state.controls_issue = None
        return engine.get_policy_status()


def bind_controls(engine, *, rotation=False):
    """Read the owning chat's metadata, or carry current choices across host compression."""
    owner = _binding(engine)
    state, db, sid, _life = owner
    if not sid:
        return
    # A local-only selection lasts until this agent restarts or the user saves another choice.
    if not rotation and state.controls_source == 'memory':
        return
    try:
        if rotation:
            durable = _persist(db, sid, _record(state.controls) if state.has_override else None)
            if _current(engine, owner):
                state.controls_source = ('session' if state.has_override else 'default') if durable else 'memory'
                state.controls_issue = None
            return
        if db is None:
            return
        record = db.read(sid, PART)
        parsed = _decode(record) if record is not None else None
        if not _current(engine, owner):
            return
        state.controls = parsed or state.defaults
        state.has_override = parsed is not None
        state.controls_source = 'session' if parsed is not None else 'default'
        state.controls_issue = ('Saved preferences were invalid; using defaults.'
                                if record is not None and parsed is None else None)
    except Exception:
        if _current(engine, owner):
            if rotation:
                state.controls_source = 'memory'
            state.controls_issue = 'Saved preferences are unavailable; current preferences are unchanged.'
