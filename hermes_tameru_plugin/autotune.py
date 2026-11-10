"""Continuous self-tuning of the cleanup policy from live session signals.

A ``Tuner`` owns five scalars (``Tuning``) that ``policy.plans`` reads instead of the fixed per-preference
table. It starts exactly at the table's "balanced" row, so a session with no signals plans as before.
Signals nudge multiplicative offsets by small steps, each inside hard bounds. A signal repeating within
``_COOLDOWN`` ticks is ignored (no thrash), and calm stretches pull the offsets back toward neutral (no
ratchet). One user-facing bias (-1 save space ... +1 preserve detail) moves the starting point.

State is scalars and short reason strings, never transcript text.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

# (activation, detail, recent, minimum_reclaim_share, regrowth) of the policy table's three rows.
_SAVE = (0.70, 0.75, 0.80, 0.20, 0.10)
_BALANCED = (0.60, 0.50, 1.00, 0.35, 0.15)
_DETAIL = (1.00, 2.00, 1.50, 0.50, 0.20)

# Hard bounds on the effective values: Tuner can never leave them, whatever the signals say.
_BOUNDS = ((0.35, 1.00), (0.40, 2.50), (0.60, 2.00), (0.08, 0.60), (0.06, 0.30))
_NAMES = ("activation", "detail", "recent", "minimum_reclaim_share", "regrowth")
# Offsets are multiplicative around 1.0 and clamped on their own too, so decay always returns to neutral.
_OFFSET_BOUNDS = (0.55, 1.45)
_COOLDOWN = 3                 # ticks before the same signal may act again
_DECAY = 0.12                 # share of each offset's distance from 1.0 returned per calm stretch
_CALM_TICKS = 6               # ticks without a signal that count as calm
_HISTORY = 24
_REASON_CHARS = 120


@dataclass(frozen=True)
class Tuning:
    activation: float = _BALANCED[0]
    detail: float = _BALANCED[1]
    recent: float = _BALANCED[2]
    minimum_reclaim_share: float = _BALANCED[3]
    regrowth: float = _BALANCED[4]


@dataclass(frozen=True)
class Adjustment:
    tick: int
    signal: str
    changed: tuple[str, ...]
    reason: str


# signal -> (offset multipliers per tuned value, reason). Values >1 raise, <1 lower.
# Order matches _NAMES. Lower activation = clean up earlier; higher detail = keep more.
_RESPONSES: dict[str, tuple[tuple[float, float, float, float, float], str]] = {
    "host_summarized": ((0.90, 1.00, 1.00, 0.90, 1.00),
                        "Hermes had to summarize on its own, so cleanup starts earlier"),
    "reclaim_declined": ((0.95, 1.00, 1.00, 0.85, 1.00),
                         "Repeated checks found too little to reclaim, so the bar is lowered"),
    "original_reread": ((1.04, 1.12, 1.08, 1.00, 1.00),
                        "Shortened outputs were re-read, so more detail is kept"),
    "fast_regrowth": ((1.00, 0.92, 1.00, 1.00, 1.10),
                      "Context refilled quickly after cleanup, so cleanup is deeper"),
}
SIGNALS = tuple(_RESPONSES)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def _base(bias: float) -> tuple[float, ...]:
    """The starting row for ``bias``: balanced at 0, the save-space row at -1, the preserve-detail row at +1."""
    end = _DETAIL if bias > 0 else _SAVE
    share = abs(bias)
    return tuple(b + (e - b) * share for b, e in zip(_BALANCED, end))


class Tuner:
    def __init__(self, bias: float = 0.0) -> None:
        self._bias = 0.0
        self._offsets = [1.0] * 5
        self._tick = 0
        self._last_applied: dict[str, int] = {}
        self._last_signal_tick = 0
        self.seeded = False                  # started from what earlier chats learned (see ``learned``)
        self.history: deque[Adjustment] = deque(maxlen=_HISTORY)
        self.set_bias(bias)

    # ---- reading -------------------------------------------------------------
    @property
    def bias(self) -> float:
        return self._bias

    def offsets(self) -> tuple[float, ...]:
        return tuple(self._offsets)

    def pristine(self) -> bool:
        """Nothing has moved this tuner yet: no signal, no seed, no restored state."""
        return not self.history and not self.seeded and all(o == 1.0 for o in self._offsets)

    def seed(self, offsets) -> bool:
        """Start from ``offsets`` (the damped prior of earlier chats). Only a pristine tuner accepts a seed."""
        try:
            values = [float(o) for o in offsets]
        except (TypeError, ValueError):
            return False
        if len(values) != 5 or any(v != v for v in values) or not self.pristine():
            return False
        self._offsets = [_clamp(v, _OFFSET_BOUNDS) for v in values]
        self.seeded = True
        return True

    def reset(self) -> None:
        """Back to a neutral tuner: no offsets, no history, no seed, bias 0 (the user's ``/tameru reset``)."""
        self._bias = 0.0
        self._offsets = [1.0] * 5
        self._tick = 0
        self._last_applied = {}
        self._last_signal_tick = 0
        self.seeded = False
        self.history.clear()

    def tuning(self) -> Tuning:
        base = _base(self._bias)
        values = [_clamp(b * o, bounds) for b, o, bounds in zip(base, self._offsets, _BOUNDS)]
        return Tuning(*values)

    def mode(self) -> str:
        """The user's bias as one word: ``save``, ``balanced`` or ``detail``."""
        return "save" if self._bias < -0.33 else "detail" if self._bias > 0.33 else "balanced"

    def posture(self) -> str:
        """A short plain label for what the tuner is currently leaning toward."""
        values = self.tuning()
        lean = (_BALANCED[0] - values.activation) / _BALANCED[0] - (values.detail - _BALANCED[1]) / _BALANCED[1]
        if lean > 0.12:
            return "cleaning up earlier"
        if lean < -0.12:
            return "keeping more detail"
        return "balanced"

    # ---- bias --------------------------------------------------------------
    def set_bias(self, bias: float) -> None:
        if not isinstance(bias, (int, float)) or isinstance(bias, bool) or bias != bias:
            raise ValueError("bias must be a number between -1 and 1")
        self._bias = max(-1.0, min(1.0, float(bias)))

    # ---- signals ----------------------------------------------------------
    def tick(self) -> bool:
        """One policy check passed. Calm stretches relax every offset toward neutral. True when they just did."""
        self._tick += 1
        if self._tick - self._last_signal_tick >= _CALM_TICKS and any(o != 1.0 for o in self._offsets):
            relaxed = [1.0 + (o - 1.0) * (1.0 - _DECAY) for o in self._offsets]
            self._offsets = [1.0 if abs(o - 1.0) < 0.005 else o for o in relaxed]   # snap: decay must end at neutral
            self._last_signal_tick = self._tick                  # pace the decay as well
            return True
        return False

    def signal(self, name: str, count: int = 1) -> Adjustment | None:
        """Apply the response to ``name`` unless it acted within the cooldown. ``count`` repeats it (bounded)."""
        response = _RESPONSES.get(name)
        if response is None:
            raise ValueError(f"unknown signal {name!r}")
        last = self._last_applied.get(name)
        if last is not None and self._tick - last < _COOLDOWN:
            return None
        factors, reason = response
        before = self.tuning()
        steps = max(1, min(int(count), 3))
        for i, factor in enumerate(factors):
            if factor != 1.0:
                self._offsets[i] = _clamp(self._offsets[i] * (factor ** steps), _OFFSET_BOUNDS)
        self._last_applied[name] = self._tick
        self._last_signal_tick = self._tick
        after = self.tuning()
        changed = tuple(n for n, a, b in zip(_NAMES, astuple(before), astuple(after)) if abs(a - b) > 1e-9)
        if not changed:
            return None                                          # already at a bound: nothing to report
        adjustment = Adjustment(self._tick, name, changed, reason[:_REASON_CHARS])
        self.history.append(adjustment)
        return adjustment

    # ---- persistence ---------------------------------------------------------
    def state(self) -> dict[str, Any]:
        return {"version": 1, "bias": self._bias, "offsets": [round(o, 6) for o in self._offsets]}

    def load(self, record: Any) -> bool:
        """Restore bias and offsets from ``state()`` output; anything malformed leaves the tuner untouched."""
        try:
            if not isinstance(record, dict) or record.get("version") != 1:
                return False
            offsets = record["offsets"]
            if not isinstance(offsets, list) or len(offsets) != 5:
                return False
            parsed = [float(o) for o in offsets]
            if any(o != o for o in parsed):
                return False
            bias = record["bias"]
            self.set_bias(bias)
        except (KeyError, TypeError, ValueError):
            return False
        self._offsets = [_clamp(o, _OFFSET_BOUNDS) for o in parsed]
        return True


def astuple(tuning: Tuning) -> tuple[float, ...]:
    return (tuning.activation, tuning.detail, tuning.recent, tuning.minimum_reclaim_share, tuning.regrowth)
