"""One place to record an error that is deliberately not raised.

Bookkeeping around a chat turn (edit state, tuning, usage numbers) must never fail the turn, so those callers swallow
their errors. Swallowing must not make the failure invisible: this counts it, by name, in the telemetry (a number and a
place, never the text), which ``/tameru`` and the log can show.
"""
from __future__ import annotations


def swallowed(engine, where: str) -> None:
    """Count an error that was caught and ignored at ``where``. Never raises."""
    try:
        engine._telemetry.count("error", where=where)
    except Exception:
        pass
