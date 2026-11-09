from __future__ import annotations
from dataclasses import asdict, replace
from .hermes_compat import session_db_usable
from .policy import Controls

POLICY_KEY = 'tameru_policy_v1'
_FIELDS = frozenset(('mode', 'preference', 'activation', 'recent'))

class PolicyError(ValueError):
    """A safe user-facing policy-control error; never include database payloads."""


def _binding(engine):
    return (engine._policy, getattr(engine, '_session_db', None),
            getattr(engine, '_session_id', '') or '', engine._activity.lifecycle_id)


def _current(engine, owner):
    state, db, sid, life = owner
    return (engine._policy is state and getattr(engine, '_session_db', None) is db
            and (getattr(engine, '_session_id', '') or '') == sid
            and engine._activity.lifecycle_id == life)


def _metadata_methods(db):
    if not session_db_usable(db):
        return None
    methods = tuple(getattr(db, name, None) for name in
                    ('get_session_model_config_value', 'patch_session_model_config', 'get_session'))
    return methods if all(callable(method) for method in methods) else None


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


def _persist(db, sid, record):
    methods = _metadata_methods(db)
    if methods is None:
        return False
    read, patch, session = methods
    if session(sid) is None:
        raise PolicyError('This chat is not available in session storage. Start a turn and try again.')
    if read(sid, POLICY_KEY) != record:
        patch(sid, {POLICY_KEY: record})
    if read(sid, POLICY_KEY) != record or session(sid) is None:
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
        methods = _metadata_methods(db)
        if methods is None:
            return
        record = methods[0](sid, POLICY_KEY)
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
