"""Recovery of pruned tool output: the original store, the ``tameru_expand`` tool, the archive lookup.

Every row Tameru renders names its original by ``ref=xxxxxxxx`` (``render.make_ref``). Two places
can hand the original back:

* ``OriginalStore``: a per-agent, locked LRU of the originals this process pruned. Probable
  credentials are never kept (``contains_secret``).
* the Hermes session archive: compaction archives the original rows (``compacted=1``), so
  ``session_lookup_factory`` finds a ref again after gateway eviction, a restart or ``--resume``.

``expand`` is the body of the ``tameru_expand`` tool: exact original lines by range or regex, as
JSON, never raising. A miss says how to search the session instead.

Import-safe: stdlib and the vendored engine only, nothing happens at import time. ``render`` is
imported when a lookup first runs, so a plugin dir without it still loads.
"""
from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .tameru.compress_context import contains_secret

EXPAND_TOOL_NAME = "tameru_expand"
DEFAULT_MAX_CHARS = 12_000
MAX_MAX_CHARS = 50_000
MAX_GREP_CHARS = 200
MAX_SCAN_ROWS = 20_000    # newest session rows a lookup considers

EXPAND_SCHEMA: dict[str, Any] = {
    "name": EXPAND_TOOL_NAME,
    "description": (
        "Recover the exact original text of a tool output that was pruned to save context. "
        "A pruned tool result starts with a one-line summary ending in "
        "[tameru:... ref=xxxxxxxx]; pass those 8 hex characters as ref. Returns the original "
        "lines (1-based, inclusive start_line..end_line), or only the lines matching a "
        "case-insensitive regex given as grep (matched_lines lists their line numbers). Output is "
        "capped at max_chars; when truncated is true, continue from the line after returned_lines. "
        "For a ref that is not found, search the session with session_search(role_filter='tool')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "The 8 hex characters after ref= in the pruned result's header.",
            },
            "start_line": {
                "type": "integer",
                "description": "First line to return, 1-based (default 1).",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to return, inclusive (default: the last line).",
            },
            "grep": {
                "type": "string",
                "description": (
                    f"Case-insensitive regex (at most {MAX_GREP_CHARS} chars): return only the "
                    "matching lines within the range."
                ),
            },
            "max_chars": {
                "type": "integer",
                "description": (
                    f"Cap on returned characters (default {DEFAULT_MAX_CHARS:,}, "
                    f"maximum {MAX_MAX_CHARS:,})."
                ),
            },
        },
        "required": ["ref"],
    },
}

_REF_RE = re.compile(r"[0-9a-f]{8}")
_TERMINATOR_RE = re.compile(r"(\r\n|\r|\n)")   # the same line breaks ``render`` counts lines by
_MISS_HINT = "session_search(query=..., role_filter='tool', session_id='{session_id}')"


# ---- the store -------------------------------------------------------------------------------
@dataclass(frozen=True)
class StoredOriginal:
    """A kept tool result: its text, the tool that produced it and its envelope fields."""

    text: str
    tool: str = ""
    meta: dict = field(default_factory=dict)


