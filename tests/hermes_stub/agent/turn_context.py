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
# The three sidecar helpers below are copied verbatim from Hermes Agent (agent/turn_context.py);
# the rest of that module (the turn prologue) is deliberately not reproduced.
"""Stub of ``agent.turn_context``: only the ``api_content`` sidecar helpers (tests only)."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def substitute_api_content(api_msg: Dict[str, Any]) -> Optional[str]:
    """Pop the ``api_content`` sidecar and substitute it into ``content`` (keeps the
    prompt-cache prefix byte-stable). Returns the popped sidecar, or ``None``."""
    sidecar = api_msg.pop("api_content", None)
    if isinstance(sidecar, str) and sidecar and api_msg.get("role") in ("user", "assistant"):
        api_msg["content"] = sidecar
    return sidecar


def drop_stale_api_content(msg: Dict[str, Any]) -> None:
    """Drop the ``api_content`` sidecar from a message whose content was rewritten
    (replaying it would resend what the rewrite removed; cost is one cache miss)."""
    msg.pop("api_content", None)


def extract_api_content_sidecar(msg: Mapping[str, Any]) -> Optional[str]:
    """Extract the ``api_content`` sidecar; ``None`` when absent/non-string."""
    v = msg.get("api_content")
    return v if isinstance(v, str) else None
