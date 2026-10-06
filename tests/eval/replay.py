"""Deterministic, offline replay of a 30-turn simulated Hermes session against a context engine.

``run_session(engine_factory, turns=30, seed=0)`` drives ``scenario.build_scenario`` through a loop that
mimics the Hermes conversation loop (``agent/turn_preflight.py``):

    before every model request:
        est = rough token estimate of the transcript (+ fixed tool-schema overhead)
        if engine.should_compress(est):        # not while awaiting real usage after a compaction
            messages = engine.compress(messages, current_tokens=est)
        elif the request follows tool results:  # compress_after_tool_results ONLY; never a turn-start preflight
            new, n = engine.prune_tool_results_only(messages, current_tokens=est)
            if n and new is not messages:       # Hermes only commits a NEW list with a non-zero count
                messages = new
    optionally engine.select_context(...) (request-only), send the request (record metrics),
    then engine.update_from_response(usage); engine.on_turn_complete(...) after each user turn

Hermes runs a check per iteration of its tool loop: before the first request of a user turn
(``run_preflight_compression``: the compress gate only, never a prune) and after every tool round
(``compress_after_tool_results``: the compress gate, else the proactive prune). A turn with R tool rounds
makes R + 1 requests: the first is a preflight, the other R follow tool results.

No network: the summary LLM that ``ContextCompressor`` calls (``agent.context_compressor.call_llm`` via
``_call_summary_llm``) is replaced by a deterministic, lossy fake (first 200 chars of every turn; the
previous summary's entries are cut to 120 chars on each iterative update). Hermes' own deterministic
summary additions (anchor index, verbatim user messages, recovery footer) are still applied.

Metrics (see tests/eval/README.md): input chars per request, cache-priced cost, compaction counts,
invalidated prefix, gold retention per Factory probe category, error-fingerprint retention, re-fetch
opportunities, wall time in the engine, tool-call pairing integrity of every request.

Hermes is imported lazily so this module can be imported (and skipped) without it.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scenario import CATEGORIES, N_TURNS, Scenario, build_scenario

# --- simulation constants (documented in tests/eval/README.md) -------------------------------------
MODEL = "test-model"
CONTEXT_LENGTH = 64_000  # tiny window so a 30-turn session needs several compactions
TOOLS_OVERHEAD_TOKENS = 3_000  # tool schemas ride every request but are not in ``messages``
CACHE_READ = 0.10  # cached prefix chars cost 0.10x
CACHE_WRITE = 1.25  # new (uncached) chars cost 1.25x
SUMMARY_FIRST_CHARS = 200  # the fake summarizer keeps this much of every turn
SUMMARY_CARRY_CHARS = 120  # ... and this much of each entry of the previous summary
SUMMARY_MAX_CHARS = 9_000  # ... and never more than this per summary
WIRE_KEYS = ("role", "content", "name", "tool_calls", "tool_call_id")  # what reaches the provider


# =================================================================================================
# Error fingerprints (the contract of the engine's ``error_fingerprints`` helper, reimplemented here
# so the harness never depends on the engine under test)
# =================================================================================================
_LEVEL_RE = re.compile(r"\b(?:ERROR|FATAL|CRITICAL|PANIC|FAIL(?:ED|URE)?)\b")
_EXC_RE = re.compile(r"^\s*(?:\w+\.)*\w*(?:Error|Exception):")
_NOT_AN_ERROR_RE = re.compile(
    r"\b(?:0|no|zero)\s+(?:errors?|failures?|failed|fatals?)\b"
    r"|\b(?:errors?|failures?|failed|fatals?|critical)\s*[=:]\s*0\b",
    re.IGNORECASE,
)
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX_RE = re.compile(r"[0-9a-f]{6,}")
_DIGITS_RE = re.compile(r"\d+")
_COLLAPSE_RE = re.compile(r"^\s*\[\u00d7\d+\]\s*")
_LINE_CAP = 2_000  # the engine looks at no more than this much of a line (``_RX_LINE_CAP``)


def _is_error_line(line: str) -> bool:
    line = line[:_LINE_CAP]
    if _NOT_AN_ERROR_RE.search(line):
        return False
    return bool(
        _LEVEL_RE.search(line)
        or _EXC_RE.match(line)
        or "Traceback (most recent call last)" in line
        or re.search(r"\bpanic:", line)
    )


def error_fingerprint(line: str) -> str:
    """Strip a leading ``[×N] ``, lowercase, mask uuids / hex runs of 6+ / digit runs, collapse spaces, cap at 160."""
    line = _COLLAPSE_RE.sub("", line[:_LINE_CAP], count=1).lower()
    line = _UUID_RE.sub("<uuid>", line)
    line = _HEX_RE.sub("<hex>", line)  # before digits, so ids differing only in digit placement collide
    line = _DIGITS_RE.sub("#", line)
    return " ".join(line.split())[:160]


def error_fingerprints(text: str) -> dict[str, str]:
    """``{fingerprint: first exemplar line}`` of the level-tagged error lines of ``text``, in original order."""
    out: dict[str, str] = {}
    # only \n, \r\n and \r end a line (str.splitlines() would also split on \x0c, U+2028, ...), like the engine
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _is_error_line(line):
            fp = error_fingerprint(line)
            if fp:
                out.setdefault(fp, line)
    return out


# =================================================================================================
# Message helpers
# =================================================================================================
def _wire(msg: dict) -> str:
    """The provider-visible serialization of one message (internal underscore keys etc. dropped)."""
    return json.dumps({k: msg[k] for k in WIRE_KEYS if k in msg}, ensure_ascii=False, sort_keys=True)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p if isinstance(p, str) else str(p.get("text", "")) for p in content if isinstance(p, (str, dict))
        )
    return "" if content is None else str(content)


def visible_text(messages: Iterable[dict]) -> str:
    """Everything the model could read in a request: contents and tool-call arguments.

    Tool results are JSON strings; the text is searched both raw and with JSON newline/quote escapes
    undone, so a fact survives whether an engine kept the envelope or unwrapped it.
    """
    parts: list[str] = []
    for m in messages:
        c = _content_text(m.get("content"))
        parts.append(c)
        if "\\n" in c or '\\"' in c:
            parts.append(c.replace("\\n", "\n").replace('\\"', '"'))
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            args = str(fn.get("arguments", ""))
            parts.append(f"{fn.get('name', '')} {args}")
            if "\\n" in args:
                parts.append(args.replace("\\n", "\n"))
    return "\n".join(parts)


def pairing_violations(messages: list[dict]) -> list[str]:
    """Tool-call/result pairing problems in one request (empty list = intact)."""
    problems: list[str] = []
    declared: dict[str, int] = {}
    answered: dict[str, int] = {}
    block: set[str] = set()  # the calls of the assistant message that opened the current run of tool rows
    seen_non_system = False
    for i, m in enumerate(messages):
        role = m.get("role")
        if role != "system":
            if not seen_non_system and role == "tool":
                problems.append(f"leading tool message at {i}")
            seen_non_system = True
        if role != "tool":
            block = set()  # providers want the results immediately after the assistant message
        for tc in m.get("tool_calls") or []:
            tid = tc.get("id") if isinstance(tc, dict) else None
            if not tid:
                problems.append(f"tool_call without id in message {i}")
                continue
            if tid in declared:
                problems.append(f"duplicate tool_call id {tid}")
            declared[tid] = i
            block.add(tid)
        if role == "tool":
            tid = m.get("tool_call_id")
            if tid not in declared:
                problems.append(f"orphan tool result {tid} at {i}")
            elif tid not in block:
                problems.append(f"tool result {tid} at {i} does not directly follow its call")
            if tid in answered:
                problems.append(f"duplicate tool result {tid}")
            answered[tid] = i
    for tid in declared:
        if tid not in answered:
            problems.append(f"unanswered tool_call {tid}")
    return problems


def _common_prefix_len(a: str, b: str) -> int:
    """Length of the longest common prefix (binary search over C-speed slice compares)."""
    n = min(len(a), len(b))
    if a[:n] == b[:n]:
        return n
    lo, hi = 0, n  # invariant: a[:lo] == b[:lo], a[:hi] != b[:hi]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid
    return lo


# =================================================================================================
# Fake summary LLM
# =================================================================================================
_RECORD_SPLIT_RE = re.compile(r"\n\n(?=\[(?:USER|ASSISTANT|SYSTEM|TOOL RESULT [^\]\n]*)\]: )")
_RECORD_START_RE = re.compile(r"\[(?:USER|ASSISTANT|SYSTEM|TOOL RESULT [^\]\n]*)\]: ")
_NO_USER_TASK = "None. This session contains no user-authored turns."


class FakeSummarizer:
    """Stands in for ``agent.context_compressor.call_llm`` (the aux summary call). Deterministic, lossy."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_chars = 0
        self.output_chars = 0
        self.unparsed = 0  # prompts whose turn records the fake could not recognise (Hermes changed the format)

    @staticmethod
    def _parse(prompt: str) -> tuple[list[str], list[str], str | None]:
        """(new records, previous 'Completed Actions' entries, previous task snapshot)."""
        prev_actions: list[str] = []
        prev_task: str | None = None
        m = re.search(r"PREVIOUS SUMMARY:\n(.*?)\n\nNEW TURNS TO INCORPORATE:\n", prompt, re.DOTALL)
        if m:
            prev = m.group(1)
            sec = re.search(r"(?ms)^## Completed Actions\n(.*?)(?=^## |\Z)", prev)
            if sec:
                prev_actions = [re.sub(r"^\d+\.\s*", "", ln) for ln in sec.group(1).splitlines() if ln.strip()]
            task = re.search(r"(?ms)^## Historical Task Snapshot\n(.*?)(?=^## |\Z)", prev)
            if task:
                prev_task = task.group(1).strip() or None
        start = re.search(r"(?:NEW TURNS TO INCORPORATE|TURNS TO SUMMARIZE):\n", prompt)
        body = prompt[start.end():] if start else ""
        end = re.search(r"\n\n(?:Update the summary using|Use this exact structure)", body)
        body = body[: end.start()] if end else body
        return _RECORD_SPLIT_RE.split(body), prev_actions, prev_task

    def __call__(self, **kwargs: Any) -> dict:
        prompt = kwargs["messages"][0]["content"]
        self.calls += 1
        self.prompt_chars += len(prompt)
        records, prev_actions, prev_task = self._parse(prompt)
        if not records or not _RECORD_START_RE.match(records[0]):
            self.unparsed += 1  # would silently summarise the wrong text; the replay tests assert this stays 0
        users = [r for r in records if r.startswith("[USER]:")]
        if users:
            task = users[-1][len("[USER]:"):].strip()[:SUMMARY_FIRST_CHARS]
        else:
            task = prev_task or _NO_USER_TASK
        entries = [a[:SUMMARY_CARRY_CHARS] for a in prev_actions]
        entries += [" ".join(r.split())[:SUMMARY_FIRST_CHARS] for r in records if r.strip()]
        kept: list[str] = []
        total = 0
        for e in reversed(entries):  # keep the newest entries within the cap
            if total + len(e) > SUMMARY_MAX_CHARS:
                break
            kept.append(e)
            total += len(e) + 4
        kept.reverse()
        text = (
            "## Historical Task Snapshot\n" + task + "\n\n## Goal\nContinue the work described above.\n\n"
            "## Completed Actions\n" + "\n".join(f"{i}. {e}" for i, e in enumerate(kept, 1)) + "\n"
        )
        self.output_chars += len(text)
        route = kwargs.get("route_info")
        if isinstance(route, dict):
            route.update({"provider": "fake", "model": "fake-summarizer"})
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


