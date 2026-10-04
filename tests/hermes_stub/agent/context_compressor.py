# Derived from Hermes Agent (https://github.com/NousResearch/hermes-agent),
# pinned at SHA 1298c8e74baa73e1a2b90124228d017261ac6bc4.
#
# MIT License
#
# Copyright (c) 2025 Nous Research
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Signatures, names and the skip rules of the pruning seam mirror Hermes Agent
# (agent/context_compressor.py). A few small helpers are copied verbatim (``_is_summary_stub``,
# ``_lean_recovery_stub``, ``_tc_get``, ``_json_dict``, ``_tool_calls_by_id``, ``_skill_view_call_sites``,
# ``_collect_protected_skill_names``, ``_dedupe_tool_results``, ``_prune_boundary``, ``_tail_soft_ceiling``,
# the image-stripping helpers, ``_pending_tool_round``); every other body is a simplified
# re-implementation, not a copy.
"""Stub of ``agent.context_compressor`` (tests only; used when real Hermes is absent).

Reproduces the calling conventions the Tameru plugin relies on, NOT Hermes' behaviour:

* ``_demote_tool_result_at`` is a ``@staticmethod`` called through ``self.`` — with 5 positional
  args from pass 2 of ``_prune_old_tool_results`` and with 4 from the nested ``_shrink_at`` of
  ``_pressure_demote_tail`` (pass 4). Same skip rules as Hermes, but a simplified one-line summary.
* ``prune_tool_results_only`` has the proactive / min-reclaim gates and returns the INPUT object
  on a no-op.
* ``compress`` prunes with ``protect_tail_tokens``, writes a deterministic fake summary and
  returns head + summary + tail.

The real-Hermes CI job (and ``tests/test_seam_contract.py``) is what guards the true contract.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

from agent.context_engine import ContextEngine

logger = logging.getLogger(__name__)

_PRUNED_TOOL_PLACEHOLDER = "[Old tool output cleared to save context space]"
_PRUNE_MIN_CHARS = 200
_CHARS_PER_TOKEN = 4
_MAX_TAIL_MESSAGE_FLOOR = 8
_PRESSURE_KEEP_RECENT_MESSAGES = 3
_LEAN_TAIL_KEEP_TOOL_ROUNDS = 6
_LEAN_TAIL_DEMOTE_MIN_CHARS = 1_500
_SKILL_PRUNE_RECENT_WINDOW = 10
TAIL_MAX_CONTEXT_FRACTION = 0.20
LEAN_TAIL_FLOOR_TOKENS = 10_000
LEAN_TAIL_CAP_TOKENS = 25_000
SKILL_PRUNED_MARKER_PREFIX = "[SKILL_PRUNED:"
COMPRESSED_SUMMARY_METADATA_KEY = "_compressed_summary"
SUMMARY_PREFIX = "[CONTEXT COMPACTION — REFERENCE ONLY] "
_SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
_LEAN_USER_MESSAGES_HEADING = "## User Messages (verbatim, newest first)"
_LEAN_RECOVERY_HEADING = "## Context Recovery"
_LEAN_ANCHOR_HEADING = "## Anchor Index (mechanically extracted, exact)"
_IMAGE_PART_TYPES = frozenset({"image_url", "input_image", "image"})
STEER_DISPLAY_KIND = "steer"  # agent.prompt_builder.STEER_DISPLAY_KIND
PROACTIVE_PRUNE_REARM_MODEL_CONFIG_KEY = "_proactive_prune_rearm_tokens"


def _is_summary_stub(content: str) -> bool:
    """True for a tool result already replaced by a 1-line ``[tool] ... (N chars)`` summary."""
    return content.startswith("[") and " chars)" in content and len(content) < 400


def _lean_recovery_stub(tool_name: str, content_len: int, session_id: str) -> str:
    """One-line replacement for a demoted tail tool result."""
    hint = f" Recover with session_search(query=..., session_id='{session_id}')" if session_id else ""
    return (
        f"[{tool_name or 'tool'} output demoted at compaction — {content_len:,} "
        f"chars preserved in session history.{hint}]"
    )


def _tc_get(obj: Any, key: str, default: Any = "") -> Any:
    """Field of a dict- or object-shaped tool call (or its ``function`` sub-object)."""
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _tool_calls_by_id(messages: List[Dict[str, Any]]) -> Dict[str, tuple]:
    """Map ``tool_call_id -> (tool_name, raw_arguments)`` over every assistant tool call."""
    out: Dict[str, tuple] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            fn = _tc_get(tc, "function", {})
            out[_tc_get(tc, "id") or ""] = (_tc_get(fn, "name", "unknown"), _tc_get(fn, "arguments"))
    return out


def _json_dict(text: Any) -> dict:
    """Parse ``text`` as a JSON object; ``{}`` for empty, invalid, or non-object input."""
    try:
        parsed = json.loads(text) if text else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _rewritten(msg: Dict[str, Any], content: Any) -> Dict[str, Any]:
    """Copy of ``msg`` carrying ``content``; drops the stale ``api_content`` sidecar."""
    from agent.turn_context import drop_stale_api_content

    new_msg = {**msg, "content": content}
    drop_stale_api_content(new_msg)
    return new_msg


def _is_image_part(part: Any) -> bool:
    """True if ``part`` is an image block (``image_url``, ``input_image``, or ``image``)."""
    return isinstance(part, dict) and part.get("type") in _IMAGE_PART_TYPES


def _replace_image_parts(parts: Any, placeholder: str) -> Optional[List[Any]]:
    """New parts list with every image part replaced by a text placeholder; None if no images."""
    if not isinstance(parts, list) or not any(_is_image_part(p) for p in parts):
        return None
    return [{"type": "text", "text": placeholder} if _is_image_part(p) else p for p in parts]


def _strip_images_from_tool_msg(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Copy of a tool message with image payloads replaced (stale ``api_content`` dropped); ``None`` if nothing to strip."""
    content = msg.get("content")
    if isinstance(content, dict) and content.get("_multimodal"):
        summary = content.get("text_summary") or "[screenshot removed to save context]"
        return _rewritten(msg, f"[screenshot removed] {str(summary)[:200]}")
    stripped = _replace_image_parts(content, "[screenshot removed to save context]")
    return None if stripped is None else _rewritten(msg, stripped)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def _estimate_msg_budget_tokens(msg: dict, charge_stale_thinking: bool = True) -> int:
    """Rough token estimate for one message (chars/4 over content and the tool-call envelope)."""
    content = _content_text(msg.get("content") or "")
    tokens = (len(content) + 3) // _CHARS_PER_TOKEN + 10  # ceil, like estimate_tokens_rough (ASCII)
    tokens += sum((len(str(tc)) + 3) // _CHARS_PER_TOKEN for tc in msg.get("tool_calls") or [] if isinstance(tc, dict))
    return tokens


def _pending_tool_round(messages: "List[Dict[str, Any]]") -> range:
    """Indices of the tool results the transcript ends with — a round the model has not answered yet.
    Trailing /steer rows do not answer it, so they are skipped."""
    end = len(messages)
    while end and messages[end - 1].get("display_kind") == STEER_DISPLAY_KIND:
        end -= 1
    start = end
    while start > 0 and messages[start - 1].get("role") == "tool":
        start -= 1
    return range(start, end)


def _skill_view_call_sites(messages: List[Dict[str, Any]]) -> list[tuple[int, str]]:
    """``(message_index, skill_name)`` for every skill_view tool call."""
    sites: list[tuple[int, str]] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            fn = _tc_get(tc, "function", {})
            args_str = _tc_get(fn, "arguments")
            if _tc_get(fn, "name") != "skill_view" or not isinstance(args_str, str):
                continue
            skill = _json_dict(args_str).get("name", "")
            if isinstance(skill, str) and skill:
                sites.append((i, skill))
    return sites


def _collect_protected_skill_names(messages: List[Dict[str, Any]], prune_boundary: int) -> set[str]:
    """Skill names (lower-cased) whose skill_view bodies must survive pass-2 demotion."""
    total = len(messages)
    if not total:
        return set()
    recent_start = max(0, total - _SKILL_PRUNE_RECENT_WINDOW)
    tail_start = max(0, prune_boundary)
    tail_user_texts = [
        m["content"].lower() for m in messages[tail_start:]
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"]
    ]
    return {
        skill.lower() for idx, skill in _skill_view_call_sites(messages)
        if idx >= min(recent_start, tail_start) or any(skill.lower() in text for text in tail_user_texts)
    }


def _summarize_tool_result(tool_name: str, tool_args: Any, tool_content: str) -> str:
    """Simplified 1-line summary ``[tool] args-brief (N chars)`` (Hermes' real one is per-tool)."""
    brief = str(tool_args or "").replace("\n", " ")
    brief = brief if len(brief) <= 80 else brief[:77] + "..."
    return f"[{tool_name}] {brief} ({len(tool_content):,} chars)"


class ContextCompressor(ContextEngine):
    """Stub of Hermes' default context engine (see the module docstring)."""

    @property
    def name(self) -> str:
        return "compressor"

    def __init__(
        self, model: str, threshold_percent: float = 0.50, protect_first_n: int = 3, protect_last_n: int = 20,
        summary_target_ratio: float = 0.20, quiet_mode: bool = False, summary_model_override: str = None,
        base_url: str = "", api_key: str = "", config_context_length: int | None = None, provider: str = "",
        api_mode: str = "", abort_on_summary_failure: bool = False, max_tokens: int | None = None,
        model_thresholds: dict[str, float] | None = None, threshold_tokens_cap: Any = None,
        proactive_prune_tokens: int = 0, proactive_prune_min_result_chars: int = 8000,
        proactive_prune_min_reclaim_tokens: int = 4096, min_tail_user_messages: int = 1, tail_mode: str = "lean",
        custom_providers: list | None = None,
    ):
        self.model, self.base_url, self.api_key, self.provider, self.api_mode = model, base_url, api_key, provider, api_mode
        self.tail_mode = tail_mode if tail_mode in ("legacy", "lean") else "lean"
        self.custom_providers = custom_providers or None
        self.model_thresholds = model_thresholds or {}
        self._config_threshold_percent = threshold_percent
        self.threshold_percent = threshold_percent
        self.threshold_tokens_cap = threshold_tokens_cap
        self.protect_first_n, self.protect_last_n = protect_first_n, protect_last_n
        self.proactive_prune_tokens = int(proactive_prune_tokens or 0)
        self.proactive_prune_min_result_chars = max(_PRUNE_MIN_CHARS, int(proactive_prune_min_result_chars or 8000))
        self.proactive_prune_min_reclaim_tokens = max(0, int(proactive_prune_min_reclaim_tokens or 0))
        self._proactive_prune_rearm_tokens: int = 0
        self.min_tail_user_messages = min_tail_user_messages
        self.summary_target_ratio = max(0.10, min(summary_target_ratio, 0.80))
        self.quiet_mode = quiet_mode
        self.max_tokens = max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else None
        self.abort_on_summary_failure = abort_on_summary_failure
        self.summary_model = summary_model_override or ""
        self._config_context_length = config_context_length
        self.context_length = config_context_length or 0
        self.threshold_tokens = int(self.context_length * self.threshold_percent)
        self.tail_token_budget = self._derive_tail_budget()
        self.last_prompt_tokens = self.last_completion_tokens = 0
        self.compression_count = 0
        self._previous_summary: Optional[str] = None
        self._session_db: Any = None
        self._session_id: str = ""
        # Stub-only observability: the kwargs of the latest compress() call.
        self._last_compress_args: Dict[str, Any] = {}

    # ---- budgets / state ------------------------------------------------------------------
    def _derive_tail_budget(self) -> int:
        if self.tail_mode == "lean":
            budget = max(LEAN_TAIL_FLOOR_TOKENS, min(LEAN_TAIL_CAP_TOKENS, int(self.context_length * 0.025)))
        else:
            budget = int(self.threshold_tokens * self.summary_target_ratio)
        if self.context_length > 0:
            budget = min(budget, int(self.context_length * TAIL_MAX_CONTEXT_FRACTION))
        return max(1, budget)

    def update_model(
        self, model: str, context_length: int, base_url: str = "", api_key: Any = "", provider: str = "",
        api_mode: str = "", max_tokens: int | None = None,
    ) -> None:
        self.model, self.base_url, self.api_key, self.provider, self.api_mode = model, base_url, api_key, provider, api_mode
        self.context_length = context_length
        if max_tokens is not None:
            self.max_tokens = max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else None
        self.threshold_percent = self._config_threshold_percent
        self.threshold_tokens = int(context_length * self.threshold_percent)
        self.tail_token_budget = self._derive_tail_budget()
        self.last_prompt_tokens = self.last_completion_tokens = self.last_total_tokens = 0
        self._proactive_prune_rearm_tokens = 0

    def update_from_response(self, usage: Dict[str, Any]):
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", self.last_prompt_tokens + self.last_completion_tokens)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        return self.threshold_tokens > 0 and tokens >= self.threshold_tokens

    def bind_session_state(self, session_db: Any = None, session_id: str = "") -> None:
        self._session_db, self._session_id = session_db, session_id or ""
        self._proactive_prune_rearm_tokens = 0

    def on_session_start(self, session_id: str, **kwargs) -> None:
        super().on_session_start(session_id, **kwargs)
        self.bind_session_state(kwargs.get("session_db", self._session_db), session_id)

    def _reset_session_compaction_state(self) -> None:
        self._previous_summary = None
        self._proactive_prune_rearm_tokens = 0

    def on_session_reset(self) -> None:
        super().on_session_reset()
        self._reset_session_compaction_state()

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        # Like Hermes: clears per-session compaction state only (counters survive, unlike /reset).
        self._reset_session_compaction_state()

    # ---- tail geometry --------------------------------------------------------------------
    def _walk_tail_budget(
        self, messages: List[Dict[str, Any]], head_end: int, ceiling: int, min_tail: int, *, cut_at_break: bool,
    ) -> tuple[int, int]:
        """Accumulate message tokens newest-first until ``ceiling`` (once ``min_tail`` rows are kept)."""
        n = len(messages)
        accumulated = 0
        cut = n
        for i in range(n - 1, head_end - 1, -1):
            msg_tokens = _estimate_msg_budget_tokens(messages[i])
            if accumulated + msg_tokens > ceiling and (n - i) >= min_tail:
                return (i if cut_at_break else cut), accumulated
            accumulated += msg_tokens
            cut = i
        return cut, accumulated

    def _prune_boundary(
        self, result: List[Dict[str, Any]], protect_tail_count: int, protect_tail_tokens: int | None,
    ) -> int:
        """First index of the protected tail; token budget (when given) beats the count floor."""
        if protect_tail_tokens is None or protect_tail_tokens <= 0:
            return len(result) - protect_tail_count
        min_protect = min(protect_tail_count, len(result), _MAX_TAIL_MESSAGE_FLOOR)
        boundary, _ = self._walk_tail_budget(result, 0, protect_tail_tokens, min_protect, cut_at_break=True)
        return min(boundary, len(result) - min_protect)

    # ---- the pruning seam -----------------------------------------------------------------
    @staticmethod
    def _dedupe_tool_results(result: List[Dict[str, Any]]) -> int:
        """Pass 1: keep the newest copy of identical tool results, back-reference older ones."""
        pruned = 0
        content_hashes: set = set()
        for i in range(len(result) - 1, -1, -1):
            msg = result[i]
            content = msg.get("content") or ""
            if msg.get("role") != "tool" or not isinstance(content, str) or len(content) < _PRUNE_MIN_CHARS:
                continue
            h = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:12]
            if h in content_hashes:
                result[i] = {**msg, "content": "[Duplicate tool output — same content as a more recent call]"}
                pruned += 1
            content_hashes.add(h)
        return pruned

    @staticmethod
    def _demote_tool_result_at(
        result: List[Dict[str, Any]], idx: int, call_id_to_tool: Dict[str, tuple[str, str]],
        min_prune_chars: int, protected_skills: Optional[set[str]] = None,
    ) -> bool:
        """Replace the tool result at ``idx`` with a 1-line summary; True if modified.
        ``protected_skills`` (lower-cased) spares matching skill_view bodies; None (pressure pass)
        overrides the guard. Image-bearing (list / ``_multimodal``) content is stripped as in Hermes;
        text-only parts are left alone."""
        msg = result[idx]
        if msg.get("role") != "tool":
            return False
        content = msg.get("content", "")
        if isinstance(content, list) or (isinstance(content, dict) and content.get("_multimodal")):
            new_msg = _strip_images_from_tool_msg(msg)
            if new_msg is not None:
                result[idx] = new_msg
            return new_msg is not None
        if (
            not isinstance(content, str) or not content or content == _PRUNED_TOOL_PLACEHOLDER
            or content.startswith(("[Duplicate tool output", "[screenshot removed"))
            or _is_summary_stub(content) or len(content) <= min_prune_chars
        ):
            return False
        tool_name, tool_args = call_id_to_tool.get(msg.get("tool_call_id", ""), ("unknown", ""))
        if protected_skills and tool_name == "skill_view":
            _skill = _json_dict(tool_args).get("name", "")
            if isinstance(_skill, str) and _skill.lower() in protected_skills:
                return False
        result[idx] = {**msg, "content": _summarize_tool_result(tool_name, tool_args, content)}
        return True

    def _tail_soft_ceiling(self, token_budget: int) -> int:
        ceiling = int(token_budget * 1.5)
        ctx = getattr(self, "context_length", 0) or 0
        if ctx > 0:
            ceiling = min(ceiling, int(ctx * TAIL_MAX_CONTEXT_FRACTION))
        return max(ceiling, token_budget)

    def _pressure_demote_tail(
        self, result: List[Dict[str, Any]], prune_boundary: int, protect_tail_tokens: int,
        call_id_to_tool: Dict[str, tuple[str, str]], min_prune_chars: int, spared: range,
    ) -> int:
        """Pass 4: demote tool-result bodies inside the protected tail when it alone exceeds the soft
        budget. Calls the seam with 4 args (``protected_skills`` None) from the nested ``_shrink_at``."""
        soft_ceiling = self._tail_soft_ceiling(protect_tail_tokens)
        demote_end = len(result) - min(_PRESSURE_KEEP_RECENT_MESSAGES, len(result))
        start = max(0, prune_boundary)

        def _protected_region_tokens() -> int:
            return sum(_estimate_msg_budget_tokens(result[i]) for i in range(start, len(result)))

        demoted = 0

        def _shrink_at(i: int) -> None:
            nonlocal demoted
            if i in spared:
                return
            if self._demote_tool_result_at(result, i, call_id_to_tool, min_prune_chars):
                demoted += 1

        if demote_end <= prune_boundary or _protected_region_tokens() <= soft_ceiling:
            return 0
        for i in range(start, demote_end):
            _shrink_at(i)
            if _protected_region_tokens() <= soft_ceiling:
                break
        if _protected_region_tokens() > soft_ceiling:
            last_tool_idx = next((i for i in range(len(result) - 1, -1, -1) if result[i].get("role") == "tool"), None)
            for i in (i for i in range(start, len(result)) if i != last_tool_idx):
                _shrink_at(i)
            if (
                last_tool_idx is not None and last_tool_idx not in spared and last_tool_idx >= prune_boundary
                and _protected_region_tokens() > soft_ceiling
            ) and self._demote_tool_result_at(result, last_tool_idx, call_id_to_tool, min_prune_chars):
                demoted += 1
        return demoted

    def _spared_pending_tool_round(self, messages: List[Dict[str, Any]]) -> range:
        """The pending round (owning assistant row included) when it fits the hard share of the window."""
        pending = _pending_tool_round(messages)
        if pending and pending.start > 0 and messages[pending.start - 1].get("tool_calls"):
            pending = range(pending.start - 1, pending.stop)
        window = getattr(self, "context_length", 0) or 0
        hard_share = int(window * TAIL_MAX_CONTEXT_FRACTION) if window else 10**12
        return pending if sum(_estimate_msg_budget_tokens(messages[i]) for i in pending) <= hard_share else range(0)

    def _prune_old_tool_results(
        self, messages: List[Dict[str, Any]], protect_tail_count: int,
        protect_tail_tokens: int | None = None, min_prune_chars: int = _PRUNE_MIN_CHARS,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Project old tool-result bodies to bounded summaries without rewriting tool-call arguments.
        Rows are shallow-copied; returns ``(messages, count)``."""
        if not messages:
            return messages, 0
        result = [m.copy() for m in messages]
        call_id_to_tool = _tool_calls_by_id(result)
        prune_boundary = self._prune_boundary(result, protect_tail_count, protect_tail_tokens)
        spared = self._spared_pending_tool_round(result)
        prune_boundary = min(prune_boundary, spared.start) if spared else prune_boundary
        pruned = self._dedupe_tool_results(result)
        protected_skills = _collect_protected_skill_names(result, prune_boundary)
        # Pass 2: summarize old tool results (5 positional args to the seam).
        pruned += sum(
            self._demote_tool_result_at(result, i, call_id_to_tool, min_prune_chars, protected_skills)
            for i in range(max(0, prune_boundary))
        )
        # Pass 4: pressure demotion inside the protected tail (4 args to the seam).
        if protect_tail_tokens is not None and protect_tail_tokens > 0 and result:
            pruned += self._pressure_demote_tail(
                result, prune_boundary, protect_tail_tokens, call_id_to_tool, min_prune_chars, spared,
            )
        return result, pruned

    def _protect_head_size(self, messages: List[Dict[str, Any]]) -> int:
        """System prompt (if present) plus the decaying ``protect_first_n`` extra rows."""
        head = 1 if messages and messages[0].get("role") == "system" else 0
        extra = 0 if (self.compression_count >= 1 or self._previous_summary) else self.protect_first_n
        return head + extra

    def prune_tool_results_only(
        self, messages: List[Dict[str, Any]], current_tokens: int | None = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Deterministic, no-LLM tool-result prune gated on ``proactive_prune_tokens``. A commit
        breaks the prompt cache, so it requires ``proactive_prune_min_reclaim_tokens``; otherwise
        returns the INPUT object as ``(messages, 0)``."""
        if self.proactive_prune_tokens <= 0 or (
            current_tokens is not None and current_tokens < self.proactive_prune_tokens
        ):
            return messages, 0
        if len(messages) <= self.protect_last_n + self._protect_head_size(messages) + 1:
            return messages, 0
        before = sum(_estimate_msg_budget_tokens(m) for m in messages)
        over_threshold = (
            current_tokens is not None and self.threshold_tokens > 0 and current_tokens >= self.threshold_tokens
        )
        if before < self._proactive_prune_rearm_tokens and not over_threshold:
            return messages, 0
        session_db = getattr(self, "_session_db", None)
        session_id = getattr(self, "_session_id", "")
        if session_db and session_id and not callable(getattr(session_db, "archive_and_compact", None)):
            return messages, 0
        pruned_msgs, pruned_count = self._prune_old_tool_results(
            messages, protect_tail_count=self.protect_last_n, protect_tail_tokens=None,
            min_prune_chars=self.proactive_prune_min_result_chars,
        )
        if not pruned_count:
            return messages, 0
        after = sum(_estimate_msg_budget_tokens(m) for m in pruned_msgs)
        reclaimed = max(0, before - after)
        if reclaimed < self.proactive_prune_min_reclaim_tokens:
            return messages, 0
        runway = max(reclaimed, self.proactive_prune_tokens, self.proactive_prune_min_reclaim_tokens)
        next_rearm_tokens = after + runway
        if session_db and session_id:
            try:
                session_db.archive_and_compact(
                    session_id, pruned_msgs,
                    model_config_patch={PROACTIVE_PRUNE_REARM_MODEL_CONFIG_KEY: next_rearm_tokens},
                    watermark=None, covered_ids=(), unresolved_held=(),
                )
            except Exception as exc:  # noqa: BLE001 — mirror Hermes: keep the original transcript
                logger.warning("Proactive tool-result prune DB commit failed; keeping the original transcript: %s", exc)
                return messages, 0
        self._proactive_prune_rearm_tokens = next_rearm_tokens
        return pruned_msgs, pruned_count

    # ---- lean-mode hooks ------------------------------------------------------------------
    def _demote_stale_tail_tools(self, messages: List[Dict[str, Any]], tail_start: int) -> List[Dict[str, Any]]:
        """Lean mode: demote tail tool results older than the newest ``_LEAN_TAIL_KEEP_TOOL_ROUNDS`` rounds to
        recovery stubs; skill-marker rows untouched. New list (untouched rows shared, demoted copied)."""
        session_id = getattr(self, "_session_id", "") or ""
        rounds_seen = 0
        protected: set[int] = set()
        prev_idx = None
        for i in (i for i in range(len(messages) - 1, tail_start - 1, -1) if messages[i].get("role") == "tool"):
            rounds_seen += prev_idx is None or prev_idx - i > 1
            prev_idx = i
            if rounds_seen > _LEAN_TAIL_KEEP_TOOL_ROUNDS:
                break
            protected.add(i)
        result = list(messages)
        for i in range(tail_start, len(messages)):
            msg = messages[i]
            content = msg.get("content")
            if msg.get("role") != "tool" or i in protected or not isinstance(content, str):
                continue
            if len(content) < _LEAN_TAIL_DEMOTE_MIN_CHARS or SKILL_PRUNED_MARKER_PREFIX in content or _is_summary_stub(content):
                continue
            result[i] = _rewritten(msg, _lean_recovery_stub(msg.get("tool_name") or "", len(content), session_id))
        return result

    def _augment_summary_lean(self, summary: str, turns_to_summarize: List[Dict[str, Any]]) -> str:
        """Append deterministic lean-mode sections to a summary; no-op in legacy mode."""
        if getattr(self, "tail_mode", "lean") != "lean":
            return summary
        if _LEAN_ANCHOR_HEADING not in summary:
            paths = sorted({
                p for m in turns_to_summarize for p in re.findall(r"(?:/[\w.\-]+){2,}", _content_text(m.get("content") or ""))
            })[:8]
            summary += f"\n\n{_LEAN_ANCHOR_HEADING}\n" + "\n".join(f"- {p}" for p in paths)
        if _LEAN_USER_MESSAGES_HEADING not in summary:
            users = [_content_text(m.get("content") or "") for m in turns_to_summarize if m.get("role") == "user"]
            summary += f"\n\n{_LEAN_USER_MESSAGES_HEADING}\n" + "\n".join(f"- {u[:200]}" for u in reversed(users[-5:]))
        if _LEAN_RECOVERY_HEADING not in summary:
            sid = getattr(self, "_session_id", "") or ""
            summary += (
                f"\n\n{_LEAN_RECOVERY_HEADING}\n{len(turns_to_summarize)} earlier message(s) are preserved in "
                f"session history." + (f" session_search(query=..., session_id='{sid}')" if sid else "")
            )
        return summary

    # ---- compression ----------------------------------------------------------------------
    def _sanitize_tool_pairs(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove orphaned tool results; strip tool_calls that have no result (except the trailing turn's)."""
        call_ids = {
            _tc_get(tc, "id") for m in messages if m.get("role") == "assistant" for tc in m.get("tool_calls") or []
        }
        result_ids = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
        messages = [m for m in messages if m.get("role") != "tool" or m.get("tool_call_id") in call_ids]
        idx = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") != "tool"), -1)
        trailing = messages[idx] if idx >= 0 and messages[idx].get("role") == "assistant" else None
        out = []
        for m in messages:
            tcs = m.get("tool_calls")
            if m.get("role") == "assistant" and tcs and m is not trailing:
                kept = [tc for tc in tcs if _tc_get(tc, "id") in result_ids]
                if len(kept) != len(tcs):
                    m = dict(m)
                    if kept:
                        m["tool_calls"] = kept
                    else:
                        m.pop("tool_calls", None)
                        if not m.get("content"):
                            m["content"] = "(tool call removed)"
            out.append(m)
        return out

    def _tail_start(self, messages: List[Dict[str, Any]], head_end: int) -> int:
        n = len(messages)
        min_tail = max(3, self.min_tail_user_messages + 1)
        cut, _ = self._walk_tail_budget(messages, head_end, self.tail_token_budget, min_tail, cut_at_break=True)
        cut = min(cut, n - min_tail)
        # Keep the newest real user message in the tail.
        last_user = next((i for i in range(n - 1, head_end - 1, -1) if messages[i].get("role") == "user"), None)
        if last_user is not None:
            cut = min(cut, last_user)
        # Do not split a tool group: move before the owning assistant row.
        while cut > head_end and messages[cut].get("role") == "tool":
            cut -= 1
        return max(cut, head_end)

    def _fake_summary(self, turns: List[Dict[str, Any]], focus_topic: Optional[str]) -> str:
        parts = [f"{m.get('role')}: {_content_text(m.get('content') or '')[:80]}" for m in turns]
        body = f"Stub summary of {len(turns)} earlier message(s)"
        if focus_topic:
            body += f" (focus: {focus_topic})"
        return (SUMMARY_PREFIX + body + ". " + " | ".join(parts))[:1500]

    def compress(
        self, messages: List[Dict[str, Any]], current_tokens: Optional[int] = None, focus_topic: Optional[str] = None,
        force: bool = False, memory_context: str = "", bypass_cooldown: bool = False,
    ) -> List[Dict[str, Any]]:
        """Prune tool results (with ``protect_tail_tokens``), write a deterministic fake summary,
        return head + summary + tail. Updates ``compression_count`` and ``_previous_summary``."""
        self._last_compress_args = {
            "current_tokens": current_tokens, "focus_topic": focus_topic, "force": force,
            "memory_context": memory_context, "bypass_cooldown": bypass_cooldown,
        }
        n = len(messages)
        head_n = self._protect_head_size(messages)
        if n <= head_n + 3 + 1:
            return messages
        canonical = list(messages)
        pruned, _count = self._prune_old_tool_results(
            messages, protect_tail_count=self.protect_last_n, protect_tail_tokens=self.tail_token_budget,
        )
        start, end = head_n, self._tail_start(pruned, head_n)
        if start >= end:
            return canonical
        turns = pruned[start:end]
        if self.tail_mode == "lean":
            pruned = self._demote_stale_tail_tools(pruned, end)
        summary = self._augment_summary_lean(self._fake_summary(turns, focus_topic), turns)
        head, tail = pruned[:start], [dict(m) for m in pruned[end:]]
        role = "assistant" if head and head[-1].get("role") == "user" else "user"
        if tail and tail[0].get("role") == role and isinstance(tail[0].get("content"), str):
            tail[0] = _rewritten(tail[0], summary + "\n\n" + _SUMMARY_END_MARKER + "\n\n" + tail[0]["content"])
            compressed = head + tail
        else:
            compressed = head + [{
                "role": role, "content": summary + "\n\n" + _SUMMARY_END_MARKER, COMPRESSED_SUMMARY_METADATA_KEY: True,
            }] + tail
        self.compression_count += 1
        self._previous_summary = summary
        self._proactive_prune_rearm_tokens = 0
        return self._sanitize_tool_pairs(compressed)
