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

import dataclasses
import json
import random
import re
from typing import Any

import engines
import replay
import scenario_registry as registry

CALL_OVERHEAD_CHARS = 300          # the assistant's call and the tool message's envelope, per read-back or re-run
READ_TOOL = "tameru_read_original"
_REF_RE = re.compile(r"ref=([0-9a-f]{8,16})")


class ExtractiveSummarizer(replay.FakeSummarizer):
    """A second stand-in for the summary call, closer to what a model does than the crude default.

    The default keeps the first 200 characters of every turn and nothing else. This one also keeps what a careful
    summary keeps: the user's own words (up to 600 characters, so a decision or a rule survives), and the identifiers
    in every record (paths, numbers of three digits or more, ids with dashes or underscores, hex strings, quoted
    strings), up to 260 characters per record, with a longer cap on the summary. It is still deterministic and still
    cannot reason: a claim that holds under both summarizers does not rest on the crudeness of one."""

    USER_CHARS = 600
    ANCHOR_CHARS = 260
    MAX_CHARS = 14_000
    _ANCHOR_RE = re.compile(
        r"(?:[\w./-]+\.[A-Za-z0-9]{1,6}\b|\b\d{3,}\b|\b[A-Za-z]+(?:[-_][A-Za-z0-9]+)+\b|\b[0-9a-f]{7,}\b|\"[^\"\n]{3,60}\"|'[^'\n]{3,60}')")

    def _entry(self, record: str) -> str:
        flat = " ".join(record.split())
        if record.startswith("[USER]:"):
            return flat[: self.USER_CHARS]
        head = flat[: replay.SUMMARY_FIRST_CHARS]
        anchors: list[str] = []
        used = 0
        for match in self._ANCHOR_RE.finditer(flat[replay.SUMMARY_FIRST_CHARS:]):
            piece = match.group(0)
            if piece in head or piece in anchors:
                continue
            if used + len(piece) + 1 > self.ANCHOR_CHARS:
                break
            anchors.append(piece)
            used += len(piece) + 1
        return head + (" | " + " ".join(anchors) if anchors else "")

    def __call__(self, **kwargs: Any) -> dict:
        prompt = kwargs["messages"][0]["content"]
        self.calls += 1
        self.prompt_chars += len(prompt)
        self.input_chars += sum(len(replay._wire(message)) + 1 for message in kwargs["messages"])
        records, prev_actions, prev_task = self._parse(prompt)
        if not records or not replay._RECORD_START_RE.match(records[0]):
            self.unparsed += 1
        users = [r for r in records if r.startswith("[USER]:")]
        task = users[-1][len("[USER]:"):].strip()[: self.USER_CHARS] if users else (prev_task or replay._NO_USER_TASK)
        entries = [a[: 2 * replay.SUMMARY_CARRY_CHARS] for a in prev_actions]
        entries += [self._entry(r) for r in records if r.strip()]
        kept: list[str] = []
        total = 0
        for entry in reversed(entries):
            if total + len(entry) > self.MAX_CHARS:
                break
            kept.append(entry)
            total += len(entry) + 4
        kept.reverse()
        text = ("## Historical Task Snapshot\n" + task + "\n\n## Goal\nContinue the work described above.\n\n"
                "## Completed Actions\n" + "\n".join(f"{i}. {e}" for i, e in enumerate(kept, 1)) + "\n")
        self.output_chars += len(text)
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def structural_variant(script, seed: int, *, spread: float = 0.4, grow: float = 1.0):
    """A different session with the same facts: the filler of every ``terminal`` output is thinned or repeated.

    The scenario builders vary a seed only in small ways (a seed changes filler text, not the shape of the work), so six seeds
    are close to one session six times. Here each terminal output of the script gets its own size factor in
    ``[1 - spread, 1 + spread]`` drawn from ``seed``: below 1 each filler line is dropped with that much probability, above 1 each is
    repeated. Never touched: a line holding any planted fact, a line the scenario counts as an error, and the first and last three
    lines. So gold facts and error templates stay exactly where they were and only the size and mix of the rest changes.
    ``grow`` multiplies every size factor (default 1: sizes stay centred on the script's); ``grow=4`` makes each terminal output about four
    times as long, so a shape whose script never fills a window (the holdouts) does. Seed 0 is the script as built unless ``grow`` is not 1.
    Deterministic. Only ``terminal`` and ``read_file`` results are changed (a read's numbered lines are renumbered)."""
    if seed == 0 and grow == 1.0:
        return script
    needles = [g.needle for g in script.gold]
    errors = {line.strip() for line in script.error_lines}
    rng = random.Random(f"structural:{seed}")

    gutter = re.compile(r"^(\d+)\|(.*)$")

    def vary(step):
        if step.name not in ("terminal", "read_file"):
            return step
        try:
            body = json.loads(step.result)
            field = "output" if step.name == "terminal" else "content"
            lines = body[field].split("\n")
        except (TypeError, ValueError, KeyError, AttributeError):
            return step
        first_number = None
        if step.name == "read_file":                      # ``N|text`` lines: vary the text, number them again afterwards
            matches = [gutter.match(line) for line in lines]
            if not all(matches):
                return step
            first_number = int(matches[0].group(1))
            lines = [m.group(2) for m in matches]
        factor = rng.uniform(1 - spread, 1 + spread) * grow
        out = []
        for index, line in enumerate(lines):
            keep = (index < 3 or index >= len(lines) - 3 or line.strip() in errors or any(n in line for n in needles))
            if keep:
                out.append(line)
            elif factor < 1:
                if rng.random() < factor:
                    out.append(line)
            else:
                copies = int(factor) + (1 if rng.random() < factor - int(factor) else 0)      # 1 + (a chance of one more) up to x1.4
                out.extend([line] * copies)
        if first_number is not None:
            body[field] = "\n".join(f"{first_number + i}|{line}" for i, line in enumerate(out))
            if isinstance(body.get("total_lines"), int):
                body["total_lines"] = max(body["total_lines"], first_number - 1 + len(out))
            if isinstance(body.get("file_size"), int):
                body["file_size"] = len("\n".join(out).encode("utf-8"))
        else:
            body[field] = "\n".join(out)
        return dataclasses.replace(step, result=json.dumps(body, ensure_ascii=False))

    turns = [dataclasses.replace(turn, rounds=tuple(
        dataclasses.replace(rnd, steps=tuple(vary(step) for step in rnd.steps)) for rnd in turn.rounds)) for turn in script.turns]
    varied = dataclasses.replace(script, turns=turns)
    for fact in varied.gold:                                   # the guard every scenario builder has: each planted fact is still there
        if fact.source == "tool":
            assert any(fact.needle in step.result or json.dumps(fact.needle, ensure_ascii=False)[1:-1] in step.result
                       for turn in varied.turns for rnd in turn.rounds for step in rnd.steps if step.key == fact.call_key), fact.id
    return varied


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


