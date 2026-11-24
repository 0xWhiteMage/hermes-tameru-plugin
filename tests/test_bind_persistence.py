"""``bind_session_state`` takes Hermes' ``persistence=`` handle and forwards it only when given.

Hermes from db4d50b9e7 (v0.23.3) passes the store's writer handle to a context engine's
``bind_session_state``: guarded at agent init, unguarded from ``ContextCompressor.on_session_start``.
Without the keyword, the prune's store gate (``prune:store_cannot_persist``) and the compaction
cooldown read (``cooldown_state_unreadable``) fail on every session. An older Hermes never passes
it, so its base method must then see the same two-argument call as before.
"""
from __future__ import annotations

import inspect

import pytest

from engine_support import make_engine
from hermes_tameru_plugin.engine import TameruContextEngine


def _base_owner():
    """The first class after the engine that defines ``bind_session_state`` (Hermes' ``ContextCompressor``)."""
    return next(c for c in TameruContextEngine.__mro__[1:] if "bind_session_state" in vars(c))


def _record_base_calls(monkeypatch):
    """Record the keywords the engine passes to the base ``bind_session_state``, then run the base."""
    calls = []
    owner = _base_owner()
    original = owner.bind_session_state
    accepted = set(inspect.signature(original).parameters)

    def recording(self, session_db=None, session_id="", **kwargs):
        calls.append(kwargs)
        return original(self, session_db, session_id, **{k: v for k, v in kwargs.items() if k in accepted})

    monkeypatch.setattr(owner, "bind_session_state", recording)
    return calls


def test_bind_accepts_the_persistence_keyword():
    parameter = inspect.signature(TameruContextEngine.bind_session_state).parameters["persistence"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


def test_without_persistence_the_base_sees_two_arguments(monkeypatch):
    engine = make_engine()
    calls = _record_base_calls(monkeypatch)
    engine.bind_session_state(session_db=None, session_id="s1")
    assert calls == [{}]
    assert engine._session_id == "s1"


def test_a_given_persistence_reaches_the_base(monkeypatch):
    engine = make_engine()
    calls = _record_base_calls(monkeypatch)
    handle = object()
    engine.bind_session_state(session_db=None, session_id="s1", persistence=handle)
    assert calls == [{"persistence": handle}]
    assert engine._session_id == "s1"


def test_the_early_bind_forwards_persistence_too(monkeypatch):
    # Parent construction may bind before ``_activity`` exists; that path returns the base call directly.
    engine = make_engine()
    calls = _record_base_calls(monkeypatch)
    monkeypatch.setattr(engine, "_activity", None)
    handle = object()
    engine.bind_session_state(session_db=None, session_id="s2", persistence=handle)
    assert calls == [{"persistence": handle}]


@pytest.mark.real_hermes
def test_on_session_start_binds_the_handle_on_a_hermes_that_passes_it():
    if "persistence" not in inspect.signature(_base_owner().bind_session_state).parameters:
        pytest.skip("this Hermes predates the persistence= handle (db4d50b9e7)")
    engine = make_engine()
    handle = object()
    engine.on_session_start("s1", session_db=None, persistence=handle)
    assert engine._session_id == "s1"
    assert getattr(engine, "_persistence", None) is handle
