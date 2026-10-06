"""The artifact trail: which files and commands a compacted stretch touched, and how each ended.

A lossy summarizer may drop the exact paths and exit codes the next turn needs. ``build_ledger``
derives them mechanically from the tool calls and results of the turns being summarized, and
``merge_into_summary`` puts them in the summary under a fixed heading (replacing, never
duplicating, an earlier section)::

    ## Artifact Trail (Tameru, exact)
    Files:
    - `src/app.py`: read, patch x2; last patch ok
    - `tests/test_app.py`: read; last read FAILED
    Commands:
    - `pytest -x tests/` -> exit 1 (x3)
    Errors:
      2025-03-14T02:17:41Z ERROR order=ORD-90417 capture failed: gateway timeout

Files carry their operations (read / write / patch / search) with counts and the outcome of the
last one (``ok``, ``FAILED``, or ``?`` when only a pruned stub is left). Commands carry the last
exit code. Entries keep first-seen order. Files and Commands stay within ``max_chars``: when they
would not fit, the least recently touched entries are left out and a ``(+N more)`` line says how many.
An outcome is read from the original when the row is a Tameru render whose original the store
still holds, else from the row itself.

Errors are the first exemplar line of every distinct error fingerprint (``error_fingerprints``) in the
original text of the summarized tool results, so a lossy summarizer cannot drop the failures a pruned
log held; they live in their own budget (``ERRORS_CHARS``, on top of ``max_chars``) and the most
recently seen win when they do not all fit.

The text never contains ``User asked:`` (Hermes' summary validator reads it as a user claim), and a
path or command that holds a probable credential is shown as ``<redacted>``.

Import-safe: stdlib, sibling modules and the vendored engine only, nothing happens at import time.
"""
from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

from .hermes_compat import (
    DUPLICATE_PREFIX,
    LEAN_STUB_RE,
    PERSISTED_OUTPUT_TAG,
    PRUNED_PLACEHOLDER,
    refused_summary,
    tool_calls_by_id,
)
from .payload import meta_line, parse_payload
from .render import parse_header
from .tameru.compress_context import contains_secret, error_fingerprints
from .tameru.transcript import text_of

if TYPE_CHECKING:
    from .recovery import OriginalStore

HEADING = "## Artifact Trail (Tameru, exact)"
MAX_CHARS = 1500              # the Files and Commands lines
ERRORS_CHARS = 1400           # the Errors lines, on top of that
ERROR_LINE_CHARS = 200

_FILE_OPS = {"read_file": "read", "write_file": "write", "patch": "patch", "search_files": "search"}
_PATH_KEYS = ("path", "file_path", "file", "filename")   # not ``target``: a search mode
_V4A_PATH_RE = re.compile(
    r"^\*\*\*\s*(?:(?:Update|Add|Delete)\s+File:\s*(?P<path>.+?)"
    r"|Move\s+File:\s*(?P<src>.+?)\s*->\s*(?P<dst>.+?))\s*$",
    re.MULTILINE,
)
_EXIT_RE = re.compile(r"-> exit (-?\d+)")
_USER_ASKED_RE = re.compile(r"\bUser\s+asked\s*:", re.IGNORECASE)
_ERROR_SCAN_BUDGET = 2_000_000   # characters of original text scanned for errors in one ledger
_ERROR_SCAN_MIN_CHARS = 60    # a shorter result cannot hold an error worth a line
_LABEL_CHARS = 120
_STUB_HEAD_CHARS = 400
_REFUSAL_MAX_CHARS = 4000     # a refusal notice is short; do not parse megabytes for one
_MORE_RESERVE = 16            # room for a "- (+NN more)" line
_REDACTED = "<redacted>"
_UNKNOWN = "?"

_SECTION_RE = re.compile(rf"^{re.escape(HEADING)}[^\n]*(?:\n|\Z).*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_FILE_LINE_RE = re.compile(
    r"^- `(?P<path>[^`]+)`: (?P<ops>[^;\n]+); last (?P<op>[a-z]+) (?P<result>ok|FAILED|\?)$", re.MULTILINE,
)
_ERROR_LINE_RE = re.compile(r"^ {2}(?P<line>\S.*)$", re.MULTILINE)
_COMMAND_LINE_RE = re.compile(
    r"^- `(?P<command>[^`]+)` -> (?P<result>exit -?\d+|exit \?|FAILED|not run)(?: \(x(?P<runs>\d+)\))?$",
    re.MULTILINE,
)
_OP_RE = re.compile(r"([a-z]+)(?: x(\d+))?")


