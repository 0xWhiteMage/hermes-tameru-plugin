"""What the provider reported for the last replies: tokens, cache use and call timing.

Methods of ``TameruContextEngine`` kept in their own module so the engine class stays readable; they use the
engine's ``self`` state and are mixed in verbatim.
"""
from __future__ import annotations

from .quiet import swallowed


class UsageMixin:
    def usage_snapshot(self) -> dict:
        """What the provider reported for the last reply. ``cache_hit_rate`` is a percentage of the prompt that was
        read from cache, or None when this provider has never reported cache use (an honest "not reported")."""
        rows = list(self._usage_ring)
        if not rows:
            return {"measured": False, "samples": 0, "prompt": None, "cache_read": None, "cache_write": None, "cache_hit_rate": None}
        prompt, read, write, fresh = rows[-1]
        reported = any((cr or 0) > 0 or (cw or 0) > 0 for _p, cr, cw, _i in rows)
        rate = round(100 * min(read or 0, prompt) / prompt) if reported and prompt else None
        timing = self._timing_ring[-1] if self._timing_ring else (None, None)
        return {"measured": True, "samples": len(rows), "prompt": prompt, "cache_read": read, "cache_write": write,
                "cache_hit_rate": rate, "fresh_input": fresh, "api_ms": timing[0], "first_chunk_ms": timing[1]}

    def record_response_timing(self, api_duration=None, started_at=None, first_chunk_at=None) -> None:
        """How long the last provider call took, and (only when Hermes streamed it) when the first chunk arrived.
        Numbers only. The first chunk is not the first token: it may be reasoning or a tool delta, so it is shown as is."""
        try:
            def ms(seconds):
                return round(seconds * 1000) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and 0 <= seconds < 86400 else None
            first = None
            if isinstance(first_chunk_at, (int, float)) and isinstance(started_at, (int, float)):
                first = ms(first_chunk_at - started_at)
            self._timing_ring.append((ms(api_duration), first))
        except Exception:
            swallowed(self, "record_response_timing")
