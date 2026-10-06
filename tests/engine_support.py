"""Shared builders for the engine-level tests (``test_ladder`` and friends).

``make_engine`` builds a ``TameruContextEngine`` whose Hermes gates are open (``proactive_prune_tokens``
1, no minimum reclaim, a 4-row protected tail), ``Chat`` builds a session of tool rounds, and
``in_scope`` / ``demote`` drive seam A directly under a pass scope.
"""
from __future__ import annotations

import contextlib
import json
import random
from typing import Any

from fixtures import hermes_payloads as hp

from hermes_tameru_plugin import engine as engine_module
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.engine import TameruContextEngine
from hermes_tameru_plugin.hermes_compat import tool_calls_by_id

MIN_PRUNE_CHARS = 200
PROTECT_LAST_N = 4


def make_engine(settings: TameruSettings | None = None, **kwargs: Any) -> TameruContextEngine:
    """An engine with Hermes' prune gates open; ``kwargs`` go to ``ContextCompressor``."""
    options: dict[str, Any] = {
        "quiet_mode": True, "config_context_length": 64_000, "proactive_prune_tokens": 1,
        "proactive_prune_min_reclaim_tokens": 0, "proactive_prune_min_result_chars": MIN_PRUNE_CHARS,
        "protect_last_n": PROTECT_LAST_N,
    }
    return TameruContextEngine(settings=settings or TameruSettings(), **{**options, **kwargs})


class Chat:
    """A conversation under construction: ``tool`` adds one assistant call and its result."""

    def __init__(self, task: str = "why did the payment gateway time out?") -> None:
        self.messages: list[dict] = [
            {"role": "system", "content": "You are an engineer."},
            {"role": "user", "content": task},
        ]
        self._calls = 0

    def tool(self, name: str, args: dict, content: Any, call_id: str | None = None) -> int:
        """Append a tool round; returns the index of the tool row."""
        self._calls += 1
        call_id = call_id or f"call_{self._calls:04d}"
        self.messages.append({
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }],
        })
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content, "tool_name": name})
        return len(self.messages) - 1

    def user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def finish(self, text: str = "continue") -> list[dict]:
        """Add short filler rounds and a user turn, so every earlier row is outside the tail."""
        for i in range(PROTECT_LAST_N):
            self.tool("terminal", {"command": f"echo filler {i}"}, hp.terminal_result("ok"))
        self.user(text)
        return self.messages


def pytest_failure(seed: int = 1) -> str:
    """A ``terminal`` result of a failing pytest run (compresses to a low-risk extract)."""
    rng = random.Random(seed)
    output = hp.pytest_output(
        rng, failed=["tests/test_orders.py::test_reserve_stock_rollback"], n_passed=120,
    )
    return hp.terminal_result(output, exit_code=1)


def pytest_failure_with_passes(seed: int = 1) -> str:
    """A failing ``pytest -v`` run: 150 PASSED lines, then one failure block (reaches the extract rung).

    Since engine 1.4.0 a test run is reduced to its failure blocks *whole*, so ``pytest_failure`` (one
    block carrying a 60-line captured log, saving ~4%) is below ``min_savings`` and lands on the brief.
    The verbose passes are what the extract elides.
    """
    rng = random.Random(seed)
    passes = hp.pytest_verbose_output(rng, 150).split("\n")[:-2]      # without the "N passed" footer
    run = hp.pytest_output(
        rng, failed=["tests/test_orders.py::test_reserve_stock_rollback"], n_passed=120, extra_log_lines=8,
    )
    output = "\n".join(passes) + "\n" + run[run.index("=" * 35 + " FAILURES"):]
    return hp.terminal_result(output, exit_code=1)


def server_log(seed: int = 1) -> str:
    """A ``terminal`` result holding a 450-line application log with a burst of errors."""
    return hp.terminal_result(hp.server_log(random.Random(seed), n_lines=450)[0])


def source_file(seed: int = 1, *, patched: bool = False) -> str:
    """A ``read_file`` result of the orders service module."""
    return hp.read_file_result(hp.orders_service_source(random.Random(seed), patched=patched))


@contextlib.contextmanager
def in_scope(engine: TameruContextEngine, messages: list[dict] | None = None, **fields: Any):
    """Run the block under a pass scope of ``engine`` (what its public overrides set up)."""
    scope = engine._new_scope(messages, **fields)
    token = engine_module._SCOPE.set(scope)
    try:
        yield scope
    finally:
        engine_module._SCOPE.reset(token)


def demote(
    engine: TameruContextEngine, messages: list[dict], idx: int, *, rest: tuple = (set(),),
    scope: dict | None = None,
) -> tuple[bool, list[dict]]:
    """Call seam A on a copy of ``messages``; ``(changed, result)``. ``rest=()`` is the pass-4 shape."""
    result = [dict(m) for m in messages]
    with in_scope(engine, messages, **(scope or {})):
        changed = engine._demote_tool_result_at(
            result, idx, tool_calls_by_id(result), MIN_PRUNE_CHARS, *rest,
        )
    return changed, result


def prune_pass(engine: TameruContextEngine, messages: list[dict], **fields: Any) -> tuple[list[dict], int]:
    """Hermes' ``_prune_old_tool_results`` (passes 1 to 4) under a pass scope, bypassing the gates."""
    with in_scope(engine, messages, **fields):
        return engine._prune_old_tool_results(
            messages, protect_tail_count=PROTECT_LAST_N, min_prune_chars=MIN_PRUNE_CHARS,
        )


def fake_summary_llm(monkeypatch) -> None:
    """Replace Hermes' summary LLM call (real Hermes only; the stub has none) with a fixed reply."""
    import agent.context_compressor as cc

    if not hasattr(cc, "call_llm"):
        return

    def reply(*args: Any, **kwargs: Any) -> dict:
        route = kwargs.get("route_info")
        if isinstance(route, dict):
            route.update({"provider": "fake", "model": "fake-summarizer"})
        text = "## Goal\nFind why the payment gateway timed out.\n## Progress\nRead the logs."
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}

    monkeypatch.setattr(cc, "call_llm", reply)
