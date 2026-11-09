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

import hashlib
import json
import re
import subprocess
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
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
        "Call this whenever you need a detail that is not visible in a tool result marked [tameru:...], and before you "
        "quote it, rely on it, or say it is missing: exact values, paths, error text, line numbers. Prefer grep or a "
        "line range over a full read. Pass the hex ref from the result's header [tameru:... ref=xxxxxxxxxxxxxxxx] "
        "(16 characters, 8 on older rows). Returns the original lines (start_line..end_line, 1-based), or only those "
        "matching the case-insensitive regex grep. If truncated is true, continue after returned_lines. "
        "Originals that looked like they held credentials are never stored, so a miss there is expected. "
        "If a ref is not found, try session_search(role_filter='tool')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Hex after ref= in the result's header (16 chars, 8 on older rows).",
            },
            "start_line": {
                "type": "integer",
                "description": "First line, 1-based (default 1).",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line, inclusive (default: the last).",
            },
            "grep": {
                "type": "string",
                "description": f"Case-insensitive regex (max {MAX_GREP_CHARS} chars); only matching lines.",
            },
            "max_chars": {
                "type": "integer",
                "description": f"Character cap (default {DEFAULT_MAX_CHARS:,}, max {MAX_MAX_CHARS:,}).",
            },
        },
        "required": ["ref"],
    },
}

READ_ORIGINAL_TOOL_NAME = "tameru_read_original"
READ_ORIGINAL_SCHEMA = {**EXPAND_SCHEMA, "name": READ_ORIGINAL_TOOL_NAME}

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
    meta: Mapping[str, Any] = field(default_factory=dict)
    _accounted_chars: int = field(default=0, repr=False, compare=False)


def _stored_meta(meta: dict | None, max_chars: int) -> tuple[Mapping[str, Any], int] | None:
    """Validate envelope metadata for storage; return (immutable copy, compact-JSON length).

    None for anything outside the documented scalar shape, an unserializable value, a probable
    credential, or metadata over the budget left after the body. The early ``lower_bound`` check
    refuses obviously over-budget strings without serializing them.
    """
    if meta is not None and not isinstance(meta, dict):
        return None
    copied = {}
    lower_bound = 0
    for key, value in (meta or {}).items():
        if type(key) is not str or type(value) not in (str, int, float, bool, type(None)):
            return None
        lower_bound += len(key) + (len(value) if isinstance(value, str) else 0)
        if lower_bound > max_chars:
            return None
        copied[key] = value
    try:
        encoded = json.dumps(copied, ensure_ascii=False, allow_nan=False, separators=(',', ':')) if copied else ''
    except ValueError:  # non-finite values or Python's integer-string conversion limit
        return None
    if len(encoded) > max_chars or contains_secret(encoded):
        return None
    return MappingProxyType(copied), len(encoded)


class OriginalStore:
    """Thread-safe LRU of original tool results, bounded by entry count and total characters.

    ``total_chars`` counts each entry's original text plus its tool name plus the compact JSON of
    its scalar envelope metadata retained for later rebriefing, not just the body.
    Entries whose metadata is malformed, credential-bearing or oversized are refused whole, with
    no changes and no evictions.

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
        larger than ``max_chars``, is not text, or its tool name/metadata fail the same screen.
        """
        if (
            not isinstance(original, str) or not isinstance(tool, str) or self._max_entries < 1
            or len(original) > self._max_chars or len(tool) > self._max_chars
            or contains_secret(original) or contains_secret(tool)
        ):
            return False
        prepared = _stored_meta(meta, self._max_chars - len(original))
        if prepared is None:
            return False
        stored_meta, meta_chars = prepared
        with self._lock:
            old = self._entries.get(ref)
            if old is not None and is_untrusted_tool(old.tool):
                tool = old.tool
            size = len(original) + len(tool) + meta_chars
            if size > self._max_chars:
                return False
            if old is not None:
                self._entries.pop(ref)
                self._chars -= old._accounted_chars
            while self._entries and (
                len(self._entries) >= self._max_entries or self._chars + size > self._max_chars
            ):
                self._chars -= self._entries.popitem(last=False)[1]._accounted_chars
            self._entries[ref] = StoredOriginal(original, tool, stored_meta, size)
            self._chars += size
        return True

    def entry(self, ref: str) -> StoredOriginal | None:
        """The entry for ``ref`` (now the most recently used), else None."""
        with self._lock:
            key = _resolve_ref(self._entries, ref)
            if key is None:
                return None
            self._entries.move_to_end(key)
            return self._entries[key]

    def matches(self, ref: str) -> dict[str, "StoredOriginal"]:
        """Snapshot candidate identities without changing eviction order."""
        with self._lock:
            return {key: self._entries[key] for key in _matching_refs(self._entries, ref)}

    def peek(self, ref: str) -> StoredOriginal | None:
        """Read without changing the recovery cache's eviction order."""
        with self._lock:
            key = _resolve_ref(self._entries, ref)
            return self._entries.get(key) if key is not None else None

    def get(self, ref: str) -> str | None:
        """The original text for ``ref``, else None."""
        found = self.entry(ref)
        return None if found is None else found.text

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._chars = 0

    def fork(self) -> "OriginalStore":
        """A same-cap copy of the bounded entries; a prune pass may publish it or drop it."""
        out = type(self)(self._max_entries, self._max_chars)
        with self._lock:
            out._entries = self._entries.copy()
            out._chars = self._chars
        return out

    def adopt(self, prepared: "OriginalStore") -> None:
        """Replace this store's entries with ``prepared``'s, keeping this identity and caps."""
        with prepared._lock:
            entries = prepared._entries.copy()
            chars = prepared._chars
        with self._lock:
            self._entries = entries
            self._chars = chars

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
        return _error(f"tameru_read_original failed: {type(exc).__name__}: {exc}")


