"""Per-chat records (policy, self-tuning, Keeps, pins, host offsets) kept in the plugin's own durable state.

Hermes gives every plugin a profile-scoped JSON store, ``ctx.state`` (``get(key, default)`` and ``set(key, value)``, atomic per key, 10 MB quota).
This module is the only place that uses it. Each chat gets one key, ``chat:<session id>`` (``chat:h:<sha256 of the id>`` when the id does not fit
the key rules), holding ``{"v": 1, "day": <UTC day number>, "policy": ..., "tuner": ..., "edits": ..., "pins": ..., "host": ...}``; each part
is the same small versioned record the plugin always kept, so the readers that validate them are unchanged. Nothing is written to Hermes' session
database.

Two consequences of using ``ctx.state``:

* There is no delete, so a chat's record outlives the chat. A small index key (``chats``: key -> last-used day) lets the store retire old
  ones: a chat not used for ``MAX_AGE_DAYS`` days, or beyond the newest ``MAX_CHATS``, has its key set to ``None`` and leaves the index. The
  sweep runs at most once a day per process, and a chat's day is refreshed when it is read or written (at most one write a day).
* Without ``ctx.state`` (a directory install, the test stub) there is no store: records live in memory only, as the status already says.

Every call is best effort and never raises: a read gives ``None``, a write gives ``False``.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Callable

STATE_VERSION = 1
INDEX_KEY = "chats"
PARTS = ("policy", "tuner", "edits", "pins", "host")
MAX_AGE_DAYS = 90
MAX_CHATS = 400

_KEY_RULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def utc_day(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // 86_400)


def chat_key(session_id: str) -> str | None:
    """The state key for a chat, or ``None`` for an empty id. Plain ``chat:<id>`` when the id is a legal key part; a hash otherwise."""
    if not isinstance(session_id, str) or not session_id:
        return None
    plain = f"chat:{session_id}"
    if _KEY_RULE.fullmatch(plain) and ".." not in plain:
        return plain
    return "chat:h:" + hashlib.sha256(session_id.encode("utf-8", "surrogatepass")).hexdigest()[:40]


def usable(state: Any) -> bool:
    return state is not None and callable(getattr(state, "get", None)) and callable(getattr(state, "set", None))


class ChatStore:
    def __init__(self, state: Any, *, today: Callable[[], int] = utc_day) -> None:
        self._state = state
        self._today = today
        self._lock = threading.RLock()
        self._swept_on: int | None = None

    def __deepcopy__(self, memo):
        return self

    def __copy__(self):
        return self

    # ---- reads and writes ------------------------------------------------------------------------------
    def _load(self, key: str) -> dict[str, Any]:
        value = self._state.get(key, None)
        return value if isinstance(value, dict) and value.get("v") == STATE_VERSION else {}

    def read(self, session_id: str, part: str) -> Any:
        """The saved record for one part of one chat, or ``None``. Refreshes the chat's day (at most one write a day)."""
        key = chat_key(session_id)
        if key is None or part not in PARTS:
            return None
        try:
            with self._lock:
                value = self._load(key)
                if not value:
                    return None
                if value.get("day") != self._today():
                    self._set(key, {**value, "day": self._today()})
                return value.get(part)
        except Exception:
            return None

    def write(self, session_id: str, part: str, record: Any) -> bool:
        """Save one part (``None`` clears it). True only when it reads back identical."""
        key = chat_key(session_id)
        if key is None or part not in PARTS:
            return False
        try:
            with self._lock:
                value = self._load(key)
                day = self._today()
                if value.get(part) == record and value.get("day") == day and (record is not None or part not in value):
                    return True
                updated = {**value, "v": STATE_VERSION, "day": day}
                if record is None:
                    updated.pop(part, None)
                else:
                    updated[part] = record
                self._set(key, updated)
                return self._load(key).get(part) == record
        except Exception:
            return False

    def _set(self, key: str, value: dict[str, Any]) -> None:
        self._state.set(key, value)
        self._touch_index(key, value["day"])

    # ---- retention -------------------------------------------------------------------------------------
    def _index(self) -> dict[str, int]:
        raw = self._state.get(INDEX_KEY, None)
        if not isinstance(raw, dict):
            return {}
        return {k: d for k, d in raw.items() if isinstance(k, str) and isinstance(d, int) and not isinstance(d, bool)}

    def _touch_index(self, key: str, day: int) -> None:
        index = self._index()
        if index.get(key) != day:
            index[key] = day
            self._state.set(INDEX_KEY, index)
        today = self._today()
        if self._swept_on != today:
            self._swept_on = today
            self.sweep()

    def sweep(self) -> list[str]:
        """Retire chats unused for ``MAX_AGE_DAYS`` days or beyond the newest ``MAX_CHATS``. Returns the keys it cleared."""
        today = self._today()
        index = self._index()
        oldest_first = sorted(index.items(), key=lambda item: (item[1], item[0]))
        over = max(0, len(oldest_first) - MAX_CHATS)
        retired = [key for position, (key, day) in enumerate(oldest_first) if position < over or today - day > MAX_AGE_DAYS]
        keep = {key for key in index if key not in set(retired)}
        for key in retired:
            self._state.set(key, None)
        if retired:
            self._state.set(INDEX_KEY, {key: day for key, day in index.items() if key in keep})
        return retired
