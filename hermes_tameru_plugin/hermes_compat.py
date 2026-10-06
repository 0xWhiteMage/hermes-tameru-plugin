"""Guarded access to the Hermes internals the plugin depends on.

Each helper resolves its Hermes counterpart lazily, at call time, and falls back to a local copy
that is byte-identical to Hermes at the pinned SHA (``tests/test_hermes_compat.py`` compares the
two on samples). That keeps the module import-safe in Hermes' eager directory loader (stdlib only,
no import of the package ``__init__``, nothing touched at import time, nothing raises when Hermes
is absent) and lets the test stub and older Hermes releases behave like the real thing.

``probe_seams`` reports whether the private seams the engine hooks still have the shape the
engine relies on; it is called with Hermes' ``ContextCompressor``, not with the plugin subclass
(which overrides the seam).
"""
from __future__ import annotations

import importlib
import inspect
import json
import re
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

# ---- constants (pinned against Hermes by tests/test_hermes_compat.py) ------------------------
MARKER_PREFIX = "⟪HERMES-CONTEXT-COMPRESSION:"
SKILL_PRUNED_MARKER_PREFIX = "[SKILL_PRUNED:"
PERSISTED_OUTPUT_TAG = "<persisted-output>"
DUPLICATE_PREFIX = "[Duplicate tool output"
PRUNED_PLACEHOLDER = "[Old tool output cleared to save context space]"
TOOL_CALL_UID_KEY = "_tool_call_uid"
MESSAGE_UID_KEY = "message_uid"

# What ``_lean_recovery_stub`` renders: ``[<tool> output demoted at compaction — N chars preserved
# in session history.[ Recover with session_search(query=..., session_id='<id>')]]``.
LEAN_STUB_RE = re.compile(
    r"^\[[^\n]*? output demoted at compaction — \d[\d,]* chars preserved in session history\."
    r"(?: Recover with session_search\(query=\.\.\., session_id='[^\n]*?'\))?\]$"
)

_PRUNE_MIN_CHARS = 200
_ELISION_MARKER_TEMPLATE = (
    MARKER_PREFIX + " {omitted:,} of {total:,} chars omitted here by Hermes's context compressor.⟫"
)
_ARTIFACT_RE = re.compile(re.escape(MARKER_PREFIX) + r"\s+\d[\d,]*")
_SYNTHETIC_USER_ROW_PREFIXES = (
    "[System:", "[CONTEXT", "[PRIOR CONTEXT", "[IMPORTANT: Background", "[Your active task list",
    "[Planning state preserved", "[ASYNC DELEGATION", "[OUT-OF-BAND", "Cronjob Response:",
)
# Heads shared by every handoff prefix Hermes has shipped (current, historical and legacy).
_SUMMARY_HEADS = ("[CONTEXT COMPACTION — REFERENCE ONLY]", "[CONTEXT SUMMARY]:")
_MERGED_SUMMARY_DELIMITER = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"


# ---- lazy resolution -------------------------------------------------------------------------
def _module(name: str) -> Any:
    """The Hermes module ``name``, or None when it cannot be imported.

    Nothing is cached: ``sys.modules`` already memoizes successes, and a failure may be transient
    (a circular import while Hermes is still initialising) or undone later (a swapped stub).
    """
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _hermes(module: str, attr: str) -> Any:
    """``module.attr`` (``attr`` may be dotted) looked up at call time, or None when unavailable."""
    obj = _module(module)
    for part in attr.split("."):
        obj = getattr(obj, part, None)
    return obj


def _prefer(module: str, attr: str, fallback: Callable[..., Any]) -> Callable[..., Any]:
    """Hermes' ``module.attr`` when it is callable, else ``fallback``."""
    found = _hermes(module, attr)
    return found if callable(found) else fallback


# ---- compression marker ----------------------------------------------------------------------
def _local_elision_marker(omitted: int, total: int) -> str:
    """Local mirror of ``agent.compression_marker._elision_marker``."""
    return _ELISION_MARKER_TEMPLATE.format(omitted=omitted, total=total)


_LOCAL_MARKER_MAX_LEN = len(_local_elision_marker(10**12 - 1, 10**12 - 1))


def elision_marker(omitted: int, total: int) -> str:
    """Hermes' non-imitable elision marker with per-instance counts."""
    mint = _prefer("agent.compression_marker", "_elision_marker", _local_elision_marker)
    return mint(omitted, total)


