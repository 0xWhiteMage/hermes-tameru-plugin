"""Content-free counters for one engine, and an optional JSONL log.

Callers pass only counts, tool names, rungs and hashes, never message text. ``count`` keeps a total
per event plus a per-value breakdown of each label (``count("render", rung="extract",
tool="terminal")`` raises the ``render`` total and the ``rung`` / ``tool`` breakdowns). Label values
are cut to ``MAX_LABEL_CHARS`` and each label keeps at most ``MAX_LABEL_VALUES`` distinct values
(the rest count under ``"_other"``), so the counters stay small whatever flows through them.

``snapshot()`` feeds ``get_status()["tameru"]["telemetry"]``; ``emit`` appends one JSON line to
``log_path`` when one is configured. Everything is thread-safe, and ``emit`` never raises.

Import-safe: stdlib only, nothing happens at import time.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

MAX_LABEL_CHARS = 64
MAX_LABEL_VALUES = 64
OVERFLOW_LABEL = "_other"


class Telemetry:
    """Thread-safe counters; copying one gives a fresh zeroed instance with the same ``log_path``."""

    def __init__(self, log_path: str = "") -> None:
        self._log_path = log_path
        self._lock = threading.Lock()          # the counters
        self._log_lock = threading.Lock()      # the log file: slow disk I/O never blocks a count
        self._clear()

    def _clear(self) -> None:
        self._events: dict[str, int] = {}
        self._labels: dict[str, dict[str, dict[str, int]]] = {}
        self._chars_before = 0
        self._chars_after = 0
        self._seconds = 0.0
        self._timed = 0

    def count(self, event: str, n: int = 1, **labels: Any) -> None:
        """Add ``n`` to ``event`` and to the breakdown of each label value."""
        with self._lock:
            self._events[event] = self._events.get(event, 0) + n
            by_label = self._labels.setdefault(event, {})
            for label, value in labels.items():
                values = by_label.setdefault(label, {})
                key = str(value)[:MAX_LABEL_CHARS]
                if key not in values and len(values) >= MAX_LABEL_VALUES:
                    key = OVERFLOW_LABEL
                values[key] = values.get(key, 0) + n

    def add_chars(self, before: int, after: int) -> None:
        """Account one rewrite: ``before`` chars of original became ``after`` chars."""
        with self._lock:
            self._chars_before += before
            self._chars_after += after

    def time(self, seconds: float) -> None:
        """Account one timed call."""
        with self._lock:
            self._seconds += seconds
            self._timed += 1

    def snapshot(self) -> dict:
        """JSON-serializable view, keys sorted: ``events``, ``chars``, ``time``."""
        with self._lock:
            events = {
                event: {
                    "count": total,
                    "labels": {
                        label: dict(sorted(values.items()))
                        for label, values in sorted(self._labels.get(event, {}).items())
                    },
                }
                for event, total in sorted(self._events.items())
            }
            return {
                "events": events,
                "chars": {
                    "before": self._chars_before,
                    "after": self._chars_after,
                    "saved": self._chars_before - self._chars_after,
                },
                "time": {"seconds": round(self._seconds, 6), "calls": self._timed},
            }

    def reset(self) -> None:
        with self._lock:
            self._clear()

    def emit(self, record: dict) -> None:
        """Append ``record`` as one JSON line to ``log_path``; a no-op without one, never raises."""
        if not self._log_path:
            return
        try:
            line = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
            path = Path(self._log_path).expanduser()
            with self._log_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as log:
                    log.write(line + "\n")
        except Exception:  # a log problem must never disturb a prune pass
            pass

    def __copy__(self) -> Telemetry:
        return Telemetry(self._log_path)

    def __deepcopy__(self, memo: dict) -> Telemetry:
        return Telemetry(self._log_path)