class OriginalStore:
    """Thread-safe LRU of original tool results, bounded by entry count and total characters.

    ``copy.copy`` / ``copy.deepcopy`` give a fresh empty store with the same caps: a cloned agent
    must not share (or try to copy the lock of) its parent's originals.
    """

    def __init__(self, max_entries: int, max_chars: int) -> None:
        self._max_entries = max_entries
        self._max_chars = max_chars
        self._entries: OrderedDict[str, StoredOriginal] = OrderedDict()
        self._chars = 0
        self._lock = threading.RLock()

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def max_chars(self) -> int:
        return self._max_chars

    @property
    def total_chars(self) -> int:
        with self._lock:
            return self._chars

    def put(self, ref: str, original: str, *, tool: str = "", meta: dict | None = None) -> bool:
        """Keep ``original`` under ``ref``, evicting the least recently used entries to fit.

        False (nothing stored, nothing evicted) when ``original`` holds a probable credential, is
        larger than ``max_chars``, or is not text.
        """
        if (
            not isinstance(original, str) or self._max_entries < 1
            or len(original) > self._max_chars or contains_secret(original)
        ):
            return False
        with self._lock:
            old = self._entries.pop(ref, None)
            if old is not None:
                self._chars -= len(old.text)
            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._chars + len(original) > self._max_chars
            ):
                self._chars -= len(self._entries.popitem(last=False)[1].text)
            self._entries[ref] = StoredOriginal(original, tool, dict(meta or {}))
            self._chars += len(original)
        return True

    def entry(self, ref: str) -> StoredOriginal | None:
        """The entry for ``ref`` (now the most recently used), else None."""
        with self._lock:
            found = self._entries.get(ref)
            if found is not None:
                self._entries.move_to_end(ref)
            return found

    def get(self, ref: str) -> str | None:
        """The original text for ``ref``, else None."""
        found = self.entry(ref)
        return None if found is None else found.text

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._chars = 0

    def __contains__(self, ref: object) -> bool:
        with self._lock:
            return ref in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __copy__(self) -> OriginalStore:
        return OriginalStore(self._max_entries, self._max_chars)

    def __deepcopy__(self, memo: dict) -> OriginalStore:
        return OriginalStore(self._max_entries, self._max_chars)


# ---- the tool --------------------------------------------------------------------------------
def _error(message: str, **extra: Any) -> str:
    return json.dumps({"error": message, **extra}, ensure_ascii=False)


def _positive_int(args: dict, key: str) -> int | None:
    """``args[key]`` as an int >= 1 (None when absent); ValueError for anything else."""
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{key} must be an integer >= 1")
    return value


def expand(
    store: OriginalStore,
    args: Any,
    *,
    session_lookup: Callable[[str], str | None] | None = None,
    session_id: str = "",
) -> str:
    """The ``tameru_expand`` tool: exact original lines for ``args["ref"]``, as a JSON string.

    Success: ``{"ref", "total_lines", "returned_lines": [first, last], "content", "truncated"}``,
    plus ``matched_lines`` (the line numbers of ``content``'s lines) when ``grep`` is given. Lines
    are numbered like the ``kept K/N lines`` of the row header and ``content`` is the original
    text, line breaks included. Output stops at ``max_chars`` (``truncated``); a single line
    longer than that is cut. The original comes from ``store``, else from ``session_lookup``
    (a hit is cached in the store). A miss or bad arguments yield ``{"error": ...}``; this never
    raises.
    """
    try:
        return _expand(store, args, session_lookup, session_id)
    except Exception as exc:  # a tool call must always answer with JSON
        return _error(f"tameru_expand failed: {type(exc).__name__}: {exc}")


def _expand(
    store: OriginalStore, args: Any, session_lookup: Callable[[str], str | None] | None,
    session_id: str,
) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = None
    if not isinstance(args, dict):
        return _error("arguments must be a JSON object with a ref")
    raw_ref = args.get("ref")
    ref = raw_ref.strip().lower().removeprefix("ref=") if isinstance(raw_ref, str) else ""
    if not _REF_RE.fullmatch(ref):
        return _error("ref must be the 8 hex characters after ref= in a [tameru:...] header")
    try:
        first, last, limit = (_positive_int(args, key) for key in ("start_line", "end_line", "max_chars"))
    except ValueError as exc:
        return _error(str(exc))
    limit = min(limit or DEFAULT_MAX_CHARS, MAX_MAX_CHARS)
    pattern = None
    grep = args.get("grep")
    if grep not in (None, ""):
        if not isinstance(grep, str) or len(grep) > MAX_GREP_CHARS:
            return _error(f"grep must be a regex string of at most {MAX_GREP_CHARS} characters")
        try:
            pattern = re.compile(grep, re.IGNORECASE)
        except re.error as exc:
            return _error(f"grep is not a valid regex: {exc}")

    original = store.get(ref)
    if original is None and session_lookup is not None:
        try:
            original = session_lookup(ref)
        except Exception:
            original = None
        if original is not None:
            store.put(ref, original)
    if original is None:
        return _error(
            f"ref not found: {ref} (it is not in this process's recovery store or the session archive)",
            hint=_MISS_HINT.format(session_id=session_id or "<id>"),
        )

    parts = _TERMINATOR_RE.split(original)   # text, break, text, ..., text
    texts, breaks = parts[0::2], parts[1::2]
    total = len(texts)
    first = first or 1
    last = min(last or total, total)
    if first > total:
        return _error(f"start_line {first} is past the end of the output", total_lines=total)
    if last < first:
        return _error("end_line must not be before start_line", total_lines=total)

    chosen = [n for n in range(first, last + 1) if pattern is None or pattern.search(texts[n - 1])]
    content: list[str] = []
    returned: list[int] = []
    used = 0
    truncated = False
    for n in chosen:
        line = texts[n - 1] + (breaks[n - 1] if n <= len(breaks) else "")
        if used + len(line) > limit:
            truncated = True
            if not content:   # one oversized line: its head, so the call still makes progress
                content.append(line[:limit])
                returned.append(n)
            break
        content.append(line)
        returned.append(n)
        used += len(line)
    result: dict[str, Any] = {
        "ref": ref,
        "total_lines": total,
        "returned_lines": [returned[0], returned[-1]] if returned else [0, 0],
        "content": "".join(content),
        "truncated": truncated,
    }
    if pattern is not None:
        result["matched_lines"] = returned
    return json.dumps(result, ensure_ascii=False)


