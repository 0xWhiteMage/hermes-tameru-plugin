"""A small, numbers-only record of what Tameru's cleanups freed, day by day (``stats_ledger: on``, off by default).

What is stored: per UTC day, how many cleanups ran and how many tokens they removed. Nothing else: no chat ids, no text, no
tool names, no paths. It lives in ``$HERMES_HOME/tameru/stats-ledger.json``, is kept for 90 days, and is cleared with
``/tameru reset stats confirm``.

It is only ever *shown* (``/tameru stats``). No policy or tuning code reads it, so it cannot change what Tameru does.
Writes are serialised by a lock file and replaced atomically; every failure is swallowed (a ledger problem never reaches a
chat).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

VERSION = 1
KEEP_DAYS = 90
_LOCK_WAIT = 0.5
_STALE_LOCK = 10.0


def _dir() -> Path:
    base = os.environ.get("TAMERU_STATS_DIR", "").strip()
    if base:
        return Path(base)
    home = os.environ.get("HERMES_HOME", "").strip() or "~/.hermes"
    return Path(home).expanduser() / "tameru"


def _path() -> Path:
    return _dir() / "stats-ledger.json"


def _day(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


def _read() -> dict:
    try:
        record = json.loads(_path().read_text(encoding="utf-8"))
        days = record.get("days") if isinstance(record, dict) and record.get("version") == VERSION else None
        if isinstance(days, dict):
            return {d: v for d, v in days.items() if isinstance(d, str) and isinstance(v, dict)}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {}


class _Lock:
    def __enter__(self):
        self.path = _dir() / "stats-ledger.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _LOCK_WAIT
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > _STALE_LOCK:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("ledger busy")
                time.sleep(0.02)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def record_cleanup(removed_tokens: int, now: float | None = None) -> bool:
    """Add one cleanup that removed ``removed_tokens`` to today's totals. True when written."""
    if type(removed_tokens) is not int or removed_tokens <= 0:
        return False
    try:
        with _Lock():
            days = _read()
            cutoff = _day((time.time() if now is None else now) - KEEP_DAYS * 86400)
            days = {d: v for d, v in days.items() if d >= cutoff}
            today = days.setdefault(_day(now), {"cleanups": 0, "tokens": 0})
            today["cleanups"] = int(today.get("cleanups", 0)) + 1
            today["tokens"] = int(today.get("tokens", 0)) + removed_tokens
            fd, tmp = tempfile.mkstemp(dir=str(_dir()), prefix="stats-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": VERSION, "days": days}, handle, sort_keys=True)
            os.replace(tmp, _path())
        return True
    except Exception:
        return False


def summary(days: int = 7, now: float | None = None) -> dict | None:
    """Totals over the last ``days`` UTC days (today included), or None when nothing is recorded."""
    data = _read()
    start = _day((time.time() if now is None else now) - (days - 1) * 86400)
    picked = {d: v for d, v in data.items() if d >= start}
    cleanups = sum(int(v.get("cleanups", 0)) for v in picked.values() if isinstance(v.get("cleanups", 0), int))
    tokens = sum(int(v.get("tokens", 0)) for v in picked.values() if isinstance(v.get("tokens", 0), int))
    return {"days": days, "active_days": len(picked), "cleanups": cleanups, "tokens": tokens} if picked else None


def purge() -> bool:
    """Delete the ledger. True when a file was removed."""
    try:
        _path().unlink()
        return True
    except OSError:
        return False