@dataclass
class _FileEntry:
    ops: dict[str, int] = field(default_factory=dict)
    last_op: str = ""
    last_result: str = ""
    touched: int = 0          # position of the last touch on the build's clock


@dataclass
class _ErrorEntry:
    line: str
    touched: int = 0


@dataclass
class _CommandEntry:
    runs: int = 0
    result: str = ""
    touched: int = 0


class _Outcome(NamedTuple):
    unknown: bool = False     # only a pruned stub is left: nothing can be said
    refused: bool = False     # an approval or write guard turned the call down
    failed: bool = False
    exit_code: int | None = None


# ---- reading outcomes ------------------------------------------------------------------------
def _json_dict(text: str) -> dict:
    if not text.lstrip().startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _envelope(content: str, store: OriginalStore | None) -> dict:
    """Envelope fields (``exit_code`` / ``error`` / ``success``) of a tool result.

    For a Tameru render: the metadata the store kept with the original, else the render's own meta
    line (the second line, when it is exactly what ``payload.meta_line`` writes: a body that merely
    starts with JSON is not an envelope). For anything else: the result's JSON object.
    """
    header = parse_header(content)
    if header is None:
        return _json_dict(content)
    entry = store.entry(header["ref"]) if store is not None else None
    if entry is not None:
        return entry.meta
    line = content.split("\n", 2)[1] if "\n" in content else ""
    meta = _json_dict(line)
    return meta if meta and meta_line(meta) == line else {}


def _inspect(tool: str, args: dict, content: str, store: OriginalStore | None) -> _Outcome:
    if (
        content.startswith((PRUNED_PLACEHOLDER, DUPLICATE_PREFIX)) or PERSISTED_OUTPUT_TAG in content
        or LEAN_STUB_RE.match(content)
    ):
        return _Outcome(unknown=True)
    if len(content) <= _REFUSAL_MAX_CHARS and refused_summary(tool, args, content) is not None:
        return _Outcome(refused=True)
    envelope = _envelope(content, store)
    code = envelope.get("exit_code")
    head = content[:_STUB_HEAD_CHARS].split("\n", 1)[0] if content.startswith("[") else ""   # a stub's line
    if isinstance(code, bool) or not isinstance(code, int):
        match = _EXIT_RE.search(head)
        code = int(match[1]) if match else None
    failed = bool(envelope.get("error")) or envelope.get("success") is False or " FAILED" in head
    return _Outcome(failed=failed, exit_code=code)


def _original_text(content: str, store: OriginalStore | None) -> str:
    """The text a result's errors are read from: the stored original of a Tameru render, else its own text.

    "" for what holds no original any more (Hermes' stubs and placeholders).
    """
    if (
        content.startswith((PRUNED_PLACEHOLDER, DUPLICATE_PREFIX)) or PERSISTED_OUTPUT_TAG in content
        or LEAN_STUB_RE.match(content)
    ):
        return ""
    header = parse_header(content)
    if header is None:
        return parse_payload(content).inner
    kept = store.get(header["ref"]) if store is not None else None
    return kept if kept is not None else content


