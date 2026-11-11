"""Send the replay's exact requests to a real provider: real billing and real answers for identical request sequences.

Why: the replay prices requests with a model of the prompt cache, and a live agent chooses its own tool calls, so two live
runs of the same task are two different sessions and a few percent cannot be told apart from the model's path. This module
removes the model's path from the comparison. It records every request the replay sends for an arm (stock Hermes, or the
plugin) and sends exactly those requests, in order, to the Anthropic Messages API with prompt caching on, then reads what the
provider billed (``usage``) and checks the answer to the replay's closing question against the scenario's needed facts.

What it measures: the provider's cache reads, writes and uncached input for each arm, and whether the closing answer still
contains the facts the question needs. What it does not: the model's own tool choices, and the plugin's live behaviour inside
Hermes (that is what the replay and the Desktop checks are for).

Safety: the API key is read in-process from the environment (``ANTHROPIC_API_KEY``) or from the file named by
``TAMERU_LIVE_KEY_FILE`` and is never printed, logged or written. ``plan`` (the default) sends nothing; a run needs
``TAMERU_LIVE=1``. A dollar cap (``max_usd``) stops a run, and every run's cache is its own (a unique id leads the system
prompt), so one arm cannot read what the other wrote.
"""
from __future__ import annotations

import copy
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

MODEL = os.environ.get("TAMERU_LIVE_MODEL", "claude-haiku-4-5-20251001")
URL = "https://api.anthropic.com/v1/messages"
# Haiku 4.5 list prices per million tokens (input, output); cache writes cost 1.25x and reads 0.10x the input price.
# Recompute from the recorded token counts if the price changes.
USD_PER_MTOK_IN, USD_PER_MTOK_OUT = 1.00, 5.00
WRITE_MULT, READ_MULT = 1.25, 0.10
PROBE_MAX_TOKENS, FILLER_MAX_TOKENS = 600, 16
MAX_BREAKPOINTS = 4


class BudgetExceeded(RuntimeError):
    pass


