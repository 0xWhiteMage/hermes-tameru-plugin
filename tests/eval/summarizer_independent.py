"""Independent summarizer stand-in, written by a separate agent from the interface description alone.

Drop-in replacement for ``replay.FakeSummarizer``: same call interface, return shape and counters. It
behaves like a careful human note-taker: keeps user requests and rules nearly whole, keeps tool calls
(files, commands) and outcomes with their concrete values, collapses repeated log noise, and cuts the
oldest entries first when the 12,000 character bound is hit.
"""

import re
from typing import Any

import replay

MAX_CHARS = 12000
USER_CAP = 900
ASSISTANT_CAP = 450
RESULT_CAP = 420
CARRY_CAP = 900
TASK_CAP = 1500

_HEADER_RE = re.compile(r"^\[(USER|ASSISTANT|SYSTEM|TOOL RESULT [^\]\n]*)\]: ?", re.DOTALL)
_SIGNAL_RE = re.compile(
    r"error|fail|exception|traceback|denied|not found|no such|cannot|can't|invalid|warn|passed|"
    r"\bok\b|success|exit|code \d+|version|v?\d+\.\d+\.\d+|[A-Za-z]:[\\/]|(?:\./|/)[\w.-]+/[\w./-]+|"
    r"\b[0-9a-f]{7,40}\b|=\s*\S+|\b\d{2,}\b",
    re.IGNORECASE,
)
_NOISE_RE = re.compile(r"^\s*(?:[-=*#_~.\s]*|ok|done|\.+|debug\b.*|info\b.*)$", re.IGNORECASE)


def _squash(text: str) -> str:
    return " ".join(text.split())


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 3].rstrip() + "..."


def _shape(line: str) -> str:
    """Line with digits and hex runs masked, so repeating log lines collapse to one."""
    return re.sub(r"\d+|[0-9a-f]{8,}", "#", line.strip().lower())


def _distill_result(body: str) -> str:
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return "(empty)"
    seen: dict[str, int] = {}
    uniq: list[str] = []
    for ln in lines:
        key = _shape(ln)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 1:
            uniq.append(ln)
    picked: list[str] = [uniq[0]]
    for ln in uniq[1:]:
        if _NOISE_RE.match(ln):
            continue
        if _SIGNAL_RE.search(ln):
            picked.append(ln)
    if len(uniq) > 1 and uniq[-1] not in picked:
        picked.append(uniq[-1])  # the last line is usually the verdict
    out = " | ".join(picked)
    repeats = sum(c - 1 for c in seen.values() if c > 1)
    if repeats:
        out += f" (+{repeats} repeated lines)"
    return _clip(_squash(out), RESULT_CAP)


class IndependentSummarizer(replay.FakeSummarizer):
    def _entry(self, rec: str) -> str | None:
        m = _HEADER_RE.match(rec)
        if not m:
            flat = _squash(rec)
            return _clip(flat, ASSISTANT_CAP) if flat else None
        kind, body = m.group(1), rec[m.end():]
        if kind == "USER":
            flat = _squash(body)
            return "User: " + _clip(flat, USER_CAP) if flat else None
        if kind == "ASSISTANT":
            flat = _squash(body)
            return "Assistant: " + _clip(flat, ASSISTANT_CAP) if flat else None
        if kind == "SYSTEM":
            flat = _squash(body)
            return "System: " + _clip(flat, 200) if flat else None
        return kind.title().replace("Tool Result", "Result") + ": " + _distill_result(body)

    def __call__(self, **kwargs: Any) -> dict:
        prompt = kwargs["messages"][0]["content"]
        self.calls += 1
        self.prompt_chars += len(prompt)
        self.input_chars += sum(len(replay._wire(message)) + 1 for message in kwargs["messages"])
        records, prev_actions, prev_task = self._parse(prompt)
        if not records or not replay._RECORD_START_RE.match(records[0]):
            self.unparsed += 1
        users = [r for r in records if r.startswith("[USER]:")]
        if users:
            task = _squash(users[-1][len("[USER]:"):])
            task = _clip(task, TASK_CAP)
            if prev_task and prev_task != replay._NO_USER_TASK and prev_task not in task:
                task = _clip(task + " (earlier: " + _squash(prev_task) + ")", TASK_CAP + 600)
        else:
            task = prev_task or replay._NO_USER_TASK

        entries = [_clip(_squash(a), CARRY_CAP) for a in prev_actions if a.strip()]
        for r in records:
            e = self._entry(r)
            if e:
                entries.append(e)
        kept: list[str] = []
        total = 600 + len(task)  # headers, goal and numbering overhead
        for e in reversed(entries):  # newest wins
            if total + len(e) + 6 > MAX_CHARS:
                break
            kept.append(e)
            total += len(e) + 6
        kept.reverse()
        text = (
            "## Historical Task Snapshot\n" + task + "\n\n## Goal\nContinue the work described above.\n\n"
            "## Completed Actions\n" + "\n".join(f"{i}. {e}" for i, e in enumerate(kept, 1)) + "\n"
        )
        self.output_chars += len(text)
        route = kwargs.get("route_info")
        if isinstance(route, dict):
            route.update({"provider": "fake", "model": "independent-summarizer"})
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