# ---- the session-archive lookup --------------------------------------------------------------
def _identity_views(row: dict) -> list[dict]:
    """The identity dicts ``make_ref`` may have seen for a session-db ``row``, likeliest first.

    SessionDB names the per-occurrence tool uid column ``tool_call_uid``; the live message that
    was ref'd carried it as ``_tool_call_uid``. A ref minted before the row's first flush used
    the uid that existed then, so each lower-precedence identity alone is a fallback view.
    """
    ids = {
        "_tool_call_uid": row.get("_tool_call_uid") or row.get("tool_call_uid"),
        "message_uid": row.get("message_uid"),
        "tool_call_id": row.get("tool_call_id"),
    }
    ids = {key: value for key, value in ids.items() if value}
    return [ids, *({key: value} for key, value in list(ids.items())[1:])]


def _ref_index(
    rows: list[tuple[dict, str]], make_ref: Callable[[dict, str], str], *, fallback: bool,
) -> dict[str, str]:
    """``ref -> original`` over ``rows``, from each row's likeliest identity (or its fallbacks)."""
    index: dict[str, str] = {}
    for row, content in rows:
        views = _identity_views(row)
        for view in views[1:] if fallback else views[:1]:
            index.setdefault(make_ref(view, content), content)
    return index


def session_lookup_factory(
    session_db: Any, session_id: str, make_ref: Callable[[dict, str], str],
) -> Callable[[str], str | None]:
    """A ``ref -> original text | None`` lookup over the archived tool rows of ``session_id``.

    The first call reads ``session_db.get_messages(session_id, include_compacted=True)`` (the
    compaction-archived originals included) and keeps the last ``MAX_SCAN_ROWS`` rows. Each tool
    row that is not itself a Tameru render gets the ref ``make_ref`` would have minted for it, and
    a match returns its content. The scan is cached for the life of the returned callable, which
    never raises: any failure is a miss.
    """
    rows: list[tuple[dict, str]] | None = None
    indexes: list[dict[str, str]] = []   # [likeliest identities, fallback identities], built on demand

    def lookup(ref: str) -> str | None:
        nonlocal rows
        try:
            from .render import is_tameru_rendered

            if rows is None:
                messages = session_db.get_messages(session_id, include_compacted=True)
                rows = [
                    (row, row["content"]) for row in messages[-MAX_SCAN_ROWS:]
                    if isinstance(row, dict) and row.get("role") == "tool"
                    and isinstance(row.get("content"), str) and not is_tameru_rendered(row["content"])
                ]
            found = next((index[ref] for index in indexes if ref in index), None)
            while found is None and len(indexes) < 2:
                indexes.append(_ref_index(rows, make_ref, fallback=bool(indexes)))
                found = indexes[-1].get(ref)
            return found
        except Exception:
            return None

    return lookup
