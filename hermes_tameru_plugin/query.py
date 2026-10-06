"""Task query for the extract and brief rungs: what the agent is doing around a tool result.

A thin wrapper over the engine's ``task_query``. It tells the engine which user rows carry no real
user words (Hermes' scaffolding injections and the compaction handoff), so a last message such as
``[System: ...]`` never becomes the query, and it memoizes the part of the query that does not
depend on the tool result: the user's words (the last and the first real user message).

Per tool result ``task_query`` scans the whole transcript for those two messages, asks Hermes'
predicates about the user rows on the way (they copy the whole row) and cleans the two texts again.
With a per-pass cache the two texts are settled once and each query reads only the tool round
around its result.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .hermes_compat import is_compaction_summary, is_synthetic_user_row
from .tameru.contract_gates import distinctive_query_terms
from .tameru.transcript import _find_tool_call, task_query, text_of

# A user row ``task_query`` takes for a real one but cleans down to nothing: a run of 200 printable
# characters is a blob to it. It stands in for a user message that left no words.
_NO_WORDS = "x" * 200


def _get(msg: Any, key: str) -> Any:
    return msg.get(key) if isinstance(msg, dict) else None


def _role(msg: Any) -> Any:
    return _get(msg, "role")


def _is_scaffolding(msg: dict[str, Any]) -> bool:
    """True for a user row that is a Hermes injection or compaction handoff, not user words."""
    text = text_of(msg.get("content"))
    return is_synthetic_user_row(text) or is_compaction_summary(text)


def _real_user(rows: Iterable[Any]) -> dict[str, Any] | None:
    """The first user row with words of the user's own, else None."""
    for msg in rows:
        if _role(msg) == "user" and text_of(msg.get("content")).strip():
            if not _is_scaffolding(msg):
                return msg
    return None


def _kept_text(msg: dict[str, Any] | None, max_chars: int) -> str:
    """What ``task_query`` keeps of ``msg``'s text (blobs removed, capped); "" when nothing."""
    if msg is None:
        return ""
    parts = task_query([msg], 1, max_chars=max_chars, skip_user=lambda m: False)
    return parts[0] if parts else ""


def _user_texts(messages: list[dict[str, Any]], max_chars: int, cache: dict) -> tuple[str, str]:
    """``(last, first)`` kept text of the real user messages of ``messages``, settled once."""
    key = (len(messages), max_chars)
    held = cache.get("user_texts")
    if held is None or held[0] is not messages or held[1] != key:
        texts = (
            _kept_text(_real_user(reversed(messages)), max_chars),
            _kept_text(_real_user(messages), max_chars),
        )
        held = cache["user_texts"] = (messages, key, texts)
    return held[2]


def _round_bounds(messages: list[dict[str, Any]], idx: int) -> tuple[int, int]:
    """``(lo, hi)``: the slice from the assistant turn that made the call behind ``messages[idx]``
    to the one that answers it; ``task_query`` reads nothing else but the user's words."""
    lo = idx
    while lo and _role(messages[lo]) != "assistant":
        lo -= 1
    hi = idx + 1
    while hi < len(messages) and _role(messages[hi]) != "assistant":
        hi += 1
    return lo, min(hi + 1, len(messages))


def build_query(
    messages: list[dict[str, Any]],
    idx: int,
    *,
    focus: str | None = None,
    max_chars: int = 2000,
    cache: dict | None = None,
) -> list[str]:
    """Query parts for ``messages[idx]``, in priority order, within ``max_chars`` in total.

    ``focus`` (a ``/compress <topic>`` argument) comes first, then the call that produced the
    result, the assistant turn that followed it and the user's words (see ``task_query``).
    ``cache`` is a dict the caller keeps for one pass of one message list and hands to every call
    of it; without it every call computes everything.
    """
    if cache is None or not 0 <= idx < len(messages):
        return task_query(
            messages, idx, focus=focus, max_chars=max_chars, skip_user=_is_scaffolding,
        )
    lo, hi = _round_bounds(messages, idx)
    call_id = _get(messages[idx], "tool_call_id")
    if call_id and _find_tool_call([messages[lo], messages[idx]], 1, str(call_id)) is None:
        # The call was not issued by the nearest assistant row; ``task_query`` looks further.
        return task_query(
            messages, idx, focus=focus, max_chars=max_chars, skip_user=_is_scaffolding,
        )
    last_text, first_text = _user_texts(messages, max_chars, cache)
    # The settled texts stand in for the user rows (the first at the start, the last at the end),
    # so ``task_query`` finds them at once; any other user row in the slice is skipped.
    first = {"role": "user", "content": first_text or _NO_WORDS}
    last = {"role": "user", "content": last_text or _NO_WORDS}
    return task_query(
        [first, *messages[lo:hi], last], idx - lo + 1, focus=focus, max_chars=max_chars,
        skip_user=lambda m: m is not first and m is not last,
    )


def usable_query(parts: list[str], text: str) -> list[str]:
    """``parts`` without those that can only make the engine keep ``text`` whole.

    The engine fails open when a query names something distinctive (a file name, a path, an
    identifier) that is nowhere in the text: the answer is not here, so it keeps everything. The call
    that produced a tool result names exactly such things (``cat report.txt``, ``pytest tests/x.py``),
    and the output rarely contains them. A part whose distinctive terms are all absent from ``text``
    is dropped; a part with none, or with one that appears, stays.
    """
    folded = text.casefold()
    kept = []
    for part in parts:
        terms = [t for t in distinctive_query_terms(part) if not t.startswith("script:")]
        if not terms or any(t in folded for t in terms):
            kept.append(part)
    return kept
