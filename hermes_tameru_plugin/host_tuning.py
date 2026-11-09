"""Adaptive values for two Hermes settings, per chat, in memory, within bounds anchored on what the user configured.

``compression.threshold`` (when Hermes summarises) and ``compression.protect_last_n`` (how many recent messages it
protects) are the user's starting point and guard rails. For one chat Tameru may move the *effective* values a little,
because a Hermes summary cannot be undone and the right moment depends on how this chat behaves. It never writes
``config.yaml``; Hermes keeps applying its own floors, caps and ceilings because the engine only changes
``threshold_percent`` and lets Hermes derive the tokens.

What it reads: how fast the prompt grows per reply and how well the cache is reused (numbers only), and how often
the user or the model goes back to an old output (re-reads, Restore, Keep). Never message text.

* **Summary threshold.** As late as is safe for how fast the chat grows: it leaves room for two of the largest recent
  replies. Between ``BELOW`` under and ``ABOVE`` over the configured value, never past ``CEILING`` of the window, and
  never raised while the cache is cold (a bigger prompt would cost more) or soon after Hermes summarised on its own.
* **Protected tail.** Wider when old outputs keep being revisited, back to the configured size when they are not.
  Between half and double the configured count, 6 to 60 messages.

State is two numbers per chat; every step is small, and each visible change carries a plain-language reason.
"""
from __future__ import annotations

from collections import deque
from typing import Any