SECRET_SOURCE = "secret"      # ``lookup_original`` source when the original held a credential and was refused
SCREEN_REFUSALS = {"count": 0}   # integer counter only (never content); surfaced by the status line


def screen_original(text: str | None) -> str | None:
    """The one gate every original passes before anything shows it, restores it or hands it on.

    ``OriginalStore.put`` already refuses credentials, but the session archive is Hermes' own transcript
    and holds whatever the tool printed. Fails closed: a credential, or any error while checking, yields
    ``None`` (as if no original existed) and bumps ``SCREEN_REFUSALS``.
    """
    if text is None:
        return None
    try:
        if contains_secret(text):
            SCREEN_REFUSALS["count"] += 1
            return None
        return text
    except Exception:
        SCREEN_REFUSALS["count"] += 1
        return None


def lookup_original(
    store: OriginalStore, ref: str,
    session_lookup: Callable[[str], str | None] | None, *, cache: bool,
) -> tuple[StoredOriginal | None, str]:
    """Resolve ``ref`` to one ``(StoredOriginal | None, 'memory'|'archive')``.

    An 8-hex ref with a native ``_SessionLookup`` merges memory and archive
    candidates by canonical key: more than one distinct candidate, or an
    unreadable archive (``candidates`` None), refuses rather than returning a
    wrong original. ``cache=True`` records the archive hit under the selected
    canonical key; ``cache=False`` never touches the store.
    """
    if len(ref) == 8 and isinstance(session_lookup, _SessionLookup):
        memory = store.matches(ref)
        if len(memory) > 1:
            return None, "memory"
        archived = session_lookup.candidates(ref)
        if archived is None:
            return None, "archive"
        merged: dict[str, tuple[str, StoredOriginal | None]] = {}
        for key, entry in memory.items():
            merged[key] = (entry.text, entry)
        for key, text in archived.items():
            if key in merged:
                if merged[key][0] != text:
                    return None, "archive"
            else:
                merged[key] = (text, None)
        if len(merged) != 1:
            return None, "archive"
        key = next(iter(merged))
        text, entry = merged[key]
        if entry is not None:
            if cache:
                store.entry(key)
            return entry, "memory"
        if screen_original(text) is None:
            return None, SECRET_SOURCE
        if cache:
            store.put(key, text)
        return StoredOriginal(text), "archive"
    entry = store.entry(ref) if cache else store.peek(ref)
    if len(ref) == 8 and len(store.matches(ref)) > 1:
        return None, "memory"
    if entry is not None:
        return entry, "memory"
    if session_lookup is not None:
        try:
            text = session_lookup(ref)
        except Exception:
            text = None
        if text is not None:
            if screen_original(text) is None:
                return None, SECRET_SOURCE
            if cache and len(ref) == 16:
                store.put(ref, text)
            return StoredOriginal(text), "archive"
    return None, "archive"


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

    entry, _source = lookup_original(store, ref, session_lookup, cache=True)
    if _source == SECRET_SOURCE:
        return _error("That output held a credential, so Tameru does not keep or show it. "
                      "Re-run the command if you need it again.")
    original = entry.text if entry is not None else None
    tool = entry.tool if entry is not None else ""
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
        overhead = len(str(n)) + 2 if pattern is not None else 0
        if used + len(line) + overhead > limit:
            truncated = True
            if not content:   # one oversized line: its head, so the call still makes progress
                content.append(line[:max(1, limit - overhead)])
                returned.append(n)
            break
        content.append(line)
        returned.append(n)
        used += len(line) + overhead
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


def _matching_refs(keys: Any, ref: str) -> list[str]:
    """The keys ``ref`` can name: a full-length ref matches itself only; an 8-hex
    ref matches an identical 8 key plus every 16-hex key it prefixes."""
    if len(ref) != 8:
        return [ref] if ref in keys else []
    return [key for key in keys if key == ref or (len(key) == 16 and key.startswith(ref))]