# Idle profiles: the pause before each user turn after the first, with the cache lifetime that makes it matter.
#   none         today's replay: no pauses, the cache never expires
#   interactive  45 s of think time before every turn: the cache (5 minutes) outlives every pause
#   mixed        10 minutes before every third turn, 45 s otherwise: now and then the cache is gone
#   slow         10 minutes before every turn: the cache is gone at every user turn
IDLE_PROFILES = {
    "interactive": lambda turn: 45.0,
    "mixed": lambda turn: 600.0 if turn % 3 == 0 else 45.0,
    "slow": lambda turn: 600.0,
}
CACHE_TTL_SECONDS = 300.0


def _transform(structural: bool, grow: float):
    if not structural and grow == 1.0:
        return None
    return lambda script, seed: structural_variant(script, seed, grow=grow)


def idle_options(profile: str) -> dict:
    if profile == "none":
        return {}
    return {"cache_ttl_s": CACHE_TTL_SECONDS, "idle_seconds": IDLE_PROFILES[profile]}


def measure(arm: str, scenario: str, window: int, seed: int = 0, *, summarizer: str = "crude", structural: bool = False,
            idle: str = "none", grow: float = 1.0) -> dict:
    """One replay of ``arm`` on ``scenario`` at ``window``, with the needed facts sorted and priced."""
    made: list[Any] = []

    def factory():
        made.append(engines.make_engine(arm))
        return made[-1]

    result = replay.run_session(factory, scenario=scenario, context_length=window, seed=seed, keep_final_request=True,
                                summarizer_factory=ExtractiveSummarizer if summarizer == "extractive" else None,
                                script_transform=_transform(structural, grow), **idle_options(idle))
    metrics = result["metrics"]
    final = result.get("final_request") or []
    spec = registry.get_spec(scenario)
    script = spec.build(seed, spec.turns)
    transform = _transform(structural, grow)
    if transform is not None:
        script = transform(script, seed)
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
        "arm": arm, "scenario": scenario, "window": window, "seed": seed, "summarizer": summarizer, "structural": structural, "idle": idle, "grow": grow,
        "compactions": metrics["compactions"], "failed_turns": metrics["failed_turns"], "base": round(base / answered, 1), "effective": round((base + extra) / answered, 1),
        "needed": len(kept) + len(recoverable) + len(lost),
        "kept": len(kept), "recoverable": len(recoverable), "lost": len(lost), "lost_unrecoverable": len(unrecoverable),
        "kept_ids": kept, "recoverable_ids": recoverable, "lost_ids": lost,
    }