def marker_len_reserve() -> int:
    """Width of the widest marker (counts under ~1 TB), for callers budgeting around gaps."""
    found = _hermes("agent.compression_marker", "ELISION_MARKER_MAX_LEN")
    return found if isinstance(found, int) else _LOCAL_MARKER_MAX_LEN


def marker_artifact_re() -> re.Pattern[str]:
    """Hermes' matcher for a minted marker (or a copy cut after its first count)."""
    found = _hermes("agent.compression_marker", "_COMPRESSION_MARKER_ARTIFACT_RE")
    return found if isinstance(found, re.Pattern) else _ARTIFACT_RE


# ---- row and summary helpers -----------------------------------------------------------------
def _local_is_summary_stub(content: str) -> bool:
    """Local mirror of ``agent.context_compressor._is_summary_stub``."""
    return content.startswith("[") and " chars)" in content and len(content) < 400


def is_summary_stub(content: str) -> bool:
    """True for a tool result already replaced by a 1-line ``[tool] ... (N chars)`` summary."""
    if not isinstance(content, str):
        return False
    return _prefer("agent.context_compressor", "_is_summary_stub", _local_is_summary_stub)(content)


def _local_drop_stale_api_content(msg: dict) -> None:
    """Local mirror of ``agent.turn_context.drop_stale_api_content``."""
    msg.pop("api_content", None)


def drop_stale_api_content(msg: dict) -> None:
    """Drop the ``api_content`` sidecar from a row whose content was rewritten, in place."""
    _prefer("agent.turn_context", "drop_stale_api_content", _local_drop_stale_api_content)(msg)


def _local_summarize_tool_result(tool_name: str, tool_args: Any, content: str) -> str:
    """Local mirror of Hermes' generic ``_summarize_tool_result`` fallback line."""
    size = len(content) if isinstance(content, str) else 0
    return f"[{tool_name}] ({size:,} chars result)"


def summarize_tool_result(tool_name: str, tool_args: Any, content: str) -> str:
    """Hermes' 1-line summary of a tool call and result; a bare size line without Hermes."""
    return _prefer(
        "agent.context_compressor", "_summarize_tool_result", _local_summarize_tool_result,
    )(tool_name, tool_args, content)