BELOW = 0.10            # most the threshold may sit under the configured value
ABOVE = 0.15            # most it may sit over it
CEILING = 0.85          # never past this share of the usable window (also Hermes' own trigger cap)
FLOOR = 0.30            # never under this share
STEP = 0.02             # largest move per turn
HOLD = 0.01             # a move smaller than this is not worth making
HEADROOM_REPLIES = 2    # keep room for this many of the largest recent replies
MIN_SAMPLES = 3         # replies of data before the threshold moves
COLD_CACHE_PERCENT = 30
COOLDOWN_TURNS = 5      # no raising after Hermes summarised on its own
SCALE_MIN, SCALE_MAX = 0.5, 2.0
REACH_CAP = 3.0         # revisits counted per step are capped, so a burst of forced re-reads moves the tail once
REACH_STEP = 0.25       # tail grows by a quarter of its configured size per revisit turn
QUIET_RELAX_AFTER = 3
QUIET_SHRINK_AFTER = 12
ANNOUNCE_PERCENT = 0.03
ANNOUNCE_MESSAGES = 3
TAIL_MIN, TAIL_MAX = 6, 60


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class HostTuning:
    """Per-chat adaptive offsets. Pure state plus one ``step``; the engine supplies the numbers and applies the result."""

    def __init__(self) -> None:
        self.offset = 0.0                 # percentage points above (+) or below (-) the configured threshold
        self.scale = 1.0                  # multiple of the configured protected-tail count
        self.last_prompt: int | None = None
        self.growth: deque = deque(maxlen=6)   # positive per-reply prompt growth, in tokens
        self.quiet = 0                    # turns since the last revisit of an old output
        self.cooldown = 0                 # turns left in which the threshold is not raised
        self.reach = 0.0                  # revisits noted since the last step (weighted)
        self.announced_percent: float | None = None
        self.announced_n: int | None = None

    # ---- inputs ---------------------------------------------------------------------------------------
    def note_reach(self, weight: float = 1.0) -> None:
        """The user or the model went back to an old output (a re-read, a Restore, a Keep)."""
        self.reach = min(REACH_CAP, self.reach + max(0.0, float(weight)))

    def note_host_summary(self) -> None:
        """Hermes summarised the chat itself: the threshold was not early enough, so do not push it later for a while."""
        self.cooldown = COOLDOWN_TURNS
        self.offset = min(self.offset, 0.0)

    def reset(self) -> None:
        self.__init__()

    # ---- one turn -------------------------------------------------------------------------------------
    def step(self, *, prompt_tokens: Any, window: int, base_percent: float, base_last_n: int,
             cache_hit_rate: Any = None) -> dict[str, Any]:
        """Update from this turn's numbers. Returns ``{"percent", "last_n", "reasons"}`` (reasons are plain sentences,
        empty when nothing visible changed). Bad or missing numbers leave everything as it was."""
        reasons: list[str] = []
        try:
            prompt = int(prompt_tokens) if type(prompt_tokens) is int and prompt_tokens > 0 else None
            if prompt is not None and self.last_prompt is not None and prompt > self.last_prompt:
                self.growth.append(prompt - self.last_prompt)
            if prompt is not None:
                self.last_prompt = prompt
        except (TypeError, ValueError):
            pass
        if self.cooldown > 0:
            self.cooldown -= 1

        # ---- protected tail
        if self.reach > 0:
            self.scale = min(SCALE_MAX, self.scale + REACH_STEP)
            self.quiet = 0
        else:
            self.quiet += 1
            if self.quiet >= QUIET_SHRINK_AFTER and self._grows_fast(window):
                self.scale = max(0.75, self.scale - 0.05)
            elif self.quiet >= QUIET_RELAX_AFTER and self.scale > 1.0:
                self.scale = max(1.0, self.scale - 0.10)
        self.reach = 0.0
        base_n = max(1, int(base_last_n))
        low_n, high_n = max(TAIL_MIN, base_n // 2), min(TAIL_MAX, base_n * 2)
        last_n = int(_clamp(round(base_n * _clamp(self.scale, SCALE_MIN, SCALE_MAX)), min(low_n, base_n), max(high_n, base_n)))

        # ---- summary threshold
        base = _clamp(float(base_percent), FLOOR, CEILING)
        low, high = max(FLOOR, base - BELOW), min(CEILING, base + ABOVE)
        cold = isinstance(cache_hit_rate, (int, float)) and not isinstance(cache_hit_rate, bool) and cache_hit_rate < COLD_CACHE_PERCENT
        if cold or self.cooldown > 0:
            high = min(high, max(base, low))
        if window > 0 and len(self.growth) >= MIN_SAMPLES:
            peak = max(self.growth)
            target = _clamp((window - HEADROOM_REPLIES * peak) / window, low, high)
            move = _clamp(target - (base + self.offset), -STEP, STEP)
            if abs(move) >= HOLD:
                self.offset = _clamp(self.offset + move, low - base, high - base)
        self.offset = _clamp(self.offset, low - base, high - base)
        percent = _clamp(base + self.offset, low, high)

        # ---- say what changed, in words, only when it is worth saying
        if self.announced_percent is None:
            self.announced_percent = base
        if self.announced_n is None:
            self.announced_n = base_n
        if abs(percent - self.announced_percent) >= ANNOUNCE_PERCENT:
            later = percent > self.announced_percent
            reasons.append(
                f"This chat grows {'slowly' if later else 'quickly'}, so Hermes' summary now comes {'later' if later else 'earlier'} "
                f"({round(self.announced_percent * 100)}% to {round(percent * 100)}% of the window).")
            self.announced_percent = percent
        if abs(last_n - self.announced_n) >= ANNOUNCE_MESSAGES:
            wider = last_n > self.announced_n
            reasons.append(
                f"{'Old outputs keep being revisited' if wider else 'This chat has moved on'}, so "
                f"{'more' if wider else 'fewer'} recent messages are protected ({self.announced_n} to {last_n}).")
            self.announced_n = last_n
        return {"percent": percent, "last_n": last_n, "reasons": reasons}

    def _grows_fast(self, window: int) -> bool:
        return bool(self.growth) and window > 0 and (sum(self.growth) / len(self.growth)) >= 0.04 * window

    # ---- saving ---------------------------------------------------------------------------------------
    def state(self) -> dict[str, float]:
        return {"offset": round(self.offset, 4), "scale": round(self.scale, 3)}

    def load(self, record: Any) -> bool:
        """Restore saved numbers; anything that does not parse, or is out of range, is ignored."""
        try:
            offset, scale = float(record["offset"]), float(record["scale"])
        except (TypeError, ValueError, KeyError):
            return False
        if offset != offset or scale != scale:
            return False
        self.offset = _clamp(offset, -BELOW, ABOVE)
        self.scale = _clamp(scale, SCALE_MIN, SCALE_MAX)
        return True
