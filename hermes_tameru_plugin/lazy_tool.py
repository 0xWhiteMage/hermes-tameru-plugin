"""Send the recovery tool only once Tameru has shortened something in this chat (the ``lazy_tool`` setting, off by default).

Why: ``tameru_read_original`` is listed on every request, and a chat that never gets a result shortened never needs it. Its
schema is a fixed ~1,300 characters at the head of every cached prefix. A Hermes ``llm_request`` middleware can take it out of
the outgoing request while there is nothing to read back, and put it back, for good, from the first request that has a
shortened result in it.

What stays the same: the tool is still registered with Hermes, so a call to it is still routed to the engine and answered
(Hermes routes by the names the engine listed at start, not by what a request carries); and anything the middleware does not
recognise, or cannot be sure about, leaves the request untouched, which means the tool stays listed.

How it decides: from the engine's own state (rows it shortened, originals it stored), never from text in the request. A file or
web page that contains something shaped like a Tameru header therefore cannot make the tool appear, which would cost a full
rewrite of the cached prefix at a moment the author of that text chose.

Supported request shapes: chat completions (``tools[i].function.name``) and Anthropic messages (``tools[i].name``, with the
cache marker moved to the new last tool when the removed tool carried it). Codex Responses requests and any other ``api_mode`` are
left alone: Hermes derives that route's prompt-cache key from the tool list before a plugin can change the request.
"""
from __future__ import annotations

from typing import Any

from .recovery import EXPAND_TOOL_NAME, READ_ORIGINAL_TOOL_NAME

HIDEABLE = frozenset({READ_ORIGINAL_TOOL_NAME, EXPAND_TOOL_NAME})
SUPPORTED_API_MODES = frozenset({"chat_completions", "anthropic_messages"})
_LOCK_WAIT_SECONDS = 0.05      # if the engine is busy, say the tool is wanted (listing it is always safe)


def _name(tool: Any) -> str | None:
    if not isinstance(tool, dict):
        return None
    function = tool.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    name = tool.get("name")
    return name if isinstance(name, str) else None


def _hideable(tool: Any) -> bool:
    """The recovery tool by its own name, or as the Anthropic subscription route sends it (Hermes prefixes every tool name
    with ``mcp__`` on the wire and maps it back on the reply)."""
    name = _name(tool)
    return name in HIDEABLE or (isinstance(name, str) and name.startswith("mcp__") and name[5:] in HIDEABLE)


def strip_tool(request: Any, api_mode: Any) -> dict | None:
    """``request`` without the recovery tool, or None when it should be sent as it is.

    ``request`` is the middleware's own copy of the provider keyword arguments, so it is changed in place and returned.
    None means: not a supported shape, no tool list, the tool is not in it, or removing it would leave the list empty."""
    if api_mode not in SUPPORTED_API_MODES or not isinstance(request, dict):
        return None
    tools = request.get("tools")
    if not isinstance(tools, list) or not tools:
        return None
    kept = [tool for tool in tools if not _hideable(tool)]
    if len(kept) == len(tools) or not kept:
        return None
    if api_mode == "anthropic_messages":
        removed_marker = next((tool["cache_control"] for tool in tools
                               if _hideable(tool) and isinstance(tool, dict) and "cache_control" in tool), None)
        if removed_marker is not None and isinstance(kept[-1], dict) and "cache_control" not in kept[-1]:
            kept[-1] = {**kept[-1], "cache_control": removed_marker}
    request["tools"] = kept
    return request


def reveal_needed(engine: Any) -> bool:
    """True once this chat has a shortened result or a stored original, and then for good (the tool is never hidden again).

    Reads engine state only. When the answer cannot be had quickly, it is True: the tool stays listed."""
    if getattr(engine, "_tool_revealed", False):
        return True
    try:
        lock = engine._activity.lifecycle_lock
        if not lock.acquire(timeout=_LOCK_WAIT_SECONDS):
            return True
        try:
            wanted = bool(engine._activity.rows()) or len(engine._working_store()) > 0
        finally:
            lock.release()
    except Exception:
        return True
    if wanted:
        engine._tool_revealed = True
    return wanted
