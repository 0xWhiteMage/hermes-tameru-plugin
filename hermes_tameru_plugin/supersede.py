"""Structural supersession (G14): tool results a later call has made stale or redundant.

Hermes only de-duplicates byte-identical output. This module recognises the structural cases:

* ``stale-after-write``: a ``read_file`` / ``search_files`` result for path P, and a later
  successful ``write_file`` / ``patch`` touched P (same normalised path);
* ``re-read``: a later successful ``read_file`` of the same path whose line range covers this one
  (a partial overlap does not cover);
* ``repeated-call``: a later successful call with the same tool and arguments, for the tools in
  ``REPEATABLE`` (for ``terminal`` only read-only commands);
* ``snapshot``: a state-snapshot call (``browser_snapshot``, ``git status``, ``git diff --stat``,
  ``ls``) with any later successful call of the same family.

``build_index`` walks the rows of one pass once; ``superseded_by`` answers per row without
rescanning them. What a result says (did the call succeed, does the row still carry its output) is
evaluated lazily and cached per row, and a lookup considers a bounded number of later rows, so a
pass stays linear in its rows.

A row whose own call failed holds the error the agent may need: only a later identical call that
succeeded supersedes it. The pending tool round (the results the transcript ends with) is never
superseded. A later row only counts when it actually holds what it replaces: a pruned stub, a
Hermes duplicate marker, a read that returned no content ("unchanged") or a spilled
``<persisted-output>`` preview does not.
"""
from __future__ import annotations

import bisect
import json
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from .hermes_compat import (
    DUPLICATE_PREFIX,
    LEAN_STUB_RE,
    PERSISTED_OUTPUT_TAG,
    PRUNED_PLACEHOLDER,
    SKILL_PRUNED_MARKER_PREFIX,
    is_summary_stub,
    refused_summary,
    result_failure_suffix,
    tool_calls_by_id,
)
from .tameru.transcript import text_of

STALE_AFTER_WRITE = "stale-after-write"
RE_READ = "re-read"
REPEATED_CALL = "repeated-call"
SNAPSHOT = "snapshot"

REPEATABLE = frozenset({
    "read_file", "search_files", "web_search", "web_extract", "browser_snapshot", "read_terminal",
    "terminal",
})
_WRITERS = frozenset({"write_file", "patch"})
_READERS = frozenset({"read_file", "search_files"})
_TRACKED = REPEATABLE | _WRITERS

_PATH_KEYS = ("path", "file_path", "file", "target", "filename")
_SEARCH_PATH_KEYS = ("path", "file_path", "file", "filename")  # ``search_files`` target: a mode
_OPEN = float("inf")  # end of a read with no ``limit``
SCAN_LIMIT = 32  # later rows a lookup considers; keeps a pass linear when many rows share a key
_MAX_READ_LINES = 2000  # Hermes clamps a ``read_file`` ``limit`` to ``tool_output.max_lines`` (2000)
_STUB_MAX = 400  # Hermes' stub length bound (``_is_summary_stub``)
_STEER_DISPLAY_KIND = "steer"  # Hermes' /steer rows trail the pending round without answering it

# Read-only terminal commands: first token, shell constructs that write or chain, and per-command
# switches that make an otherwise read-only command mutate or never return.
_READ_ONLY_COMMANDS = frozenset({
    "git", "ls", "pwd", "cat", "head", "tail", "grep", "rg", "find", "wc", "stat", "ps", "df", "du",
    "env", "which",
})
_GIT_READ_ONLY = frozenset({
    "status", "diff", "log", "show", "ls-files", "ls-tree", "rev-parse", "rev-list", "blame",
    "describe", "grep", "shortlog", "show-ref", "cat-file", "diff-tree",
})
_FIND_MUTATORS = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls",
})
_SHELL_CONTROL_RE = re.compile(r"[>|;&`\n]|\$\(|<\(")
_RM_RE = re.compile(r"(?<![\w./-])rm(?![\w-])")
_CD_RE = re.compile(r"(?<![\w./-])(?:cd|pushd|popd)(?![\w-])")   # Hermes' terminal keeps its cwd between calls
_CWD_BLIND_COMMANDS = frozenset({"env", "ps", "df", "which"})
_QUOTING_RE = re.compile(r"[\"'\\]")
_RECURSIVE_RE = re.compile(r"-[A-Za-z]*R[A-Za-z]*|--recursive")
_FOLLOW_RE = re.compile(r"^(?:-[A-Za-z]*[fF][A-Za-z0-9]*|--follow.*)$")

