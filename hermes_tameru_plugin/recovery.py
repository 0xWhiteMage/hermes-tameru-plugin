"""Recovery of pruned tool output: the original store, the ``tameru_expand`` tool, the archive lookup.

Every row Tameru renders names its original by ``ref=<16 hex>`` (``render.make_ref``; rows from
before 1.4.1 carry its first 8 hex, which still resolve). Two places can hand the original back:

* ``OriginalStore``: a per-agent, locked LRU of the originals this process pruned. Probable
  credentials are never kept (``contains_secret``).
* the Hermes session archive: compaction archives the original rows (``compacted=1``), so
  ``session_lookup_factory`` finds a ref again after gateway eviction, a restart or ``--resume``.

``expand`` is the body of the ``tameru_expand`` tool: exact original lines by range or regex, as
JSON, never raising. A miss says how to search the session instead. The answer for the original of a web,
browser or MCP result comes inside Hermes' untrusted-data block, as that result did.

Import-safe: stdlib and the vendored engine only, nothing happens at import time. ``render`` is
imported when a lookup first runs, so a plugin dir without it still loads.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .hermes_compat import has_untrusted_wrapper, is_untrusted_tool, wrap_untrusted
from .tameru.compress_context import contains_secret

EXPAND_TOOL_NAME = "tameru_expand"
DEFAULT_MAX_CHARS = 12_000
MAX_MAX_CHARS = 50_000
MAX_GREP_CHARS = 200
MAX_SCAN_ROWS = 20_000    # newest session rows a lookup considers
DEFAULT_GREP_TIMEOUT = 2.0        # seconds a grep that may backtrack can run (in a child process) before it is killed
DEFAULT_GREP_LINE_CHARS = 20_000  # chars of each line a grep scans (defence in depth; the rest of the line is not searched)

# The child that runs a grep which may backtrack: ``re`` cannot be interrupted in-process, a process can be killed.
# Stdlib only, isolated (-I) and without site (-S), so it starts in tens of milliseconds and imports nothing of the
# host. Input: JSON {"p": pattern, "t": [line, ...]} on stdin (ASCII: surrogates escaped); output: JSON hit indexes.
_GREP_CHILD = (
    "import json,re,sys\n"
    "d=json.loads(sys.stdin.buffer.read().decode('ascii'))\n"
    "p=re.compile(d['p'],re.IGNORECASE)\n"
    "sys.stdout.write(json.dumps([i for i,t in enumerate(d['t']) if p.search(t)]))\n"
)

EXPAND_SCHEMA: dict[str, Any] = {
    "name": EXPAND_TOOL_NAME,
    "description": (
        "Recover the exact original text of a tool output that was pruned to save context. "
        "A pruned tool result starts with a one-line summary ending in "
        "[tameru:... ref=xxxxxxxxxxxxxxxx]; pass those hex characters (16, or 8 on older rows) as ref. Returns the original "
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
                "description": "The hex characters after ref= in the pruned result's header (16, or 8 on older rows).",
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

_REF_RE = re.compile(r"[0-9a-f]{16}|[0-9a-f]{8}")
_NESTED_QUANTIFIER_RE = re.compile(r"[)][*+]|[)][{]\d*,?\d*[}]")   # fallback when ``re``'s parser is unreachable
_TERMINATOR_RE = re.compile(r"(\r\n|\r|\n)")   # the same line breaks ``render`` counts lines by
_MISS_HINT = "session_search(query=..., role_filter='tool', session_id='{session_id}')"


def _linear_pattern(pattern: str) -> bool:
    """True for a pattern with no repeat and no alternation (literals, classes, ``.``, anchors, groups of those):
    it cannot backtrack beyond its own length, so it runs in-process. Anything else runs in the timed child."""
    try:
        from re import _constants as const
        from re import _parser as parser

        plain = {
            getattr(const, name) for name in (
                "LITERAL", "NOT_LITERAL", "ANY", "IN", "AT", "CATEGORY", "NEGATE", "RANGE", "CHARSET", "BIGCHARSET",
            ) if hasattr(const, name)
        }

        def flat(sub: Any) -> bool:
            for op, av in sub:
                if op == const.SUBPATTERN:
                    if not flat(av[3]):
                        return False
                elif op not in plain:
                    return False
            return True

        return flat(parser.parse(pattern))
    except Exception:
        return False


def _grep_hits(
    pattern: re.Pattern[str], texts: list[str], timeout: float,
) -> tuple[list[int] | None, str]:
    """Indexes of ``texts`` that ``pattern`` matches: ``(hits, "")``, or ``(None, error)`` when the search ran past
    ``timeout`` seconds (the child is killed) or the child could not run."""
    if _linear_pattern(pattern.pattern):
        return [i for i, text in enumerate(texts) if pattern.search(text)], ""
    if not sys.executable:   # an embedded interpreter has no program to start
        return None, "grep could not run in a child process (no Python executable); use a literal pattern"
    payload = json.dumps({"p": pattern.pattern, "t": texts}).encode("ascii")
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _GREP_CHILD], input=payload, capture_output=True,
            timeout=timeout, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return None, (
            f"grep timed out after {timeout:g} s and was stopped: the pattern backtracks too much on this output; "
            "simplify it (a literal word, or fewer .* / \\w* repeats)"
        )
    except (OSError, ValueError, TypeError) as exc:
        return None, f"grep could not run in a child process ({type(exc).__name__}); use a literal pattern"
    try:
        hits = json.loads(done.stdout.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return None, "grep could not run in a child process (no result); use a literal pattern"
    return [int(i) for i in hits], ""


def _backtracking_risk(pattern: str) -> bool:
    """True for a regex that can backtrack exponentially: a repeat (more than once) whose body holds
    another repeat or an alternation (``(a+)+``, ``(x|x)*``). ``re`` cannot be interrupted, so such a
    ``grep`` from the model (or from text it read) would hang the agent: 26 characters cost 13 seconds.
    """
    try:
        from re import _constants as const
        from re import _parser as parser

        repeats = tuple(
            getattr(const, name) for name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")
            if hasattr(const, name)
        )
        looks = tuple(getattr(const, name) for name in ("ASSERT", "ASSERT_NOT") if hasattr(const, name))
        atomic = getattr(const, "ATOMIC_GROUP", None)

        def risky(sub: Any, repeating: bool) -> bool:
            for op, av in sub:
                if op in repeats:
                    multi = av[1] > 1
                    if (repeating and multi) or risky(av[2], repeating or multi):
                        return True
                elif op == const.BRANCH:
                    if repeating or any(risky(alt, repeating) for alt in av[1]):
                        return True
                elif op == const.SUBPATTERN:
                    if risky(av[3], repeating):
                        return True
                elif op in looks:
                    if risky(av[1], repeating):
                        return True
                elif atomic is not None and op == atomic:
                    if risky(av, repeating):
                        return True
            return False

        return risky(parser.parse(pattern), False)
    except Exception:
        return _NESTED_QUANTIFIER_RE.search(pattern) is not None


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
            key = _resolve_ref(self._entries, ref)
            if key is None:
                return None
            self._entries.move_to_end(key)
            return self._entries[key]

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
            return isinstance(ref, str) and _resolve_ref(self._entries, ref) is not None

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
    grep_timeout: float = DEFAULT_GREP_TIMEOUT,
    grep_line_chars: int = DEFAULT_GREP_LINE_CHARS,
) -> str:
    """The ``tameru_expand`` tool: exact original lines for ``args["ref"]``, as a JSON string.

    Success: ``{"ref", "total_lines", "returned_lines": [first, last], "content", "truncated"}``,
    plus ``matched_lines`` (the line numbers of ``content``'s lines) when ``grep`` is given. Lines
    are numbered like the ``kept K/N lines`` of the row header and ``content`` is the original
    text, line breaks included. Output stops at ``max_chars`` (``truncated``); a single line
    longer than that is cut. The original comes from ``store``, else from ``session_lookup``
    (a hit is cached in the store). A miss or bad arguments yield ``{"error": ...}``; this never
    raises. The answer for a web, browser or MCP result (the store knows the tool; an archived one is
    told by Hermes' wrapper at the start of its text) is wrapped whole as untrusted data, like the result was.

    A ``grep`` with any repeat or alternation runs in a child process killed after ``grep_timeout`` seconds (an
    error asks the model to simplify the pattern); each line is searched in its first ``grep_line_chars`` chars
    (``lines_capped`` counts the lines that were longer).
    """
    try:
        return _expand(store, args, session_lookup, session_id, grep_timeout, grep_line_chars)
    except Exception as exc:  # a tool call must always answer with JSON
        return _error(f"tameru_expand failed: {type(exc).__name__}: {exc}")


def _expand(
    store: OriginalStore, args: Any, session_lookup: Callable[[str], str | None] | None,
    session_id: str, grep_timeout: float = DEFAULT_GREP_TIMEOUT, grep_line_chars: int = DEFAULT_GREP_LINE_CHARS,
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
        return _error("ref must be the 16 (or, on older rows, 8) hex characters after ref= in a [tameru:...] header")
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
        if _backtracking_risk(grep):
            return _error(
                "grep is rejected: a repeated group that holds a repeat or an alternation, such as "
                "(a+)+ or (x|y)*, can run for minutes. Use a flat pattern (a|b alternation outside a "
                "repeat, \\s+, .*)."
            )

    original = store.get(ref)
    tool = ""
    if original is not None:
        try:
            tool = store.entry(ref).tool   # the tool decides whether the answer is untrusted data
        except Exception:
            tool = ""
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

    chosen = list(range(first, last + 1))
    capped = 0
    if pattern is not None:
        cap = max(1, grep_line_chars)
        capped = sum(1 for n in chosen if len(texts[n - 1]) > cap)
        hits, failure = _grep_hits(pattern, [texts[n - 1][:cap] for n in chosen], grep_timeout)
        if hits is None:
            return _error(failure)
        chosen = [chosen[i] for i in hits]
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
        if capped:
            result["lines_capped"] = capped
    answer = json.dumps(result, ensure_ascii=False)
    if is_untrusted_tool(tool) or has_untrusted_wrapper(original):
        return wrap_untrusted(tool if is_untrusted_tool(tool) else EXPAND_TOOL_NAME, answer)
    return answer


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


def _resolve_ref(keys: Any, ref: str) -> str | None:
    """The key of ``keys`` that ``ref`` names: itself, else (a legacy 8-hex ref) the one 16-hex key it prefixes.

    Two 16-hex keys sharing those 8 chars make an old ref ambiguous: no key, rather than a wrong original.
    """
    if ref in keys:
        return ref
    if len(ref) != 8:
        return None
    hits = [key for key in keys if len(key) == 16 and key.startswith(ref)]
    return hits[0] if len(hits) == 1 else None


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


def _archived_messages(session_db: Any, session_id: str) -> list:
    """Every row of ``session_id`` the archive holds, compaction-archived ones included.

    After a compaction that rotated the session (Hermes' parent -> child split) the rows a ref names stay in the
    parent session, so the read covers the verified compression lineage when this Hermes can (``include_ancestors``);
    an older ``get_messages`` without that argument gives the session alone.
    """
    try:
        return session_db.get_messages(session_id, include_compacted=True, include_ancestors=True)
    except TypeError:
        return session_db.get_messages(session_id, include_compacted=True)


def session_lookup_factory(
    session_db: Any, session_id: str, make_ref: Callable[[dict, str], str],
) -> Callable[[str], str | None]:
    """A ``ref -> original text | None`` lookup over the archived tool rows of ``session_id``.

    The first call reads ``session_db.get_messages(session_id, include_compacted=True)`` (the
    compaction-archived originals included, and after a session rotation those of the parent sessions)
    and keeps the last ``MAX_SCAN_ROWS`` rows. Each tool
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
                messages = _archived_messages(session_db, session_id)
                rows = [
                    (row, row["content"]) for row in messages[-MAX_SCAN_ROWS:]
                    if isinstance(row, dict) and row.get("role") == "tool"
                    and isinstance(row.get("content"), str) and not is_tameru_rendered(row["content"])
                ]
            def find(index: dict[str, str]) -> str | None:
                key = _resolve_ref(index, ref)
                return None if key is None else index[key]

            found = next((hit for hit in map(find, indexes) if hit is not None), None)
            while found is None and len(indexes) < 2:
                indexes.append(_ref_index(rows, make_ref, fallback=bool(indexes)))
                found = find(indexes[-1])
            return found
        except Exception:
            return None

    return lookup
