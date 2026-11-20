"""Round-2 session corpus -> this replay's ``Scenario`` (P2c; dev and validation only, the holdout is refused).

The engine repository exports the host-neutral corpus (``python -m benchmarks.session_corpus.build --split dev``, one canonical JSON
file per scenario; ``benchmarks/session_corpus/SCHEMA.md`` and ``ADAPTERS.md`` there). This module reads such an export by path; it
never imports the corpus package and never generates text.

Mapping (ADAPTERS.md, "Hermes replay"):

* the window slice is ``steps[0 : windows[str(W)]["steps"]]`` (the corpus's ``steps_for_window``); the questions answerable at W are
  those with ``first_window <= W``;
* turn 1's user message is the scenario ``task``; steps are grouped into rounds of one tool call and turns of ``STEPS_PER_TURN`` steps;
  later user messages are the neutral ``继续`` (zh / mixed) or ``continue`` (en); the closing question is the answerable question set;
* tool calls get Hermes' envelope: ``bash`` -> ``terminal`` (``{"output", "exit_code", "error"}``, exit code 1 for an error step),
  ``read_file`` -> ``read_file`` (``N|`` gutter, Hermes' key order); ``gh_run_view``, ``kubectl_logs``, ``sql_query`` and
  ``ansible_playbook`` -> ``terminal`` running ``gh run view`` / ``kubectl logs`` / ``psql -c`` / ``ansible-playbook`` (Hermes has no
  such tools; it runs them in the terminal);
  ``ToolStep.key = call_key(tool, args, nth)`` with ``nth`` the step index for a repeated command;
* each fact of an answerable question becomes a ``GoldFact`` (category ``recall``, source ``tool``, needed, the turn of its step); its
  ``needle`` is the fact line as it appears inside the JSON envelope (``json.dumps`` escaping), so the kit's drift guard holds.

The fact check is ADAPTERS.md's, not a bare substring of one form: ``fact_held`` is true when the full fact line is in the final
prompt verbatim (raw, as an engine extract shows it, or JSON-escaped, as the untouched envelope shows it), or when one line of the
prompt (whitespace collapsed) holds both ``key`` and ``value`` (a real fact: its ``value`` line). ``value_present`` is the secondary
column. An ``aggregate`` question needs every fact; a ``superseded`` question's fact is the newest line (the stale line is a separate
``stale`` fact that no question names); an ``absent`` question has no fact and is left to answer probes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from scenario import GoldFact, Round, Scenario, ToolStep, Turn
from scenario_kit import call_key

STEPS_PER_TURN = 6
GENERATOR = "session-corpus-3"
WINDOWS = (128_000, 200_000, 252_000, 500_000, 1_000_000)      # the corpus's window table
# The sealed split's families (engine splits.json, deal attempt 10). Family names are public; their text is never read here.
HOLDOUT_FAMILIES = frozenset({
    "apijson", "apt", "configdump", "csvexport", "docker", "dotnet", "eslint", "gitdiff", "gradle", "helm", "javac", "journald",
    "k8sevents", "markdown", "mypy", "nginx", "npm", "phpunit", "pip", "promtext", "terraform", "trace-csharp", "trace-go",
    "trace-java", "tsc",
})
ALLOWED_SPLITS = ("dev", "validation")
BURNED_ROUND1_SHA256 = "ac9e77dd723d3755b8238c4a1e60d6947e798ddcc0ee489e9d2c10f2b5ca1489"
CONTINUE = {"en": "continue", "zh": "继续", "mixed": "继续"}
SAY = ""                                   # the assistant's text beside a tool call (none, as a terse agent sends)
FINAL = {"en": "Done with this batch; continuing.", "zh": "这一批完成了，继续。", "mixed": "这一批完成了，继续。"}
SYSTEM_PROMPT = (
    "You are Hermes, an autonomous software engineering agent working in the user's repository.\n"
    "Use the available tools (terminal, read_file) to inspect the project and report what the user asks for.\n"
    "Prefer small, verifiable steps. Never invent output; run or read it first.\n"
    "When a tool result is large, summarise what matters for the task instead of repeating it.\n"
) * 6


class HoldoutRefused(RuntimeError):
    """The loader never reads the sealed holdout (or a burned registration)."""


# ------------------------------------------------------------------------------------------------ fact check
_WS_RE = re.compile(r"\s+")


def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def escaped(line: str) -> str:
    """The line as it appears inside a JSON string (``json.dumps`` without the quotes, non-ASCII kept)."""
    return json.dumps(line, ensure_ascii=False)[1:-1]


def prompt_lines(text: str) -> list[str]:
    """The prompt's lines, whitespace collapsed; a JSON envelope's ``\\n`` escapes count as line breaks too."""
    out = []
    for line in text.split("\n"):
        for part in line.split("\\n"):
            c = _collapse(part)
            if c:
                out.append(c)
    return out


@dataclass(frozen=True)
class FactRef:
    """One fact of an answerable question, located in the sliced session."""

    id: str
    kind: str                 # natural | error | buried | real
    key: str
    value: str
    line: str                 # output_text.split("\n")[line_index]
    step: int                 # 0-based step index
    turn: int                 # 1-based user turn holding the step
    call_key: str
    position: str             # early | middle | late (step / steps(W), thirds)


def fact_held(fact: FactRef, text: str, lines: list[str] | None = None) -> bool:
    """ADAPTERS.md's check on the final prompt ``text``."""
    if fact.line and (fact.line in text or escaped(fact.line) in text):
        return True
    lines = prompt_lines(text) if lines is None else lines
    if fact.kind == "real":
        want = _collapse(fact.value)
        return bool(want) and any(want in line for line in lines)
    key, value = _collapse(fact.key), _collapse(fact.value)
    return bool(key and value) and any(key in line and value in line for line in lines)