_V4A_FILE_RE = re.compile(r"^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$", re.MULTILINE)
_V4A_MOVE_RE = re.compile(r"^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+?)\s*$", re.MULTILINE)

# What Hermes' summary of a failed call looks like (``... FAILED: <error>``, ``-> exit 1``).
_STUB_FAILED_RE = re.compile(r" FAILED\b")
_STUB_EXIT_RE = re.compile(r"-> exit (-?\d+|\?)")
_TOOL_STUB_RE = re.compile(r"\[[a-z][a-z0-9_]*\] ")  # Hermes' one-line ``[tool] ...`` summaries
_NOT_LIVE_PREFIXES = (
    DUPLICATE_PREFIX, PRUNED_PLACEHOLDER, PERSISTED_OUTPUT_TAG, SKILL_PRUNED_MARKER_PREFIX,
)


class _Outcome(NamedTuple):
    """What a tool result says, as far as supersession cares."""

    ok: bool  # the call ran and did its job (a patch that changed nothing did not)
    live: bool  # the row still holds the output (not a stub, marker, preview or "unchanged")
    span: tuple[int, float] | None  # lines of the file a ``read_file`` actually returned


@dataclass(slots=True)
class ToolCall:
    """One tracked tool-result row of the pass."""

    idx: int
    tool: str
    content: Any
    canon: str | None = None  # canonical arguments, when a later identical call can supersede
    path: str | None = None  # normalised path argument of a read or search
    paths: tuple[str, ...] = ()  # normalised paths a ``write_file`` / ``patch`` touches
    span: tuple[int, float] | None = None  # lines a ``read_file`` asks for (inf: no limit)
    family: tuple | None = None  # snapshot family
    outcome: _Outcome | None = None

    def assess(self) -> _Outcome:
        """The outcome of this row, evaluated on first use."""
        if self.outcome is None:
            self.outcome = _assess(self)
        return self.outcome


@dataclass(slots=True)
class SupersessionIndex:
    """Lookup tables over the tracked rows of one pass; every position list is ascending."""

    pending: range
    calls: dict[int, ToolCall] = field(default_factory=dict)
    writes: dict[str, list[int]] = field(default_factory=dict)
    reads: dict[str, list[int]] = field(default_factory=dict)
    same_call: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    families: dict[tuple, list[int]] = field(default_factory=dict)


# ---- arguments -------------------------------------------------------------------------------
def _as_int(value: Any) -> int | None:
    """``value`` as an int when it is one (or a numeric string), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _args_dict(raw: Any) -> dict | None:
    """Tool-call arguments as a dict (they arrive as a JSON string), or None when unusable."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            return None
    return raw if isinstance(raw, dict) else None


def _canonical(args: dict) -> str | None:
    """Key-sorted JSON of ``args``, or None when it cannot be serialised."""
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        return None


def _norm_path(value: Any) -> str | None:
    """``value`` as a normalised path (lexical only: nothing here touches the file system)."""
    if not isinstance(value, str) or not value.strip():
        return None
    return posixpath.normpath(value.strip())


def _path_of(args: dict, keys: tuple[str, ...] = _PATH_KEYS) -> str | None:
    """The file a call names, from the first of ``keys`` that holds a path."""
    return next((p for p in (_norm_path(args.get(k)) for k in keys) if p is not None), None)