def _resolve_ref(keys: Any, ref: str) -> str | None:
    """The sole key of ``keys`` that ``ref`` names: itself, or (a legacy 8-hex
    ref) the one matching key it identifies. Two keys sharing the 8 chars make
    an old ref ambiguous: no key, rather than a wrong original."""
    hits = _matching_refs(keys, ref)
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
    # Newer Hermes dedupes ``include_compacted`` reads to one row per logical message (the live, shortened one), so
    # the superseded originals only come back with ``include_inactive``; older Hermes has no such argument.
    for kwargs in (dict(include_inactive=True, include_compacted=True, include_ancestors=True),
                   dict(include_compacted=True, include_ancestors=True),
                   dict(include_compacted=True)):
        try:
            return session_db.get_messages(session_id, **kwargs)
        except TypeError:
            continue
    return session_db.get_messages(session_id)


class _SessionLookup:
    """Archive lookup with structured short-ref candidate resolution.

    ``candidates`` inspects both identity indexes and gathers every matching
    canonical key mapped to its raw archived content; a key seen under differing
    raw content makes the read uncertain (None). ``__call__`` keeps the plain
    ``ref -> text | None`` contract: an 8-hex ref resolves only a sole
    candidate, a 16-hex ref its exact key lazily.
    """

    def __init__(self, session_db: Any, session_id: str,
                 make_ref: Callable[[dict, str], str],
                 project: Callable[[str], str] | None = None) -> None:
        self.db = session_db
        self.sid = session_id
        self.maker = make_ref
        self.project = project
        self.rows: list[tuple[dict, str]] | None = None
        self.indexes: list[dict[str, str]] = []
        self._digests: dict[str, str] | None = None

    def _index(self, position: int) -> dict[str, str] | None:
        try:
            if self.rows is None:
                messages = _archived_messages(self.db, self.sid)
                self.rows = [
                    (row, row["content"]) for row in messages[-MAX_SCAN_ROWS:]
                    if isinstance(row, dict) and row.get("role") == "tool"
                    and isinstance(row.get("content"), str)
                ]
            while len(self.indexes) <= position and len(self.indexes) < 2:
                self.indexes.append(
                    _ref_index(self.rows, self.maker, fallback=bool(self.indexes)))
            return self.indexes[position] if position < len(self.indexes) else None
        except Exception:
            return None

    def vouch(self, ref: str, live_row: dict, from_digest: Callable[[dict, str], str]) -> str | None:
        """The projected original that an archived row proves for ``live_row``, else None.

        ``from_digest(row, sha256_hex)`` is ``make_ref`` for a known digest. The ref in a header names the
        message id *and* the original's hash, so it is reproduced here from the live row's own id and each
        archived row's raw content: a header copied onto another message, or one printed by a tool, matches
        nothing. More than one distinct archived original for the ref is refused.
        """
        try:
            if self._index(0) is None or not self.rows:
                return None
            if self._digests is None:
                self._digests = {}
                for _, raw in self.rows:
                    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
                    self._digests.setdefault(digest, raw)
            # A ref was made from one id the message carried at the time (render._UID_KEYS order); the stored copy
            # can carry more ids than the in-memory one did, so each of the row's own ids is tried on its own.
            variants = [{key: live_row[key]} for key in ("_tool_call_uid", "message_uid", "tool_call_id")
                        if live_row.get(key)]
            if not variants:
                return None
            found = {raw for digest, raw in self._digests.items()
                     if any(from_digest(variant, digest).startswith(ref) for variant in variants)}
            if len(found) != 1:
                return None
            raw = next(iter(found))
            return self.project(raw) if self.project is not None else raw
        except Exception:
            return None

    def candidates(self, ref: str) -> dict[str, str] | None:
        found: dict[str, str] = {}
        for position in (0, 1):
            index = self._index(position)
            if index is None:
                return None
            for key in _matching_refs(index, ref):
                value = index[key]
                if key in found and found[key] != value:
                    return None
                found[key] = value
        if self.project is not None:
            try:
                return {key: self.project(value) for key, value in found.items()}
            except Exception:
                return None
        return found

    def __call__(self, ref: str) -> str | None:
        try:
            if len(ref) != 16:
                found = self.candidates(ref)
                if not found or len(found) != 1:
                    return None
                return next(iter(found.values()))
            index = self._index(0)
            if index is None:
                return None
            content = index.get(ref)
            if content is None:
                index = self._index(1)
                if index is None:
                    return None
                content = index.get(ref)
            if content is None:
                return None
            return self.project(content) if self.project is not None else content
        except Exception:
            return None


def session_lookup_factory(
    session_db: Any, session_id: str, make_ref: Callable[[dict, str], str],
    *, project: Callable[[str], str] | None = None,
) -> Callable[[str], str | None]:
    """A ``ref -> original text | None`` lookup over the archived tool rows of ``session_id``.

    The first call reads ``session_db.get_messages(session_id, include_compacted=True)`` (the
    compaction-archived originals included, and after a session rotation those of the parent sessions)
    and keeps the last ``MAX_SCAN_ROWS`` rows. Each tool
    row is indexed by the ref ``make_ref`` derives from its actual content, including raw content
    that resembles a Tameru marker; embedded references are never followed. A match returns that
    content. The scan is cached for the life of the returned callable, which
    never raises: any failure is a miss.
    """
    return _SessionLookup(session_db, session_id, make_ref, project)