# ---- the key: read it, never show it ----------------------------------------------------------------------------------
def load_key(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if not key and env.get("TAMERU_LIVE_KEY_FILE"):
        try:
            key = Path(env["TAMERU_LIVE_KEY_FILE"]).read_text(encoding="utf-8").strip().splitlines()[0].strip()
        except (OSError, IndexError):
            key = ""
    if not key:
        raise RuntimeError("no API key: set ANTHROPIC_API_KEY, or TAMERU_LIVE_KEY_FILE to a file whose first line is the key")
    return key


# ---- request conversion -------------------------------------------------------------------------------------------------
def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return "" if content is None else str(content)


def filler(chars: int) -> str:
    """A deterministic stand-in for Hermes' tool block: JSON-looking tool descriptions of exactly ``chars`` characters."""
    if chars <= 0:
        return ""
    unit = json.dumps({"name": "tool_{n:03d}", "description": "Run the tool with the given arguments and return its output.",
                       "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "text": {"type": "string"}}}})
    out, n = [], 0
    while sum(map(len, out)) < chars:
        out.append(unit.replace("{n:03d}", f"{n:03d}") + "\n")
        n += 1
    return "".join(out)[:chars]


def to_anthropic(messages: list[dict], *, run_id: str, tool_block_chars: int) -> tuple[list[dict], list[dict]]:
    """(system blocks, messages) for the Messages API. Tool calls and results become plain text: the point is the text
    the model is billed for, not a working tool loop. Roles alternate, the first is ``user`` and the last block of the
    system prompt and of the last three messages carries a cache breakpoint (Hermes' ``system_and_3`` layout)."""
    system_parts: list[str] = []
    merged: list[dict] = []

    def add(role: str, text: str) -> None:
        if merged and merged[-1]["role"] == role:
            merged[-1]["text"] += "\n\n" + text
        else:
            merged.append({"role": role, "text": text})

    tool_names: dict[str, str] = {}
    for m in messages:
        role = m.get("role")
        if role == "system":
            system_parts.append(_text(m.get("content")))
        elif role == "assistant":
            parts = [_text(m.get("content"))] if _text(m.get("content")) else []
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                tool_names[call.get("id", "")] = fn.get("name", "tool")
                parts.append(f"[tool call] {fn.get('name', 'tool')}({fn.get('arguments', '')})")
            add("assistant", "\n".join(parts) or "(no content)")
        elif role == "tool":
            add("user", f"[tool result: {tool_names.get(m.get('tool_call_id', ''), 'tool')}]\n{_text(m.get('content'))}")
        else:
            add("user", _text(m.get("content")))
    if not merged or merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "text": "(start of conversation)"})
    if merged[-1]["role"] != "user":
        merged.append({"role": "user", "text": "(continue)"})

    system_text = f"[run {run_id}]\n" + "\n\n".join(system_parts) + "\n\n" + filler(tool_block_chars)
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    out = [{"role": m["role"], "content": [{"type": "text", "text": m["text"]}]} for m in merged]
    for m in out[-(MAX_BREAKPOINTS - 1):]:
        m["content"][-1]["cache_control"] = {"type": "ephemeral"}
    return system, out


# ---- the provider ---------------------------------------------------------------------------------------------------------
Transport = Callable[[str, dict, bytes], tuple[int, dict]]


def _http(url: str, headers: dict, body: bytes) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except ValueError:
            return exc.code, {}


def price(usage: dict) -> dict:
    """Tokens and cost of one response, in dollars and in uncached-input-token equivalents (the replay's unit)."""
    fresh = int(usage.get("input_tokens") or 0)
    write = int(usage.get("cache_creation_input_tokens") or 0)
    read = int(usage.get("cache_read_input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    units = fresh + WRITE_MULT * write + READ_MULT * read
    usd = (units * USD_PER_MTOK_IN + out * USD_PER_MTOK_OUT) / 1e6
    return {"fresh": fresh, "write": write, "read": read, "out": out, "units": units, "usd": usd}


class Client:
    def __init__(self, key: str, transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        self._transport, self._sleep = transport or _http, sleep

    def send(self, system: list[dict], messages: list[dict], max_tokens: int) -> dict:
        body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "temperature": 0, "system": system, "messages": messages}).encode("utf-8")
        for attempt in range(6):
            status, payload = self._transport(URL, self._headers, body)
            if status == 200:
                return payload
            if status in (429, 500, 502, 503, 529) and attempt < 5:
                self._sleep(min(60, 4 * 2 ** attempt))
                continue
            kind = (payload.get("error") or {}).get("type", "error") if isinstance(payload, dict) else "error"
            raise RuntimeError(f"provider returned HTTP {status} ({kind})")      # never echo the request: it may hold the key header
        raise RuntimeError("provider kept failing")


# ---- one arm ---------------------------------------------------------------------------------------------------------------
def capture(arm: str, scenario: str, window: int, seed: int = 0, *, structural: bool = True, tool_chars: int = 39_993) -> dict:
    """Replay ``arm`` and return its recorded requests, its replay metrics and what the closing question needs."""
    import engines
    import protocol3
    import replay
    import scenario_registry as registry

    log: list[dict] = []
    saved = (replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS)
    if tool_chars:
        replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS = tool_chars, tool_chars // 4
    try:
        result = replay.run_session(lambda: engines.make_engine(arm), scenario=scenario, context_length=window, seed=seed,
                                    summarizer_factory=protocol3._summarizer_factory("crude"),
                                    script_transform=protocol3._transform(structural, 1.0), request_log=log)
    finally:
        replay.TOOLS_OVERHEAD_CHARS, replay.TOOLS_OVERHEAD_TOKENS = saved
    spec = registry.get_spec(scenario)
    script = spec.build(seed, spec.turns)
    transform = protocol3._transform(structural, 1.0)
    if transform is not None:
        script = transform(script, seed)
    needles = [g.needle for g in script.gold if g.needed]
    return {"arm": arm, "scenario": scenario, "window": window, "seed": seed, "requests": log,
            "replay_cost": result["metrics"]["total_simulated_input_cost"], "replay_requests": len(log),
            "question": script.final_question, "needles": needles}


def plan(recorded: dict) -> dict:
    """What a run would cost at most, without sending anything: every request priced as a full cache write."""
    chars = sum(sum(len(_text(m.get("content"))) for m in r["messages"]) + r["tool_block_chars"] for r in recorded["requests"])
    tokens = chars / 3.5
    return {"arm": recorded["arm"], "requests": len(recorded["requests"]), "approx_input_tokens": int(tokens),
            "usd_upper_bound": round(tokens * WRITE_MULT * USD_PER_MTOK_IN / 1e6, 4)}


def run_arm(recorded: dict, client: Client, *, max_usd: float, spent_before: float = 0.0, log: Callable[[str], None] = lambda _m: None) -> dict:
    """Send every recorded request in order. The last one is the closing question: its answer is scored."""
    run_id = uuid.uuid4().hex
    rows: list[dict] = []
    spent = spent_before
    answer = ""
    total = len(recorded["requests"])
    for index, request in enumerate(recorded["requests"], start=1):
        if spent >= max_usd:
            raise BudgetExceeded(f"stopped before request {index}/{total}: ${spent:.2f} reached the ${max_usd:.2f} cap")
        system, messages = to_anthropic(request["messages"], run_id=run_id, tool_block_chars=request["tool_block_chars"])
        last = index == total
        response = client.send(system, messages, PROBE_MAX_TOKENS if last else FILLER_MAX_TOKENS)
        cost = price(response.get("usage") or {})
        spent += cost["usd"]
        rows.append({"n": index, **{k: cost[k] for k in ("fresh", "write", "read", "out")}})
        if last:
            answer = "".join(block.get("text", "") for block in response.get("content") or [] if isinstance(block, dict))
        if index % 20 == 0:
            log(f"{recorded['arm']}: {index}/{total} requests, ${spent - spent_before:.2f}")
    totals = price({"input_tokens": sum(r["fresh"] for r in rows), "cache_creation_input_tokens": sum(r["write"] for r in rows),
                    "cache_read_input_tokens": sum(r["read"] for r in rows), "output_tokens": sum(r["out"] for r in rows)})
    needles = recorded["needles"]
    found = [n for n in needles if n in answer]
    return {"arm": recorded["arm"], "scenario": recorded["scenario"], "window": recorded["window"], "seed": recorded["seed"],
            "model": MODEL, "requests": total, "totals": totals, "replay_cost": recorded["replay_cost"],
            "needed": len(needles), "answered": len(found), "missing": [n for n in needles if n not in answer],
            "answer_chars": len(answer), "per_request": rows}


def summarise(results: list[dict]) -> str:
    """One table: real cost and answer accuracy per arm, and the ratio against the first arm, next to the replay's ratio."""
    lines = ["arm                      requests   real USD   units(M)   needed facts answered   replay cost ratio   real cost ratio"]
    base = results[0]
    for r in results:
        real = r["totals"]["units"] / base["totals"]["units"] if base["totals"]["units"] else float("nan")
        sim = r["replay_cost"] / base["replay_cost"] if base["replay_cost"] else float("nan")
        lines.append(f"{r['arm']:<24} {r['requests']:>8}   {r['totals']['usd']:>8.3f}   {r['totals']['units']/1e6:>8.3f}"
                     f"   {r['answered']:>2} of {r['needed']:<2}              {sim:>10.3f}          {real:>10.3f}")
    return "\n".join(lines)


def save(results: list[dict], path: str | Path) -> None:
    """Write the results. They hold counts and the closing answer's length, never a key and never request text."""
    clean = copy.deepcopy(results)
    Path(path).write_text(json.dumps(clean, indent=1), encoding="utf-8")
