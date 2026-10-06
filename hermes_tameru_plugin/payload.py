"""Hermes tool-result envelopes: payload text, the metadata line and content hints.

Hermes tools answer with a JSON object (``{"output": ..., "exit_code": 1, "error": null}``,
``{"content": ..., "total_lines": 220, ...}``, ``{"total_count": 12, "matches_text": ...}``).
Compressing the text inside would drop the envelope, yet Hermes' own summarizers read it back from a
row: ``"exit_code"\\s*:\\s*(-?\\d+)``, ``"total_count"\\s*:`` and a JSON parse for ``error`` /
``success``. ``meta_line`` therefore re-emits the envelope as one line of compact JSON that those
readers still understand; ``content_hint`` tells the engine what kind of text the payload is.

Import-safe: stdlib and the vendored engine only, nothing happens at import time.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any

from .tameru.compress_context import unwrap_tool_payload

META_KEYS = (
    "exit_code", "error", "success", "status", "total_lines", "total_count", "truncated", "path",
)

_STR_LIMITS = (120, 60, 30)   # string values are cut to the first limit that fits ``max_chars``
_SNIFF_CHARS = 2000
_GUTTER_RE = re.compile(r"^\s*\d+(?:\t|\|)")   # `cat -n` ("   12\t...") or `N|`
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")
_ENV_ASSIGN_RE = re.compile(r"[A-Za-z_]\w*=")
_PYTHON_RE = re.compile(r"python[\d.]*|py")
_WRAPPERS = frozenset({"sudo", "time", "nohup", "env", "command", "exec", "npx", "bunx"})
_RUNNERS = frozenset({"uv", "poetry", "pdm", "pipenv", "hatch"})   # "<runner> run <program> ..."
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_UNIFIED_FLAG_RE = re.compile(r"-[A-Za-z]*[uU]\d*[A-Za-z]*")
_GREP_FLAG_RE = re.compile(r"-[A-Za-z]*[nrR][A-Za-z]*|--(?:line-number|recursive|dereference-recursive)")
_NUMBER_FLAG_RE = re.compile(r"-[A-Za-z]*n[A-Za-z]*|--number")
_TEST_PROGRAMS = frozenset({"pytest", "py.test", "jest", "vitest"})
_CONTAINER_PROGRAMS = frozenset({"docker", "docker-compose", "podman", "kubectl", "oc"})


@dataclass(frozen=True)
class HermesPayload:
    """A tool result split into payload text and envelope.

    ``inner``    the payload text (the whole result when there is no envelope text to split off)
    ``field``    ``"content"`` or ``"output"`` when ``inner`` was unwrapped, else None
    ``meta``     the scalar top-level fields of the result other than ``field`` (for a result that
                 was not unwrapped: its ``META_KEYS`` fields)
    ``wrapped``  True when ``inner`` was unwrapped from a JSON object
    """

    inner: str
    field: str | None
    meta: dict
    wrapped: bool


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _meta_fields(content: str) -> dict:
    """The ``META_KEYS`` scalars of a JSON object result ({} for anything else)."""
    text = content.strip() if isinstance(content, str) else ""
    if not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {key: parsed[key] for key in META_KEYS if key in parsed and _is_scalar(parsed[key])}


def parse_payload(content: str) -> HermesPayload:
    """Split ``content`` with the engine's ``unwrap_tool_payload``.

    A JSON object without a ``content`` / ``output`` text longer than 200 chars (``search_files``,
    ``patch``, a short result) stays whole in ``inner``, but its ``META_KEYS`` fields
    (``total_count``, ``success``, ``exit_code``, ...) still land in ``meta``.
    """
    payload = unwrap_tool_payload(content)
    meta = payload.meta if payload.wrapped else _meta_fields(content)
    return HermesPayload(payload.inner, payload.field, dict(meta), payload.wrapped)


# ---- metadata line ---------------------------------------------------------------------------
def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit - 1] + "…"
    return value


def _dump(items: dict, str_limit: int) -> str:
    clipped = {key: _clip(value, str_limit) for key, value in items.items()}
    return json.dumps(clipped, separators=(",", ":"), ensure_ascii=False)


def meta_line(meta: dict, *, max_chars: int = 300) -> str:
    """The ``META_KEYS`` present in ``meta`` as one line of compact JSON, in ``META_KEYS`` order.

    Hermes' ``"exit_code"`` regex and its JSON parsers read the line as they read the original
    envelope. Long strings are cut (120 chars, then shorter while the line exceeds ``max_chars``);
    when that is not enough the trailing keys are dropped. "" when no key applies.
    """
    items = {key: meta[key] for key in META_KEYS if key in meta and _is_scalar(meta[key])}
    while items:
        for limit in _STR_LIMITS:
            line = _dump(items, limit)
            if len(line) <= max_chars:
                return line
        items.popitem()
    return ""


# ---- content hints ---------------------------------------------------------------------------
def _has_gutter(text: str) -> bool:
    """True when the first non-blank lines all start with a line-number gutter."""
    head = text[:_SNIFF_CHARS].split("\n")
    if len(text) > _SNIFF_CHARS:
        head = head[:-1]   # the last line may be cut
    lines = [line for line in head if line.strip()][:5]
    return bool(lines) and all(_GUTTER_RE.match(line) for line in lines)


def _strip_launchers(tokens: list[str]) -> list[str]:
    """``tokens`` without leading ``VAR=x``, ``sudo``, ``uv run``, ``python -m`` and the like."""
    while tokens:
        head = tokens[0]
        if head in _WRAPPERS or _ENV_ASSIGN_RE.match(head):
            tokens = tokens[1:]
        elif head in _RUNNERS and tokens[1:2] == ["run"]:
            tokens = tokens[2:]
        elif _PYTHON_RE.fullmatch(os.path.basename(head)) and tokens[1:2] == ["-m"]:
            tokens = tokens[2:]
        else:
            break
    return tokens


def _programs(command: str) -> list[tuple[str, list[str]]]:
    """``(program, args)`` of each simple command of a shell line, the last one first (the last
    command of a pipeline is the one whose output the result holds)."""
    found = []
    for segment in _SEGMENT_SPLIT_RE.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        tokens = _strip_launchers(tokens)
        if tokens:
            found.append((os.path.basename(tokens[0]), tokens[1:]))
    return found[::-1]


def _git_subcommand(args: list[str]) -> str:
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in _GIT_VALUE_OPTIONS:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return ""


def _program_hint(program: str, args: list[str], inner: str) -> str | None:
    flags = [arg for arg in args if arg.startswith("-")]
    if program == "git":
        return "diff" if _git_subcommand(args) in ("diff", "show") else None
    if program == "diff":
        return "diff" if any(_UNIFIED_FLAG_RE.fullmatch(f) or f.startswith("--unified") for f in flags) else None
    if program == "rg" or (
        program in ("grep", "egrep", "fgrep") and any(_GREP_FLAG_RE.fullmatch(f) for f in flags)
    ):
        return "grep"
    if (
        program in _TEST_PROGRAMS
        or (program in ("go", "cargo") and args[:1] == ["test"])
        or (program in ("npm", "yarn", "pnpm", "bun") and "test" in args[:2])
    ):
        return "test"
    if (
        program == "journalctl"
        or (program in _CONTAINER_PROGRAMS and "logs" in args[:5])
        or (program == "tail" and any(arg.endswith(".log") for arg in args))
    ):
        return "log"
    if program == "nl" or (program == "cat" and any(_NUMBER_FLAG_RE.fullmatch(f) for f in flags)):
        return "numbered_code" if _has_gutter(inner) else None
    return None


def content_hint(tool_name: str, args: dict, inner: str) -> str | None:
    """The engine's ``content_hint`` for a tool result: what kind of text ``inner`` is, else None.

    ``read_file`` / ``cat -n`` / ``nl`` give ``numbered_code`` when ``inner`` really starts with a
    line-number gutter; ``search_files`` and ``rg`` / ``grep -n|-r`` give ``grep``; ``git diff|show``
    and ``diff -u`` give ``diff``; test runners give ``test``; ``docker|kubectl logs``,
    ``journalctl`` and ``tail`` of a ``*.log`` give ``log``; ``web_extract`` gives ``text``.
    """
    if tool_name == "read_file":
        return "numbered_code" if _has_gutter(inner) else None
    if tool_name == "search_files":
        return "grep"
    if tool_name == "web_extract":
        return "text"
    if tool_name != "terminal" or not isinstance(args, dict):
        return None
    command = args.get("command")
    if not isinstance(command, str):
        return None
    for program, program_args in _programs(command):
        hint = _program_hint(program, program_args, inner)
        if hint:
            return hint
    return None
