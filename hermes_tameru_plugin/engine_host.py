"""Adaptive host settings: the per-chat summary threshold and protected tail.

Methods of ``TameruContextEngine`` kept in their own module so the engine class stays readable; they use the
engine's ``self`` state and are mixed in verbatim.
"""
from __future__ import annotations

from .quiet import swallowed


class HostSettingsMixin:
    def _host_anchor(self) -> tuple[float, int, int]:
        """(configured threshold percent after Hermes' own floor, configured protected-tail count, usable window)."""
        raw = getattr(self, "_base_threshold_percent", None) or getattr(self, "_config_threshold_percent", None) or 0.5
        window = int(getattr(self, "context_length", 0) or 0)
        floor = getattr(self, "_effective_threshold_percent", None)
        base = float(floor(window, raw)) if callable(floor) and window else float(raw)
        usable = getattr(self, "_effective_input_window", None)
        # Hermes multiplies threshold_percent by this effective input window (context less the reply reserve), not by
        # the whole context, so it is the size every percent in HostTuning is a share of.
        room = int(usable(window, getattr(self, "max_tokens", None))) if callable(usable) and window else window
        if self._host_anchor_n is None:
            self._host_anchor_n = int(self.protect_last_n)
        return base, int(self._host_anchor_n), room

    def _host_enabled(self) -> bool:
        return (bool(self._settings.adaptive_host) and bool(self._settings.enabled)
                and self._settings.policy_mode == "adaptive" and not self._policy.paused)

    def _apply_host(self, percent: float, last_n: int) -> None:
        """Give Hermes the new values. Only ``threshold_percent`` is set: Hermes derives the tokens itself, so its
        small-window floor, the configured cap and the summariser ceiling all still apply."""
        self.protect_last_n = last_n
        window = int(getattr(self, "context_length", 0) or 0)
        floor = getattr(self, "_effective_threshold_percent", None)
        if callable(floor) and window:
            percent = float(floor(window, percent))
        if abs(float(self.threshold_percent) - percent) > 1e-9:
            self.threshold_percent = percent
            if isinstance(getattr(type(self), "threshold_tokens", None), property):
                self._threshold_tokens = None            # Hermes recomputes with its own rules
                self._tail_token_budget = None
            else:
                self.threshold_tokens = int(window * percent)

    def adapt_host_settings(self) -> None:
        """Once per turn: move the effective summary threshold and protected tail a small, bounded step. Never raises."""
        try:
            with self._activity.lifecycle_lock:
                base, base_n, room = self._host_anchor()
                if not self._host_enabled():
                    if self._host_saved is not None or self._host.offset or self._host.scale != 1.0:
                        self._host.reset()
                        self._apply_host(base, base_n)    # back to exactly what the user configured
                        self._host_saved = None
                    return
                usage = self.usage_snapshot()
                outcome = self._host.step(
                    prompt_tokens=getattr(self, "last_real_prompt_tokens", None), window=room, base_percent=base,
                    base_last_n=base_n, cache_hit_rate=usage.get("cache_hit_rate"))
                self._apply_host(outcome["percent"], outcome["last_n"])
                for reason in outcome["reasons"]:
                    self._event("adjust", signal="host_settings", changed=[], reason=reason)
                state = self._host.state()
                if state != self._host_saved:
                    from .session_host import save
                    save(self)
                    self._host_saved = state
        except Exception:
            swallowed(self, "adapt_host_settings")                                             # tuning is a convenience, never a reason to fail a turn

    def host_snapshot(self) -> dict:
        """What Hermes is using now next to what the user configured, for the panel and ``/tameru``."""
        try:
            base, base_n, _room = self._host_anchor()
            now = float(self.threshold_percent)
            return {"enabled": self._host_enabled(), "base_percent": round(base * 100), "threshold_percent": round(now * 100),
                    "base_last_n": base_n, "protect_last_n": int(self.protect_last_n),
                    "adapted": abs(now - base) >= 0.005 or int(self.protect_last_n) != base_n}
        except Exception:
            return {"enabled": False, "adapted": False}