def value_present(fact: FactRef, text: str) -> bool:
    return bool(fact.value) and (fact.value in text or escaped(fact.value) in text)


# ------------------------------------------------------------------------------------------------ loading
def _refuse_path(path: Path) -> None:
    parts = {p.lower() for p in Path(path).resolve().parts}
    if "holdout" in parts:
        raise HoldoutRefused(f"{path}: the holdout export is sealed; this loader reads dev and validation only")


def _refuse_scenario(raw: dict, source: str) -> None:
    if raw.get("family") in HOLDOUT_FAMILIES:
        raise HoldoutRefused(f"{source}: family {raw.get('family')!r} belongs to the sealed holdout")
    if BURNED_ROUND1_SHA256 in json.dumps(raw.get("registration", "")):
        raise HoldoutRefused(f"{source}: names the burned round-1 holdout")
    if raw.get("generator") != GENERATOR:
        raise ValueError(f"{source}: generator {raw.get('generator')!r}, expected {GENERATOR!r}")


def split_dir(root: Path, split: str) -> Path:
    if split not in ALLOWED_SPLITS:
        raise HoldoutRefused(f"split {split!r}: only {ALLOWED_SPLITS} may be read")
    d = Path(root) / split / "full"
    _refuse_path(d)
    return d


def load_index(root: Path, split: str) -> list[dict]:
    d = split_dir(root, split)
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    for row in index:
        _refuse_scenario(dict(row, generator=GENERATOR), f"{d}/index.json:{row.get('id')}")
    return index


def load_raw(path: Path) -> dict:
    path = Path(path)
    _refuse_path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    _refuse_scenario(raw, str(path))
    return raw


def load_scenario(root: Path, split: str, scenario_id: str) -> dict:
    return load_raw(split_dir(root, split) / f"{scenario_id}.json")


# ------------------------------------------------------------------------------------------------ mapping
def hermes_call(step: dict) -> tuple[str, dict, str]:
    """(Hermes tool name, arguments, result envelope) for one corpus step."""
    tool, args, text = step["tool"], step["args"], step["output_text"]
    if tool == "read_file":
        numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(text.removesuffix("\n").split("\n"), start=1))
        body = {"content": numbered, "total_lines": numbered.count("\n") + 1, "file_size": len(text.encode("utf-8")),
                "truncated": False, "is_binary": False, "is_image": False, "not_found": False}
        return "read_file", {"path": args["path"]}, json.dumps(body, ensure_ascii=False)
    if tool == "bash":
        command = args["command"]
    elif tool == "gh_run_view":
        command = f"gh run view {args['run']}" + (" --log" if str(args.get("log", "")).lower() == "true" else "")
    elif tool == "kubectl_logs":
        command = f"kubectl logs -n {args['namespace']} {args['pod']} --tail={args['tail']}"
    elif tool == "sql_query":
        command = "psql -c " + json.dumps(args["query"], ensure_ascii=False)
    elif tool == "ansible_playbook":
        command = f"ansible-playbook {args['playbook']} --limit {args['limit']}"
    elif tool == "http_get":
        command = f"curl -s {args.get('url', '')}".rstrip()
    else:        # any other corpus tool: Hermes would run it through the terminal; the command names the tool and its arguments
        command = " ".join([tool, *(f"--{k}={v}" for k, v in sorted(args.items()))])
    exit_code = 1 if step.get("kind") == "error" else 0
    return "terminal", {"command": command}, json.dumps({"output": text, "exit_code": exit_code, "error": None}, ensure_ascii=False)