def _written_paths(args: dict) -> tuple[str, ...]:
    """Files a ``write_file`` / ``patch`` call touches, V4A patch bodies included."""
    found = [_path_of(args)]
    patch = args.get("patch")
    if isinstance(patch, str):
        found += _V4A_FILE_RE.findall(patch)
        found += [p for pair in _V4A_MOVE_RE.findall(patch) for p in pair]
    return tuple(dict.fromkeys(p for p in map(_norm_path, found) if p is not None))


def _span(args: dict) -> tuple[int, float] | None:
    """Lines a ``read_file`` call asks for: ``(first, last)``; ``last`` is inf without ``limit``."""
    offset, limit = args.get("offset"), args.get("limit")
    first = 1 if offset is None else _as_int(offset)
    count = None if limit is None else _as_int(limit)
    if first is None or (limit is not None and (count is None or count < 1)):
        return None
    first = max(first, 1)
    return first, _OPEN if count is None else first + count - 1


# ---- terminal commands -----------------------------------------------------------------------
def _read_only(command: str, tokens: list[str]) -> bool:
    """True for a plain read-only command: no redirect, pipe, chain, ``rm`` or mutating switch."""
    if not tokens or tokens[0] not in _READ_ONLY_COMMANDS:
        return False
    if _SHELL_CONTROL_RE.search(command) or _RM_RE.search(command):
        return False
    head, rest = tokens[0], tokens[1:]
    if head == "git":
        return bool(rest) and rest[0] in _GIT_READ_ONLY and not any(
            t.startswith("--output") for t in rest
        )
    if head == "env":
        return all(t.startswith("-") for t in rest)  # ``env VAR=x cmd`` runs cmd
    if head == "find":
        return _FIND_MUTATORS.isdisjoint(rest)
    if head == "tail":
        return not any(_FOLLOW_RE.match(t) for t in rest)  # following never returns
    return True


def _cwd_free(tokens: list[str]) -> bool:
    """True when a read-only command's result cannot depend on the shell's working directory: it names
    only absolute (or ``~``) paths and numbers, or is one of the commands that never look at it."""
    if tokens[0] in _CWD_BLIND_COMMANDS:
        return True
    operands = [t for t in tokens[1:] if not t.startswith("-")]
    return bool(operands) and all(t.startswith(("/", "~")) or t.isdigit() for t in operands)


def _terminal_family(tokens: list[str], workdir: Any, epoch: int = 0) -> tuple | None:
    """Snapshot family of ``git status`` / ``git diff --stat`` / ``ls``: command and its targets.

    ``epoch`` counts the earlier calls that changed the shell's working directory: the same relative
    command asks about a different place after a ``cd``. A recursive ``ls`` is a family of its own (a
    later shallow listing does not replace it).
    """
    if tokens[0] == "ls":
        kind, rest = "ls", tokens[1:]
        if any(_RECURSIVE_RE.fullmatch(t) for t in rest):
            kind = "ls -R"
    elif tokens[0] == "git" and tokens[1:2] == ["status"]:
        kind, rest = "git status", tokens[2:]
    elif tokens[0] == "git" and tokens[1:2] == ["diff"] and any(
        t.startswith("--stat") for t in tokens
    ):
        kind, rest = "git diff --stat", tokens[2:]
    else:
        return None
    targets = tuple(t for t in rest if not t.startswith("-"))
    return "terminal", kind, targets, workdir if isinstance(workdir, str) else "", epoch


# ---- what a result says ----------------------------------------------------------------------
def _json_object(text: str) -> dict:
    """``text`` parsed as a JSON object; ``{}`` for anything else."""
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _outcome_sources(text: str) -> tuple[str, str]:
    """``(summary line, JSON text)`` a result's outcome is read from; either may be empty.

    A raw Hermes payload is JSON. A row Hermes or the engine already reduced starts with a
    one-line ``[...]`` summary, and the engine's render follows it with a compact JSON meta line.
    """
    if text.startswith("{"):
        return "", text
    nl = text.find("\n")
    head = text if nl < 0 else text[:nl]
    if not head.startswith("[") or len(head) > _STUB_MAX:
        return "", ""
    meta = ""
    if nl >= 0:
        end = text.find("\n", nl + 1)
        meta = text[nl + 1 : len(text) if end < 0 else end]
    return head, meta if meta.startswith("{") else ""


