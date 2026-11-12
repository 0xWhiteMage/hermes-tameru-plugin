"""A profile-wide memory of what self-tuning has learned, so a new chat does not start from zero.

Each chat's ``Tuner`` keeps five multiplicative offsets around 1.0 (see ``autotune``). Whenever a chat adjusts or relaxes
them, this module moves a stored *prior* a quarter of the way toward them (an exponential moving average across chats and
over time). A new chat starts from that prior, damped to 60% of the way from neutral, so one odd chat cannot drag every
later chat far, and a prior that is no longer reinforced relaxes back toward 1.0 as calm chats pull it there.

It is online learning with small, bounded, explainable steps, not a trained model. State is five numbers in one small JSON
file under ``$HERMES_HOME/tameru/``. Every read and write is best effort: a missing, unreadable or corrupt file means
"nothing learned yet" and never an error.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

VERSION = 1
DAILY_CAP = 0.12               # most one stored offset may move per UTC day, across every chat
RATE = 0.25                    # how far one observation moves the stored prior
RELAX_RATE = 0.06              # how far a calm stretch eases the prior back toward neutral
SEED_SHARE = 0.60              # how much of the stored prior a new chat starts from
BOUNDS = (0.70, 1.30)          # the prior is held tighter than a single chat's offsets (0.55 to 1.45)
_MIN_CHANGE = 0.0005           # smaller moves are not worth a disk write
_SNAP = 0.01                   # a value this close to neutral is stored as exactly neutral, so a prior can fully relax
_COUNT = 5


def _path() -> Path:
    base = os.environ.get("TAMERU_LEARNED_DIR", "").strip()
    if not base:
        from .home import hermes_home

        base = str(hermes_home() / "tameru")
    return Path(base) / "learned.json"


def _clamp(value: float) -> float:
    return max(BOUNDS[0], min(BOUNDS[1], value))


def load() -> list[float] | None:
    """The stored prior, or None when nothing usable has been learned."""
    try:
        record = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("version") != VERSION:
            return None
        values = record.get("offsets")
        if not isinstance(values, list) or len(values) != _COUNT:
            return None
        parsed = [float(v) for v in values]
        if any(v != v or v in (float("inf"), float("-inf")) for v in parsed):
            return None
        return [_clamp(v) for v in parsed]
    except (OSError, ValueError, TypeError):
        return None


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _moved_today() -> list[float]:
    """How far each stored offset has already moved today (UTC). Zeros on a new day or an unreadable file."""
    try:
        record = json.loads(_path().read_text(encoding="utf-8"))
        moved = record.get("moved")
        if record.get("day") == _today() and isinstance(moved, list) and len(moved) == _COUNT:
            parsed = [abs(float(v)) for v in moved]
            if all(v == v and v != float("inf") for v in parsed):
                return parsed
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return [0.0] * _COUNT


def record(offsets) -> bool:
    """Move the prior toward ``offsets`` (one chat's current offsets). True when the file was updated.

    The prior can move at most ``DAILY_CAP`` per offset per UTC day, however many chats report, so one hostile or
    runaway chat cannot saturate it in a few adjustments."""
    try:
        observed = [float(v) for v in offsets]
        if len(observed) != _COUNT or any(v != v for v in observed):
            return False
        prior = load() or [1.0] * _COUNT
        moved = _moved_today()
        updated = []
        for index, (p, o) in enumerate(zip(prior, observed)):
            step = _clamp(p + RATE * (o - p)) - p
            room = max(0.0, DAILY_CAP - moved[index])
            updated.append(_clamp(p + max(-room, min(room, step))))
        updated = [1.0 if abs(u - 1.0) < _SNAP else u for u in updated]
        if max(abs(u - p) for u, p in zip(updated, prior)) < _MIN_CHANGE:
            return False
        moved = [m + abs(u - p) for m, u, p in zip(moved, updated, prior)]
        target = _path()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=str(target.parent), prefix=".learned-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": VERSION, "offsets": [round(u, 6) for u in updated],
                           "day": _today(), "moved": [round(m, 6) for m in moved]}, handle)
            os.replace(temp, target)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        return True
    except (OSError, ValueError, TypeError):
        return False


def relax() -> bool:
    """A chat had a calm stretch: ease the prior toward neutral, whatever that chat's own offsets are."""
    prior = load()
    if prior is None:
        return False
    return record([p + (1.0 - p) * (RELAX_RATE / RATE) for p in prior])


def seed() -> list[float] | None:
    """Offsets a brand-new chat should start from: the prior, damped toward neutral. None before anything is learned."""
    prior = load()
    if prior is None:
        return None
    return [1.0 + (p - 1.0) * SEED_SHARE for p in prior]