@contextmanager
def patched_summary_llm(summarizer: FakeSummarizer):
    """Replace the summary LLM call with ``summarizer`` for the duration of the block."""
    import agent.context_compressor as cc

    original = cc.call_llm
    cc.call_llm = summarizer
    try:
        yield summarizer
    finally:
        cc.call_llm = original


# =================================================================================================
# The session loop
# =================================================================================================
def prepare_engine(engine: Any) -> None:
    """Configure the engine the way Hermes' ``agent_init`` does: tiny window, then session start."""
    engine.update_model(model=MODEL, context_length=CONTEXT_LENGTH)
    engine.on_session_start("replay-session")


class _Session:
    def __init__(self, engine: Any, scenario: Scenario | None, estimate: Callable[[list[dict]], int] | None = None) -> None:
        if estimate is None:  # Hermes' own rough estimator, as the host uses before a request
            from agent.model_metadata import estimate_messages_tokens_rough as estimate
        self._estimate = estimate
        self.engine = engine
        self.scenario = scenario
        # metrics
        self.request_chars: list[int] = []
        self.cached_chars: list[int] = []
        self.invalidated_chars: list[int] = []
        self.prefix_breaks = 0
        self.cost_x100 = 0  # integer "centi-chars" so sums are exact
        self.cost_uncached = 0
        self.violations: list[str] = []
        self.events: list[dict] = []
        self.compress_calls = 0
        self.compactions = 0
        self.compactions_shrinking = 0
        self.prune_calls = 0
        self.prune_commits = 0
        self.prune_rows = 0
        self.engine_seconds = 0.0
        self.engine_seconds_by_op = {"should_compress": 0.0, "compress": 0.0, "prune": 0.0}
        self._prev_wire: list[str] | None = None
        self._prev_total = 0
        self.last_request: list[dict] = []
        self.last_usage: dict | None = None
        self._prev_rows: dict[str, str] | None = None
        self.rewrites: list[str] = []  # tool rows whose bytes changed in a request that committed nothing

    # -- Hermes' pre-request check --------------------------------------------------------------
    def preflight(self, messages: list[dict], *, after_tools: bool = False) -> list[dict]:
        """Hermes' pre-request gate. ``after_tools``: the request follows tool results (the post-tool path,
        the only one that may prune); otherwise it is the turn-start preflight (compress gate only)."""
        eng = self.engine
        est = self._estimate(messages) + TOOLS_OVERHEAD_TOKENS
        if getattr(eng, "awaiting_real_usage_after_compression", False):
            return messages  # a fresh checkpoint must reach the provider before any gate acts on it
        t0 = time.perf_counter()
        should = bool(eng.should_compress(est))
        dt = time.perf_counter() - t0
        self.engine_seconds_by_op["should_compress"] += dt
        self.engine_seconds += dt
        if should:
            before = sum(len(_wire(m)) for m in messages)
            t0 = time.perf_counter()
            out = eng.compress(messages, current_tokens=est)
            dt = time.perf_counter() - t0
            self.engine_seconds_by_op["compress"] += dt
            self.engine_seconds += dt
            self.compress_calls += 1
            if out is not messages:
                self.compactions += 1
                self.compactions_shrinking += len(out) < len(messages)
                self.events.append(
                    {"request": len(self.request_chars), "kind": "compress", "messages_before": len(messages),
                     "messages_after": len(out), "chars_before": before, "chars_after": sum(len(_wire(m)) for m in out)}
                )
                messages = out
            else:
                self.events.append({"request": len(self.request_chars), "kind": "compress_noop"})
            return messages
        prune = getattr(eng, "prune_tool_results_only", None)
        if after_tools and callable(prune):
            before = sum(len(_wire(m)) for m in messages)
            t0 = time.perf_counter()
            new, n = prune(messages, current_tokens=est)
            dt = time.perf_counter() - t0
            self.engine_seconds_by_op["prune"] += dt
            self.engine_seconds += dt
            self.prune_calls += 1
            if n and new is not messages:  # Hermes' no-op contract: only a NEW list with n > 0 commits
                self.prune_commits += 1
                self.prune_rows += int(n)
                self.events.append(
                    {"request": len(self.request_chars), "kind": "prune", "rows": int(n),
                     "chars_before": before, "chars_after": sum(len(_wire(m)) for m in new)}
                )
                messages = new
        return messages

    # -- optional ContextEngine hooks (Hermes: agent/conversation_loop.py) ---------------------------
    def _overrides(self, name: str) -> bool:
        """True when the engine implements hook ``name`` itself (the ABC's no-op default costs nothing)."""
        hook = getattr(self.engine, name, None)
        if not callable(hook):
            return False
        from agent.context_engine import ContextEngine

        return getattr(hook, "__func__", None) is not getattr(ContextEngine, name)

    def _select(self, messages: list[dict]) -> list[dict]:
        """``select_context``: a request-only replacement of the context, fail-open like Hermes."""
        if not self._overrides("select_context"):
            return messages
        selected = self.engine.select_context(
            list(messages),
            conversation_messages=list(messages),
            incoming_message=None,
            budget_tokens=int(getattr(self.engine, "context_length", 0) or 0),
        )
        if isinstance(selected, list) and selected and all(isinstance(m, dict) for m in selected):
            return selected
        return messages

    def turn_complete(self, messages: list[dict]) -> None:
        if self._overrides("on_turn_complete"):
            self.engine.on_turn_complete(list(messages), usage=self.last_usage)

    def _check_stability(self, messages: list[dict], *, committed: bool) -> None:
        """Hermes only rewrites history inside a commit (a prune or a compaction): between commits every
        tool row must keep its bytes, or the prompt cache breaks for nothing."""
        rows = {
            str(m["tool_call_id"]): m["content"] for m in messages
            if m.get("role") == "tool" and m.get("tool_call_id") and isinstance(m.get("content"), str)
        }
        if self._prev_rows is not None and not committed:
            for cid, content in rows.items():
                if cid in self._prev_rows and self._prev_rows[cid] != content:
                    self.rewrites.append(f"request {len(self.request_chars) + 1}: {cid}")
        self._prev_rows = rows

    # -- one model request ----------------------------------------------------------------------
    def request(self, messages: list[dict]) -> list[dict]:
        """Preflight (may compact the persisted transcript), then send. Returns the persisted transcript."""
        before = messages
        messages = self.preflight(messages, after_tools=bool(messages) and messages[-1].get("role") == "tool")
        self._check_stability(messages, committed=messages is not before)
        sent = self._select(messages)
        wire = [_wire(m) for m in sent]
        total = sum(len(w) + 1 for w in wire)
        if self._prev_wire is None:
            lcp = 0
        else:
            lcp = 0
            for a, b in zip(self._prev_wire, wire):
                if a == b:
                    lcp += len(a) + 1
                else:
                    lcp += _common_prefix_len(a, b)
                    break
            lcp = min(lcp, self._prev_total, total)
        new_chars = total - lcp
        self.cost_x100 += round(CACHE_READ * 100) * lcp + round(CACHE_WRITE * 100) * new_chars
        self.cost_uncached += 100 * total
        invalidated = (self._prev_total - lcp) if self._prev_wire is not None else 0
        if invalidated > 0:
            self.prefix_breaks += 1
        self.request_chars.append(total)
        self.cached_chars.append(lcp)
        self.invalidated_chars.append(invalidated)
        for v in pairing_violations(sent):
            self.violations.append(f"request {len(self.request_chars)}: {v}")
        self._prev_wire, self._prev_total = wire, total
        self.last_request = sent
        # provider usage for this request (rough tokens + overhead), fed back like Hermes does
        prompt_tokens = self._estimate(sent) + TOOLS_OVERHEAD_TOKENS
        self.last_usage = {"prompt_tokens": prompt_tokens, "completion_tokens": 120, "total_tokens": prompt_tokens + 120}
        self.engine.update_from_response(dict(self.last_usage))
        return messages


