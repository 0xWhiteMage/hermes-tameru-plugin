"""Effective cost: what a replayed session costs once the facts an arm lost, or only holds behind a read-back, are priced.

The saved baselines (protocol 2) count a needed fact as kept only if its text is in the final request, and charge
nothing for a fact an arm lost or for the tool call that gets one back. That favours an arm that drops rows (it looks
cheap) and ignores the read-back an arm built for exactly that case. This module leaves ``replay`` and its baselines
untouched and measures one run a second way:

* ``kept``         the needed fact's text is in the final request;
* ``recoverable``  it is not, but a ``tameru_read_original`` call on a ref that is in the request returns it;
* ``lost``         neither. A fact planted by a tool call costs a re-run of that call; one the user or assistant said
                   cannot be re-run and is counted in ``lost_unrecoverable`` (and not priced).

Prices (characters, like the replay): a read-back or re-run costs ``CACHE_WRITE`` times the returned text plus
``CALL_OVERHEAD_CHARS`` for the call and its envelope. That is a model of the extra turn, not a measurement: it assumes
the returned text is written to the cache once and not read again, and it ignores output tokens and latency.

Run ``TAMERU_P3_MATRIX=out.json python -m pytest tests/eval/test_protocol3.py`` (real Hermes) for a table.
"""
from __future__ import annotations

import json
import re
from typing import Any

import engines
import replay
import scenario_registry as registry

CALL_OVERHEAD_CHARS = 300          # the assistant's call and the tool message's envelope, per read-back or re-run
READ_TOOL = "tameru_read_original"
_REF_RE = re.compile(r"ref=([0-9a-f]{8,16})")


def _tool_results(script) -> dict[str, int]:
    """Characters of the result each scripted tool call printed, by call key."""
    sizes: dict[str, int] = {}
    for turn in script.turns:
        for rnd in turn.rounds:
            for step in rnd.steps:
                sizes[step.key] = max(sizes.get(step.key, 0), len(step.result))
    return sizes


def _read_back(engine: Any, refs: list[str], needle: str) -> int | None:
    """Characters returned by the first read-back that contains ``needle``, or None when none does."""
    call = getattr(engine, "handle_tool_call", None)
    schemas = getattr(engine, "get_tool_schemas", None)
    if not callable(call) or not callable(schemas):
        return None
    try:
        if not any(isinstance(s, dict) and s.get("name") == READ_TOOL for s in schemas()):
            return None
    except Exception:
        return None
    for ref in refs:
        try:
            answer = call(READ_TOOL, {"ref": ref, "grep": re.escape(needle)})
        except Exception:
            continue
        try:
            body = json.loads(answer)
        except (TypeError, ValueError):
            continue
        if isinstance(body, dict) and needle in str(body.get("content", "")):
            return len(answer)
    return None


def measure(arm: str, scenario: str, window: int, seed: int = 0) -> dict:
    """One replay of ``arm`` on ``scenario`` at ``window``, with the needed facts sorted and priced."""
    made: list[Any] = []

    def factory():
        made.append(engines.make_engine(arm))
        return made[-1]

    result = replay.run_session(factory, scenario=scenario, context_length=window, seed=seed, keep_final_request=True)
    metrics = result["metrics"]
    final = result.get("final_request") or []
    spec = registry.get_spec(scenario)
    script = spec.build(seed, spec.turns)
    sizes = _tool_results(script)
    text = replay.visible_text(final)
    refs = list(dict.fromkeys(_REF_RE.findall(text)))
    engine = made[-1]

    kept, recoverable, lost, unrecoverable = [], [], [], []
    extra = 0.0
    for fact in (g for g in script.gold if g.needed):
        if fact.needle in text:
            kept.append(fact.id)
            continue
        returned = _read_back(engine, refs, fact.needle)
        if returned is not None:
            recoverable.append(fact.id)
            extra += replay.CACHE_WRITE * (returned + CALL_OVERHEAD_CHARS)
        elif fact.source == "tool" and fact.call_key in sizes:
            lost.append(fact.id)
            extra += replay.CACHE_WRITE * (sizes[fact.call_key] + CALL_OVERHEAD_CHARS)
        else:
            lost.append(fact.id)
            unrecoverable.append(fact.id)
    answered = max(1, metrics["turns"] - metrics["failed_turns"])
    base = metrics["total_simulated_input_cost"]
    return {
        "arm": arm, "scenario": scenario, "window": window, "seed": seed,
        "base": round(base / answered, 1), "effective": round((base + extra) / answered, 1),
        "needed": len(kept) + len(recoverable) + len(lost),
        "kept": len(kept), "recoverable": len(recoverable), "lost": len(lost), "lost_unrecoverable": len(unrecoverable),
        "kept_ids": kept, "recoverable_ids": recoverable, "lost_ids": lost,
    }