def _bad_exit(code: Any) -> bool:
    return isinstance(code, int) and not isinstance(code, bool) and code != 0


def _stub_failed(head: str) -> bool:
    """True when a one-line summary reports a failure or a non-zero exit."""
    exit_code = _STUB_EXIT_RE.search(head)
    return bool(_STUB_FAILED_RE.search(head)) or (exit_code is not None and exit_code[1] != "0")


def _assess(call: ToolCall) -> _Outcome:
    text = text_of(call.content)
    head, body = _outcome_sources(text)
    payload = _json_object(body) if body else {}
    failed = any((
        body and result_failure_suffix(body),
        _bad_exit(payload.get("exit_code")),
        head and _stub_failed(head),
    ))
    if not failed and (
        text[:64].lstrip().startswith("BLOCKED") or payload.get("status") == "pending_approval"
    ):
        failed = refused_summary(call.tool, {}, text) is not None
    changed = call.tool not in _WRITERS or not payload.get("no_change")
    stub = len(text) < _STUB_MAX and bool(
        is_summary_stub(text) or LEAN_STUB_RE.match(text) or _TOOL_STUB_RE.match(text)
    )
    live = not (
        text.startswith(_NOT_LIVE_PREFIXES) or stub or payload.get("content_returned") is False
    )
    span = call.span
    if span is not None and payload.get("truncated"):
        next_offset = _as_int(payload.get("next_offset"))
        if next_offset is not None:
            span = (span[0], min(span[1], next_offset - 1))
        elif span[1] == _OPEN or span[1] - span[0] >= _MAX_READ_LINES:
            span = None  # cut at an unknown line (no limit, or a limit Hermes clamped)
    return _Outcome(ok=not failed and changed, live=live, span=span)


# ---- index -----------------------------------------------------------------------------------
def _get(msg: Any, key: str) -> Any:
    return msg.get(key) if isinstance(msg, dict) else None


def _pending_round(messages: list[dict]) -> range:
    """Indices of the tool results the transcript ends with: a round the model has not answered."""
    end = len(messages)
    while end and _get(messages[end - 1], "display_kind") == _STEER_DISPLAY_KIND:
        end -= 1
    start = end
    while start and _get(messages[start - 1], "role") == "tool":
        start -= 1
    return range(start, end)


def _lookup(table: dict[str, tuple[str, Any]], call_id: Any) -> tuple[str, Any]:
    """``(tool, raw args)`` of the call a result answers; an id may be ``call_id|item_id``."""
    if isinstance(call_id, str):
        for key in (call_id, *call_id.split("|")):
            found = table.get(key.strip()) if key.strip() else None
            if found is not None and isinstance(found[0], str):
                return found
    return "", None


def _make_call(idx: int, tool: str, args: dict, content: Any, epoch: int = 0) -> ToolCall:
    call = ToolCall(idx, tool, content)
    if tool in _WRITERS:
        call.paths = _written_paths(args)
        return call
    call.path = _path_of(args, _SEARCH_PATH_KEYS if tool == "search_files" else _PATH_KEYS)
    if tool == "read_file":
        call.span = _span(args)
    if tool == "terminal":
        raw_command = str(args.get("command") or "")
        # Spacing inside quotes matters (``grep "a  b"`` is not ``grep "a b"``): only a command without
        # quoting is whitespace-normalised.
        command = raw_command.strip() if _QUOTING_RE.search(raw_command) else " ".join(raw_command.split())
        tokens = command.split()
        if not args.get("background") and _read_only(command, tokens):
            epoch = 0 if _cwd_free(tokens) else epoch
            call.canon = _canonical({**args, "command": command, "_cwd_epoch": epoch})
            call.family = _terminal_family(tokens, args.get("workdir"), epoch)
    else:
        call.canon = _canonical(args)
        if tool == "browser_snapshot":
            call.family = ("browser_snapshot",)
    return call


