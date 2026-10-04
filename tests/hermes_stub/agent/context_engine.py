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
# Signatures, attribute names and hook defaults below mirror Hermes Agent (agent/context_engine.py);
# docstrings are abbreviated and anything the plugin does not rely on is omitted.
"""Stub of ``agent.context_engine``: the ``ContextEngine`` ABC and its optional hooks (tests only)."""

from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ContextEngine(ABC):
    """Base class all context engines must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g. 'compressor', 'lcm')."""

    # Token state: engines MUST maintain these; the host reads them directly.
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0
    threshold_percent: float = 0.75
    protect_first_n: int = 3
    protect_last_n: int = 6
    emit_automatic_compaction_status: bool = True

    @abstractmethod
    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """Update tracked token usage after every LLM call."""

    @abstractmethod
    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Return True if compaction should fire this turn."""

    def should_compress_info(self, prompt_tokens: int = None) -> "tuple[bool, str | None]":
        """Return ``(should_compress, reason)``; the default has no block reasons."""
        return self.should_compress(prompt_tokens), None

    @abstractmethod
    def compress(
        self, messages: List[Dict[str, Any]], current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None, force: bool = False, memory_context: str = "",
    ) -> List[Dict[str, Any]]:
        """Compact ``messages`` into a valid OpenAI-format list that fits the budget."""

    def prune_tool_results_only(
        self, messages: List[Dict[str, Any]], current_tokens: int | None = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Deterministically trim old tool-result payloads without an LLM call (default no-op)."""
        return messages, 0

    def select_context(
        self, request_messages: List[Dict[str, Any]], *, conversation_messages: List[Dict[str, Any]] = None,
        incoming_message: Dict[str, Any] = None, budget_tokens: int = 0,
    ) -> List[Dict[str, Any]]:
        """Optionally replace the context for THIS request; ``None`` leaves it unchanged."""
        return None

    def on_turn_complete(self, messages: List[Dict[str, Any]], usage: Dict[str, Any] = None, **kwargs: Any) -> None:
        """Observe a finished turn (best-effort)."""
        return None

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        """Cheap rough check before the API call; default skips."""
        return False

    def should_defer_preflight_to_real_usage(self, rough_tokens: int) -> bool:
        return False

    def get_automatic_compaction_status_message(
        self, *, phase: str, default_message: str, **context: Any,
    ) -> str | None:
        """User-visible status for automatic compaction, or ``None`` to suppress it."""
        return default_message if self.emit_automatic_compaction_status else None

    def has_content_to_compress(self, messages: List[Dict[str, Any]]) -> bool:
        return True

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Session begins: load persisted state."""

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Real session boundary (CLI exit, /reset, gateway expiry) — never per-turn."""

    def on_session_reset(self) -> None:
        """/new or /reset: reset per-session state."""
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Tool schemas this engine exposes to the agent (default: none)."""
        return []

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle a call to one of this engine's tools; must return a JSON string."""
        return json.dumps({"error": f"Unknown context engine tool: {name}"})

    def get_status(self) -> Dict[str, Any]:
        """Status dict with the standard fields the host expects."""
        last_prompt = max(self.last_prompt_tokens, 0)
        return {
            "last_prompt_tokens": last_prompt,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": min(100, last_prompt / self.context_length * 100) if self.context_length else 0,
            "compression_count": self.compression_count,
        }

    def clone_for_agent(self) -> "ContextEngine":
        """Per-agent instance of a plugin-registered engine (default: deepcopy)."""
        return copy.deepcopy(self)

    def update_model(
        self, model: str, context_length: int, base_url: str = "", api_key: str = "",
        provider: str = "", api_mode: str = "",
    ) -> None:
        """Model switch / fallback: recompute threshold_tokens."""
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)