def _assistant_with_calls(say: str, steps, ids: list[str]) -> dict:
    return {
        "role": "assistant",
        "content": say,
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": s.name, "arguments": json.dumps(s.args, ensure_ascii=False)}}
            for cid, s in zip(ids, steps)
        ],
    }


def _tool_row(cid: str, step) -> dict:
    return {"role": "tool", "name": step.name, "tool_name": step.name, "content": step.result, "tool_call_id": cid}


def run_session(
    engine_factory: Callable[[], Any], *, turns: int = N_TURNS, seed: int = 0, keep_final_request: bool = False
) -> dict:
    """Replay the scripted session against ``engine_factory()`` and return the metrics.

    Returns ``{"meta", "metrics", "series", "timing"[, "final_request"]}``. ``metrics`` and ``series`` are
    fully deterministic for a given (engine, turns, seed); ``timing`` is wall-clock and is not.
    """
    scenario = build_scenario(seed, turns)
    engine = engine_factory()
    prepare_engine(engine)
    sess = _Session(engine, scenario)
    summarizer = FakeSummarizer()
    call_counter = 0

    def new_call_id() -> str:
        nonlocal call_counter
        call_counter += 1
        return f"call_{seed:02d}{call_counter:06d}"

    messages: list[dict] = [{"role": "system", "content": scenario.system_prompt}]
    with patched_summary_llm(summarizer):
        for turn in scenario.turns:
            messages.append({"role": "user", "content": turn.user})
            for rnd in turn.rounds:
                messages = sess.request(messages)
                ids = [new_call_id() for _ in rnd.steps]
                messages.append(_assistant_with_calls(rnd.say, rnd.steps, ids))
                for cid, st in zip(ids, rnd.steps):
                    messages.append(_tool_row(cid, st))
            messages = sess.request(messages)
            messages.append({"role": "assistant", "content": turn.final})
            sess.turn_complete(messages)
        messages.append({"role": "user", "content": scenario.final_question})
        messages = sess.request(messages)
    final = sess.last_request
    final_text = visible_text(final)

    # ---- gold retention -----------------------------------------------------------------------
    by_cat: dict[str, dict] = {}
    for cat in CATEGORIES:
        facts = [g for g in scenario.gold if g.category == cat]
        kept = [g.id for g in facts if g.needle in final_text]
        by_cat[cat] = {
            "total": len(facts),
            "retained": len(kept),
            "retention": round(len(kept) / len(facts), 4) if facts else None,
            "missing": [g.id for g in facts if g.needle not in final_text],
        }
    n_total = len(scenario.gold)
    n_kept = sum(1 for g in scenario.gold if g.needle in final_text)
    needed = [g for g in scenario.gold if g.needed]
    missing_needed = [g for g in needed if g.needle not in final_text]

    # ---- error fingerprints from the large incident log ---------------------------------------
    log_fps = set(error_fingerprints("\n".join(scenario.error_lines)))
    final_fps = set(error_fingerprints(final_text))
    kept_fps = log_fps & final_fps

    metrics = {
        "turns": turns,
        "requests": len(sess.request_chars),
        "context_length": CONTEXT_LENGTH,
        "threshold_tokens": int(getattr(engine, "threshold_tokens", 0) or 0),
        "input_chars_total": sum(sess.request_chars),
        "input_chars_max": max(sess.request_chars),
        "input_chars_final": sess.request_chars[-1],
        "cost_cache_priced": sess.cost_x100 / 100,
        "cost_uncached": sess.cost_uncached / 100,
        "cost_ratio_vs_uncached": round(sess.cost_x100 / sess.cost_uncached, 6),
        "cached_chars_total": sum(sess.cached_chars),
        "compress_calls": sess.compress_calls,
        "compactions": sess.compactions,
        "compactions_shrinking": sess.compactions_shrinking,
        "summary_llm_calls": summarizer.calls,
        "summary_unparsed_prompts": summarizer.unparsed,
        "summary_prompt_chars": summarizer.prompt_chars,
        "prune_calls": sess.prune_calls,
        "prune_commits": sess.prune_commits,
        "prune_rows_demoted": sess.prune_rows,
        "prefix_invalidated_chars": sum(sess.invalidated_chars),
        "prefix_break_events": sess.prefix_breaks,
        "messages_final": len(final),
        "gold": {
            "total": n_total,
            "retained": n_kept,
            "retention": round(n_kept / n_total, 4) if n_total else None,
            "by_category": by_cat,
        },
        "error_fingerprints": {
            "total": len(log_fps),
            "retained": len(kept_fps),
            "retention": round(len(kept_fps) / len(log_fps), 4) if log_fps else None,
        },
        "refetch": {
            "needed_facts": len(needed),
            "missing_needed_facts": len(missing_needed),
            "refetch_rate": round(len(missing_needed) / len(needed), 4) if needed else None,
            "missing_ids": [g.id for g in missing_needed],
            "refetch_opportunities": len({g.call_key for g in missing_needed if g.call_key}),
        },
        "pairing": {"requests_checked": len(sess.request_chars), "violations": len(sess.violations),
                    "details": sess.violations[:10]},
        "byte_stability": {"requests_checked": len(sess.request_chars), "rewrites_outside_commits": len(sess.rewrites),
                           "details": sess.rewrites[:10]},
    }
    series = {
        "request_chars": sess.request_chars,
        "cached_chars": sess.cached_chars,
        "invalidated_chars": sess.invalidated_chars,
        "events": sess.events,
    }
    result = {
        "meta": {"engine": type(engine).__name__, "engine_name": getattr(engine, "name", None), "seed": seed,
                 "turns": turns, "hermes_pin": "1298c8e74baa73e1a2b90124228d017261ac6bc4"},
        "metrics": metrics,
        "series": series,
        "timing": {
            "engine_seconds": round(sess.engine_seconds, 3),
            "by_op": {k: round(v, 3) for k, v in sess.engine_seconds_by_op.items()},
        },
    }
    if keep_final_request:
        result["final_request"] = final
    return result


def deterministic_part(result: dict) -> dict:
    """The comparable (non-timing) part of a ``run_session`` result."""
    return {"metrics": result["metrics"], "series": result["series"]}