def _position(step: int, n: int) -> str:
    third = 3 * step / max(1, n)
    return "early" if third < 1 else "middle" if third < 2 else "late"


@dataclass
class Mapped:
    """A corpus scenario at one window, in this replay's shape, with what the row needs to score it."""

    id: str
    family: str
    language: str
    style: str
    window: int
    steps: int
    scenario: Scenario
    facts: list[FactRef]
    questions: list[dict]                     # answerable at the window (absent included)
    step_keys: list[str]                      # call key per step
    step_results: list[str]                   # Hermes envelope per step
    real_chars: int = 0
    total_chars: int = 0
    meta: dict = field(default_factory=dict)


def map_scenario(raw: dict, window: int) -> Mapped:
    """The corpus scenario ``raw`` sliced to ``window`` and mapped to a replay ``Scenario``."""
    _refuse_scenario(raw, raw.get("id", "?"))
    if str(window) not in raw["windows"]:
        raise ValueError(f"{raw['id']}: no {window} slice (have {sorted(raw['windows'], key=int)})")
    n = int(raw["windows"][str(window)]["steps"])
    steps = raw["steps"][:n]
    if len(steps) != n or any(s["index"] != i for i, s in enumerate(steps)):
        raise ValueError(f"{raw['id']}: export holds {len(raw['steps'])} steps, the {window} slice needs {n}")
    lang = raw["language"]
    seen: set[str] = set()
    tool_steps: list[ToolStep] = []
    for s in steps:
        name, args, result = hermes_call(s)
        base = call_key(name, args)
        key = call_key(name, args, s["index"] if base in seen else None)
        seen.add(base)
        tool_steps.append(ToolStep(name, args, result, key))
    turns: list[Turn] = []
    for t in range(0, n, STEPS_PER_TURN):
        user = raw["task"] if t == 0 else CONTINUE[lang]
        rounds = tuple(Round(SAY, (st,)) for st in tool_steps[t:t + STEPS_PER_TURN])
        turns.append(Turn(user, rounds, FINAL[lang]))
    questions = [q for q in raw["questions"] if int(q["first_window"]) <= window]
    by_id = {f["id"]: (s, f) for s in steps for f in s["facts"]}
    facts: list[FactRef] = []
    for q in questions:
        for fid in q["fact_ids"]:
            if fid in {f.id for f in facts}:
                continue
            if fid not in by_id:
                raise ValueError(f"{raw['id']}: question {q['id']} names {fid}, not in the {window} slice")
            s, f = by_id[fid]
            line = s["output_text"].split("\n")[f["line_index"]]
            i = s["index"]
            facts.append(FactRef(fid, f["kind"], f["key"], f["value"], line, i, i // STEPS_PER_TURN + 1, tool_steps[i].key,
                                 _position(i, n)))
    gold = [GoldFact(f.id, "recall", escaped(f.line), f.turn, "tool", True, f.call_key) for f in facts]
    final_question = "\n".join(q["text"] for q in questions)
    scenario = Scenario(turns=turns, gold=gold, error_lines=[], log_turn=1, final_question=final_question,
                        system_prompt=SYSTEM_PROMPT)
    scenario.facts_by_id = {g.id: g for g in gold}
    real = sum(s["chars"] for s in steps if s["kind"] == "real")
    total = sum(s["chars"] for s in steps)
    return Mapped(raw["id"], raw["family"], lang, raw["task_style"], window, n, scenario, facts, questions,
                  [st.key for st in tool_steps], [st.result for st in tool_steps], real, total,
                  {"seed": raw["seed"], "index": raw["index"]})


def question_held(q: dict, held: dict[str, bool]) -> bool | None:
    """An aggregate question needs every fact; an absent one is not scored here (None)."""
    if not q["fact_ids"]:
        return None
    return all(held[f] for f in q["fact_ids"])


# ------------------------------------------------------------------------------------------------ deterministic subsample
def stratified_subsample(index: list[dict], per_family: int, seed: str) -> list[str]:
    """``per_family`` scenario ids from every family, drawn by ``random.Random(seed + family)``; sorted, logged by the caller."""
    import random

    fams: dict[str, list[str]] = {}
    for row in index:
        fams.setdefault(row["family"], []).append(row["id"])
    out: list[str] = []
    for fam in sorted(fams):
        ids = sorted(fams[fam])
        out.extend(sorted(random.Random(f"{seed}:{fam}").sample(ids, min(per_family, len(ids)))))
    return out