def _error_line(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= ERROR_LINE_CHARS else line[:ERROR_LINE_CHARS - 1] + "…"


def _note_errors(
    errors: dict[str, _ErrorEntry], clock: Iterator[int], text: str,
) -> None:
    """Remember the first exemplar of every error fingerprint of ``text`` (a repeat only refreshes its age)."""
    for fp, line in error_fingerprints(text).items():
        entry = errors.get(fp)
        if entry is None:
            shown = _error_line(line)
            if contains_secret(shown):
                continue
            entry = errors[fp] = _ErrorEntry(shown)
        entry.touched = next(clock)


def _file_result(outcome: _Outcome) -> str:
    if outcome.unknown:
        return _UNKNOWN
    return "FAILED" if outcome.failed or outcome.refused else "ok"


def _command_result(outcome: _Outcome) -> str:
    if outcome.refused:
        return "not run"
    if outcome.exit_code is not None:
        return f"exit {outcome.exit_code}"
    return "FAILED" if outcome.failed else "exit ?"


# ---- collecting entries ----------------------------------------------------------------------
def _args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    return _json_dict(raw) if isinstance(raw, str) else {}


def _label(text: str, *, keep_tail: bool = False) -> str:
    """``text`` as one short backtick-free line; a probable credential becomes ``<redacted>``."""
    text = " ".join(text.split()).replace("`", "'")
    if contains_secret(text):
        return _REDACTED
    if len(text) <= _LABEL_CHARS:
        return text
    return "…" + text[-(_LABEL_CHARS - 1):] if keep_tail else text[:_LABEL_CHARS - 1] + "…"


def _paths(tool: str, args: dict) -> list[str]:
    """The files one call named (a V4A ``patch`` can name several)."""
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return [value]
    patch = args.get("patch")
    if tool != "patch" or not isinstance(patch, str):
        return []
    found = (path for m in _V4A_PATH_RE.finditer(patch) for path in m.group("path", "src", "dst") if path)
    return list(dict.fromkeys(found))


def _note_file(
    files: dict[str, _FileEntry], clock: Iterator[int], path: str, op: str, result: str, count: int = 1,
) -> None:
    entry = files.setdefault(path, _FileEntry())
    entry.ops[op] = entry.ops.get(op, 0) + count
    entry.last_op, entry.last_result, entry.touched = op, result, next(clock)


def _note_command(
    commands: dict[str, _CommandEntry], clock: Iterator[int], command: str, result: str, runs: int = 1,
) -> None:
    entry = commands.setdefault(command, _CommandEntry())
    entry.runs += runs
    entry.result, entry.touched = result, next(clock)


def _seed(
    previous: str, files: dict[str, _FileEntry], commands: dict[str, _CommandEntry],
    errors: dict[str, _ErrorEntry], clock: Iterator[int],
) -> None:
    """Entries of the Artifact Trail section of an earlier summary (our own line grammar).

    Its lines read oldest first, which is how the clock ranks them against the new turns.
    """
    section = _SECTION_RE.search(previous)
    if section is None:
        return
    text = section.group(0)
    for m in _FILE_LINE_RE.finditer(text):
        for op, count in _OP_RE.findall(m["ops"]):
            _note_file(files, clock, m["path"], op, m["result"], int(count or 1))
        files[m["path"]].last_op = m["op"]
    for m in _COMMAND_LINE_RE.finditer(text):
        _note_command(commands, clock, m["command"], m["result"], int(m["runs"] or 1))
    marker = text.find("\nErrors:\n")
    for m in _ERROR_LINE_RE.finditer(text[marker + 1:] if marker >= 0 else ""):
        _note_errors(errors, clock, m["line"])


# ---- rendering -------------------------------------------------------------------------------
def _plain(line: str) -> str:
    return _USER_ASKED_RE.sub("User asked -", line)


def _file_line(path: str, entry: _FileEntry) -> tuple[int, str]:
    ops = ", ".join(op if count == 1 else f"{op} x{count}" for op, count in entry.ops.items())
    return entry.touched, _plain(f"- `{path}`: {ops}; last {entry.last_op} {entry.last_result}")


def _command_line(command: str, entry: _CommandEntry) -> tuple[int, str]:
    runs = f" (x{entry.runs})" if entry.runs > 1 else ""
    return entry.touched, _plain(f"- `{command}` -> {entry.result}{runs}")


def _size(lines: list[str]) -> int:
    return sum(len(line) + 1 for line in lines)


def _fit(items: list[tuple[int, str]], budget: int) -> list[str]:
    """The lines of ``items`` (``(touched, line)``) within ``budget`` chars, in their order.

    When they do not all fit the most recently touched win, and a ``(+N more)`` line counts the rest.
    """
    if _size([line for _, line in items]) <= budget:
        return [line for _, line in items]
    keep: set[int] = set()
    used = 0
    for i in sorted(range(len(items)), key=lambda i: -items[i][0]):
        if used + len(items[i][1]) + 1 <= budget - _MORE_RESERVE:
            keep.add(i)
            used += len(items[i][1]) + 1
    lines = [line for i, (_, line) in enumerate(items) if i in keep]
    more = f"- (+{len(items) - len(keep)} more)"
    return [*lines, more] if used + len(more) + 1 <= budget else lines


def build_ledger(
    turns: list[dict], store: OriginalStore | None = None, *, max_chars: int = MAX_CHARS,
    previous: str = "", skip_tools: frozenset[str] = frozenset(), extra_originals: Iterable[str] = (),
) -> str:
    """The Artifact Trail section for ``turns`` (the messages being summarized), or "".

    ``store`` supplies the originals of Tameru-rendered rows. ``previous`` is the summary from
    before ``turns`` (Hermes' ``_previous_summary``, which does not yet account for them): the
    entries of its Artifact Trail section come first, so the trail accumulates across compactions.
    ``skip_tools`` names tools whose results are not scanned for errors (instructions, memory);
    ``extra_originals`` are texts of results outside ``turns`` whose bodies are gone (also scanned).
    Files and Commands never exceed ``max_chars``, the Errors lines ``ERRORS_CHARS``.
    """
    files: dict[str, _FileEntry] = {}
    commands: dict[str, _CommandEntry] = {}
    errors: dict[str, _ErrorEntry] = {}
    clock = itertools.count()
    _seed(previous, files, commands, errors, clock)
    scan_room = _ERROR_SCAN_BUDGET

    def scan(text: str) -> None:
        nonlocal scan_room
        if text and len(text) <= scan_room:
            scan_room -= len(text)
            _note_errors(errors, clock, text)

    for text in extra_originals:
        scan(text)
    calls: dict[str, tuple[str, Any]] = {}
    for msg in turns:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            calls.update(tool_calls_by_id([msg]))
        elif msg.get("role") == "tool" and msg.get("tool_call_id"):
            tool, raw_args = calls.get(str(msg["tool_call_id"]), ("", None))
            content = text_of(msg.get("content"))
            if tool not in skip_tools and len(content) >= _ERROR_SCAN_MIN_CHARS:
                scan(_original_text(content, store))
            if tool != "terminal" and tool not in _FILE_OPS:
                continue
            args = _args(raw_args)
            outcome = _inspect(tool, args, content, store)
            if tool == "terminal":
                command = args.get("command")
                if isinstance(command, str) and command.strip():
                    _note_command(commands, clock, _label(command), _command_result(outcome))
            else:
                for path in _paths(tool, args):
                    _note_file(
                        files, clock, _label(path, keep_tail=True), _FILE_OPS[tool], _file_result(outcome),
                    )

    sections = [
        ("Files:", [_file_line(path, entry) for path, entry in files.items()]),
        ("Commands:", [_command_line(command, entry) for command, entry in commands.items()]),
    ]
    sections = [(title, lines) for title, lines in sections if lines]
    room = max_chars - len(HEADING) - 1 - sum(len(title) + 1 for title, _ in sections)
    out: list[str] = []
    if sections and room > _MORE_RESERVE:
        # An earlier section may take 60% of the room (all of what the later ones leave unused).
        for i, (title, items) in enumerate(sections):
            later = sum(_size([line for _, line in rest]) for _, rest in sections[i + 1:])
            budget = room if i == len(sections) - 1 else max(room * 3 // 5, room - later)
            kept = _fit(items, budget)
            room -= _size(kept)
            if kept:
                out += [title, *kept]
    error_items = [(entry.touched, _plain(f"  {entry.line}")) for entry in errors.values()]
    if error_items and ERRORS_CHARS > len("Errors:") + 1 + _MORE_RESERVE:
        kept = _fit(error_items, ERRORS_CHARS - len("Errors:") - 1)
        if kept:
            out += ["Errors:", *kept]
    return "\n".join([HEADING, *out]) if out else ""


def merge_into_summary(summary: str, ledger: str) -> str:
    """``summary`` with ``ledger`` as its one Artifact Trail section.

    An existing section (up to the next ``## `` heading) is replaced in place and any repeats are
    dropped; without one the ledger is appended. An empty ``ledger`` leaves the summary alone.
    """
    if not ledger:
        return summary
    ledger = ledger.strip("\n")
    replaced = False

    def swap(match: re.Match[str]) -> str:
        nonlocal replaced
        if replaced:
            return ""
        replaced = True
        text = match.group(0)
        return ledger + text[len(text.rstrip()):]

    merged = _SECTION_RE.sub(swap, summary)
    if replaced:
        return merged
    base = summary.rstrip("\n")
    return f"{base}\n\n{ledger}" if base else ledger