def _local_json_dict(text: Any) -> dict:
    """Local mirror of ``agent.context_compressor._json_dict`` (also survives pathological JSON)."""
    try:
        parsed = json.loads(text) if text else {}
    except (ValueError, TypeError, RecursionError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _local_str_arg(args: dict, key: str) -> str:
    """Local mirror of ``agent.context_compressor._str_arg`` (no default argument needed here)."""
    val = args.get(key, "")
    return val if isinstance(val, str) else "" if val is None else str(val)


def _local_refused_summary(tool_name: str, args: dict, content: str) -> str | None:
    """Local mirror of ``agent.context_compressor._summarize_refused_tool_result``."""
    payload = _local_json_dict(content)
    status = payload.get("status")
    error = payload.get("error") if isinstance(payload.get("error"), str) else ""
    if not error and content.lstrip().startswith("BLOCKED"):
        error = content.strip()
    if status == "pending_approval":
        outcome = "awaiting the user's approval, not run"
    elif error and (status == "blocked" or error.lstrip().startswith("BLOCKED")):
        outcome = "BLOCKED, not run"
        if "NOT consented" in error:
            outcome += (
                "; the user did NOT consent, do not retry or reach the same outcome another way"
            )
    else:
        return None
    target = _local_str_arg(args, "command") or _local_str_arg(args, "path")
    target = f" `{target if len(target) <= 60 else target[:57] + '...'}`" if target else ""
    return f"[{tool_name}]{target} {outcome}"[:_PRUNE_MIN_CHARS - 1]


def refused_summary(tool_name: str, args: dict, content: str) -> str | None:
    """Hermes' summary of a call an approval or write guard refused, else None."""
    if not isinstance(content, str):
        return None
    args = args if isinstance(args, dict) else {}
    summarize = _prefer(
        "agent.context_compressor", "_summarize_refused_tool_result", _local_refused_summary,
    )
    try:
        return summarize(tool_name, args, content)
    except Exception:  # Hermes' JSON probe is unguarded (a 5,000-digit "number" raises)
        return _local_refused_summary(tool_name, args, content)


def _local_result_failure_suffix(content: str) -> str:
    """Local mirror of ``agent.context_compressor._result_failure_suffix``."""
    payload = _local_json_dict(content)
    error = payload.get("error")
    if not error and payload.get("success") is not False:
        return ""
    preview = " ".join(str(error).split())[:80] if error else ""
    return f" FAILED: {preview}" if preview else " FAILED"


def result_failure_suffix(content: str) -> str:
    """`` FAILED: <error>`` for a payload with a top-level ``error`` or ``success: false``."""
    suffix = _prefer(
        "agent.context_compressor", "_result_failure_suffix", _local_result_failure_suffix,
    )
    try:
        return suffix(content)
    except Exception:  # Hermes' JSON probe is unguarded (a 5,000-digit "number" raises)
        return _local_result_failure_suffix(content)


def _tc_get(obj: Any, key: str, default: Any = "") -> Any:
    """Field of a dict- or object-shaped tool call (or its ``function`` sub-object)."""
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _local_tool_calls_by_id(messages: list[dict]) -> dict[str, tuple[str, Any]]:
    """Local mirror of ``agent.context_compressor._tool_calls_by_id``."""
    out: dict[str, tuple[str, Any]] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            fn = _tc_get(tc, "function", {})
            call = (_tc_get(fn, "name", "unknown"), _tc_get(fn, "arguments"))
            out[_tc_get(tc, "id") or ""] = call
    return out


def tool_calls_by_id(messages: list[dict]) -> dict[str, tuple[str, Any]]:
    """Map ``tool_call_id -> (tool_name, raw_arguments)`` over every assistant tool call."""
    calls = _prefer("agent.context_compressor", "_tool_calls_by_id", _local_tool_calls_by_id)
    return calls(messages)


def _local_is_synthetic_user_row(content: str) -> bool:
    """Local mirror of ``agent.context_compressor._synthetic_user_row``."""
    if not isinstance(content, str) or not content.strip():
        return True
    return content.lstrip().startswith(_SYNTHETIC_USER_ROW_PREFIXES)


def is_synthetic_user_row(content: str) -> bool:
    """True for scaffolding user rows (and blank ones) that carry no real user words."""
    return _prefer(
        "agent.context_compressor", "_synthetic_user_row", _local_is_synthetic_user_row,
    )(content)


def _local_is_compaction_summary(content: str) -> bool:
    """Local mirror of ``ContextCompressor.classify_summary_content`` (standalone or merged).

    Matches the head every shipped handoff prefix starts with instead of copying each full prefix,
    so it is a superset of Hermes' check.
    """
    text = content.lstrip()
    if _MERGED_SUMMARY_DELIMITER in text:
        text = text.split(_MERGED_SUMMARY_DELIMITER, 1)[1].lstrip()
    return text.startswith(_SUMMARY_HEADS)


def is_compaction_summary(content: str) -> bool:
    """True for Hermes' compaction handoff row, standalone or merged into preserved content."""
    if not isinstance(content, str):
        return False
    classify = _hermes("agent.context_compressor", "ContextCompressor.classify_summary_content")
    if callable(classify):
        return classify(content) is not None
    return _local_is_compaction_summary(content)


def compression_threshold(model: str, provider: str, cfg: dict) -> float | None:
    """Hermes' compaction threshold for ``model`` (with its Codex autoraise), or None on failure.

    ``cfg`` is the merged ``compression`` config section.
    """
    resolve = _hermes("agent.agent_init", "_compression_threshold")
    if not callable(resolve):
        return None
    try:
        return float(resolve(SimpleNamespace(model=model, provider=provider), cfg)[0])
    except Exception:
        return None


def session_db_usable(db: Any) -> bool:
    """True when ``db`` can read history and commit a compaction.

    An isolation proxy (``plugins.isolation: host`` hands the engine an ``Opaque`` placeholder)
    raises on attribute access and so fails the capability check; the class-name test catches a
    proxy that merely answers it.
    """
    if db is None or "Opaque" in type(db).__name__:
        return False
    try:
        needed = ("get_messages", "archive_and_compact")
        return all(callable(getattr(db, name, None)) for name in needed)
    except Exception:
        return False


# ---- seam probe ------------------------------------------------------------------------------
@dataclass(frozen=True)
class SeamReport:
    """Which Hermes seams the engine can hook; ``reasons`` explains each one that cannot be.

    ``demote`` is seam A (``_demote_tool_result_at``), ``pressure`` the pass-4 wrapper,
    ``lean_tail`` seam B (``_demote_stale_tail_tools``), ``augment`` ``_augment_summary_lean`` and
    ``compress_kw`` ``compress(bypass_cooldown=...)``. All False is the stock-behaviour report.
    """

    demote: bool = False
    pressure: bool = False
    lean_tail: bool = False
    augment: bool = False
    compress_kw: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """The ladder needs seam A; the other seams only add to it."""
        return self.demote


_DEMOTE_SEAM = "_demote_tool_result_at"
_DEMOTE_PARAMS = ("result", "idx", "call_id_to_tool", "min_prune_chars")


def _code_of(obj: Any) -> types.CodeType | None:
    """Code object behind a function, staticmethod or ``functools.wraps`` chain, else None."""
    try:
        code = inspect.unwrap(getattr(obj, "__func__", obj)).__code__
    except (AttributeError, ValueError):
        return None
    return code if isinstance(code, types.CodeType) else None


def _reaches(obj: Any, name: str) -> bool:
    """True when ``name`` is a global/attribute name used by ``obj`` or by any code nested in it."""
    code = _code_of(obj)
    stack = [code] if code else []
    while stack:
        code = stack.pop()
        if name in code.co_names:
            return True
        stack.extend(c for c in code.co_consts if isinstance(c, types.CodeType))
    return False


def _param_names(obj: Any) -> list[str]:
    return list(inspect.signature(getattr(obj, "__func__", obj)).parameters)


def _check_demote(cls: type) -> str | None:
    seam = inspect.getattr_static(cls, _DEMOTE_SEAM, None)
    if seam is None:
        return f"{_DEMOTE_SEAM} is missing"
    if not isinstance(seam, staticmethod):
        return f"{_DEMOTE_SEAM} is no longer a staticmethod"
    params = _param_names(seam)
    if tuple(params[:4]) != _DEMOTE_PARAMS:
        return f"{_DEMOTE_SEAM} parameters changed: {params}"
    if not _reaches(inspect.getattr_static(cls, "_prune_old_tool_results", None), _DEMOTE_SEAM):
        return f"_prune_old_tool_results is missing or no longer calls {_DEMOTE_SEAM}"
    return None


def _check_pressure(cls: type) -> str | None:
    if not _reaches(inspect.getattr_static(cls, "_pressure_demote_tail", None), _DEMOTE_SEAM):
        return f"_pressure_demote_tail is missing or no longer calls {_DEMOTE_SEAM}"
    return None


def _check_method(cls: type, name: str, params: tuple[str, str]) -> str | None:
    """``name`` exists and takes ``params`` right after ``self``."""
    func = getattr(cls, name, None)
    if not callable(func):
        return f"{name} is missing"
    got = tuple(_param_names(func)[1:3])
    return None if got == params else f"{name} parameters changed: {got}"


def _check_lean_tail(cls: type) -> str | None:
    return _check_method(cls, "_demote_stale_tail_tools", ("messages", "tail_start"))


def _check_augment(cls: type) -> str | None:
    return _check_method(cls, "_augment_summary_lean", ("summary", "turns_to_summarize"))


def _check_compress(cls: type) -> str | None:
    func = getattr(cls, "compress", None)
    if not callable(func) or "bypass_cooldown" not in _param_names(func):
        return "compress does not accept bypass_cooldown"
    return None


_SEAM_CHECKS: tuple[tuple[str, Callable[[type], str | None]], ...] = (
    ("demote", _check_demote),
    ("pressure", _check_pressure),
    ("lean_tail", _check_lean_tail),
    ("augment", _check_augment),
    ("compress_kw", _check_compress),
)


def probe_seams(cls: type) -> SeamReport:
    """Check ``cls`` (Hermes' ``ContextCompressor``) for the engine's seams; never raises."""
    flags: dict[str, bool] = {}
    reasons: list[str] = []
    for field, check in _SEAM_CHECKS:
        try:
            problem = check(cls)
        except Exception as exc:  # a probe must never break agent init
            problem = f"{field} probe failed: {exc!r}"
        flags[field] = problem is None
        if problem:
            reasons.append(problem)
    return SeamReport(**flags, reasons=tuple(reasons))