def build_index(
    messages: list[dict], call_id_to_tool: dict[str, tuple[str, Any]] | None = None,
) -> SupersessionIndex:
    """Index the tool-result rows of ``messages`` in one walk.

    ``call_id_to_tool`` maps ``tool_call_id -> (tool name, raw arguments)`` as Hermes hands it to
    the seam; it is derived from ``messages`` when omitted. Only the tools supersession reasons
    about are indexed.
    """
    table = tool_calls_by_id(messages) if call_id_to_tool is None else call_id_to_tool
    index = SupersessionIndex(pending=_pending_round(messages))
    epoch = 0   # earlier terminal calls that may have changed the working directory
    for idx, msg in enumerate(messages):
        if _get(msg, "role") != "tool":
            continue
        tool, raw_args = _lookup(table, msg.get("tool_call_id"))
        args = _args_dict(raw_args) if tool in _TRACKED else None
        if args is None:
            epoch += tool == "terminal"   # unreadable arguments: assume the worst
            continue
        call = _make_call(idx, tool, args, msg.get("content"), epoch)
        if tool == "terminal" and _CD_RE.search(str(args.get("command") or "")):
            epoch += 1
        index.calls[idx] = call
        for path in call.paths:
            index.writes.setdefault(path, []).append(idx)
        if call.tool == "read_file" and call.path is not None:
            index.reads.setdefault(call.path, []).append(idx)
        if call.canon is not None:
            index.same_call.setdefault((tool, call.canon), []).append(idx)
        if call.family is not None:
            index.families.setdefault(call.family, []).append(idx)
    return index


# ---- queries ---------------------------------------------------------------------------------
def _nearest_later(
    index: SupersessionIndex,
    positions: list[int] | None,
    idx: int,
    accept: Callable[[ToolCall], bool],
) -> int | None:
    """First of the next ``SCAN_LIMIT`` rows of ``positions`` after ``idx`` that ``accept``s."""
    if not positions:
        return None
    start = bisect.bisect_right(positions, idx)
    for later in positions[start : start + SCAN_LIMIT]:
        if accept(index.calls[later]):
            return later
    return None


def _succeeded(call: ToolCall) -> bool:
    return call.assess().ok


def _holds_content(call: ToolCall) -> bool:
    outcome = call.assess()
    return outcome.ok and outcome.live


def _covering(span: tuple[int, float]) -> Callable[[ToolCall], bool]:
    """Accept a live, successful read whose returned lines include all of ``span``."""
    def accept(call: ToolCall) -> bool:
        outcome = call.assess()
        got = outcome.span
        covers = got is not None and got[0] <= span[0] and got[1] >= span[1]
        return outcome.ok and outcome.live and covers

    return accept


def superseded_by(index: SupersessionIndex, idx: int) -> tuple[int, str] | None:
    """``(later_idx, reason)`` when a later row makes row ``idx`` stale or redundant, else None.

    Reasons, checked in this order: ``stale-after-write``, ``re-read``, ``repeated-call`` and
    ``snapshot``. ``later_idx`` is the nearest later row that qualifies.
    """
    call = index.calls.get(idx)
    if call is None or idx in index.pending:
        return None
    ok = call.assess().ok
    if ok and call.tool in _READERS and call.path is not None:
        later = _nearest_later(index, index.writes.get(call.path), idx, _succeeded)
        if later is not None:
            return later, STALE_AFTER_WRITE
    if ok and call.span is not None and call.path is not None:
        later = _nearest_later(index, index.reads.get(call.path), idx, _covering(call.span))
        if later is not None:
            return later, RE_READ
    if call.canon is not None:
        repeats = index.same_call.get((call.tool, call.canon))
        later = _nearest_later(index, repeats, idx, _holds_content)
        if later is not None:
            return later, REPEATED_CALL
    if ok and call.family is not None:
        later = _nearest_later(index, index.families.get(call.family), idx, _succeeded)
        if later is not None:
            return later, SNAPSHOT
    return None
