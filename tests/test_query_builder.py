"""``query.build_query``: the task query for a tool result, and its per-pass memo.

The engine's ``task_query`` does the work; what this module adds is knowing which user rows are
Hermes scaffolding rather than the user, and not re-deciding that for every tool result of a pass.
"""
from __future__ import annotations

import json
import random

import pytest
from agent.context_compressor import SUMMARY_PREFIX

from hermes_tameru_plugin import query
from hermes_tameru_plugin.hermes_compat import is_compaction_summary, is_synthetic_user_row
from hermes_tameru_plugin.query import build_query
from hermes_tameru_plugin.tameru.transcript import task_query, text_of

TASK = "Fix the flaky retry logic in client.py"
FOLLOW_UP = "Now make the timeout configurable"
MERGED_DELIMITER = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"  # joins a merged handoff
SCAFFOLDING = [
    "[System: The user sent a voice message]",
    "[CONTEXT COMPACTION NOTE] earlier turns were compacted",
    "[PRIOR CONTEXT] earlier turns were compacted",
    "[IMPORTANT: Background process proc_1 finished]",
    "[Your active task list was preserved across context compression]",
    "Cronjob Response: nightly sync finished",
    "   ",
    "",
]


def user(text):
    return {"role": "user", "content": text}


def conversation(make_tool_turn, *, tail=(), head=(TASK,)):
    """``head`` user rows, one tool round (``messages[idx]`` is its result), an answer, ``tail``."""
    assistant, result = make_tool_turn("read_file", {"path": "client.py"}, "x" * 900)
    rows = [user(t) for t in head] + [assistant, result]
    rows.append({"role": "assistant", "content": "The retry loop never backs off."})
    rows += list(tail)
    return rows, rows.index(result)


def joined(parts):
    return "\n".join(parts)


# ---- which user rows count -------------------------------------------------------------------
@pytest.mark.parametrize("scaffold", SCAFFOLDING)
def test_scaffolding_is_never_the_users_words(make_tool_turn, scaffold):
    rows, idx = conversation(make_tool_turn, tail=[user(FOLLOW_UP), user(scaffold)])
    parts = build_query(rows, idx)
    assert FOLLOW_UP in parts, "the last real user message is used"
    assert scaffold.strip() not in parts


def test_without_the_predicate_the_engine_takes_the_injection(make_tool_turn):
    """The contrast that makes the wrapper necessary."""
    rows, idx = conversation(make_tool_turn, tail=[user(FOLLOW_UP), user(SCAFFOLDING[0])])
    assert SCAFFOLDING[0] in task_query(rows, idx)
    assert SCAFFOLDING[0] not in build_query(rows, idx)


@pytest.mark.parametrize(
    "summary",
    [
        SUMMARY_PREFIX + "## Goal\nrefactor the client",
        "[CONTEXT SUMMARY]: ## Goal\nrefactor the client",  # a legacy handoff
        "  \n" + SUMMARY_PREFIX + "## Goal\nrefactor the client",
        f"what I said before\n\n{MERGED_DELIMITER}\n{SUMMARY_PREFIX}## Goal\nrefactor the client",
    ],
)
def test_the_compaction_handoff_is_not_the_users_words(make_tool_turn, summary):
    rows, idx = conversation(make_tool_turn, head=(TASK, summary), tail=[user(summary)])
    text = joined(build_query(rows, idx))
    assert "refactor the client" not in text
    assert "COMPACTION" not in text
    assert TASK in text, "the task before the handoff is still the original task"


def test_a_real_message_that_merely_mentions_the_markers_counts(make_tool_turn):
    mention = "Why does the log say [System: ready] and [CONTEXT] twice?"
    rows, idx = conversation(make_tool_turn, tail=[user(mention)])
    assert mention in build_query(rows, idx)


def test_every_user_row_being_scaffolding_leaves_no_user_part(make_tool_turn):
    rows, idx = conversation(make_tool_turn, head=(SCAFFOLDING[0],), tail=[user(SCAFFOLDING[1])])
    text = joined(build_query(rows, idx))
    assert "System" not in text and "COMPACTION" not in text
    assert "client.py" in text, "the call that produced the result still describes it"


def test_multimodal_user_rows_contribute_their_text_only(make_tool_turn):
    blob = "data:image/png;base64," + "A" * 5000
    shot = {
        "role": "user",
        "content": [
            {"type": "text", "text": "why does this render wrong?"},
            {"type": "image_url", "image_url": {"url": blob}},
        ],
    }
    rows, idx = conversation(make_tool_turn, tail=[shot])
    text = joined(build_query(rows, idx))
    assert "why does this render wrong?" in text
    assert "base64" not in text and "AAAA" not in text


# ---- what else the query holds ---------------------------------------------------------------
def test_focus_comes_first(make_tool_turn):
    rows, idx = conversation(make_tool_turn)
    assert build_query(rows, idx, focus="retry backoff")[0] == "retry backoff"
    assert "retry backoff" not in build_query(rows, idx)


def test_the_call_the_followup_and_the_task_are_all_there_in_priority_order(make_tool_turn):
    rows, idx = conversation(make_tool_turn, tail=[user(FOLLOW_UP)])
    parts = build_query(rows, idx, focus="backoff")
    assert parts[:2] == ["backoff", "read_file"]
    assert "client.py" in parts[2]
    answer = "The retry loop never backs off."
    assert parts.index(answer) < parts.index(FOLLOW_UP) < parts.index(TASK)


def test_a_responses_transport_composite_id_finds_its_call(make_tool_turn):
    rows, idx = conversation(make_tool_turn)
    rows[idx]["tool_call_id"] += "|fc_77"
    assert "client.py" in build_query(rows, idx)


@pytest.mark.parametrize("max_chars", [0, 10, 80, 2000])
def test_the_query_never_exceeds_its_budget(make_tool_turn, max_chars):
    rows, idx = conversation(make_tool_turn, tail=[user("details " * 400)])
    parts = build_query(rows, idx, max_chars=max_chars, focus="f " * 300)
    assert sum(map(len, parts)) + max(len(parts) - 1, 0) <= max_chars


def test_an_empty_transcript_has_no_query():
    assert build_query([], 0) == []


# ---- the per-pass memo -----------------------------------------------------------------------
def many_rounds(make_tool_turn, rounds=30):
    rows = [user(TASK + " " + "background " * 5000), user(FOLLOW_UP)]
    results = []
    for i in range(rounds):
        assistant, result = make_tool_turn("read_file", {"path": f"f{i}.py"}, "x" * 900)
        rows += [assistant, result]
        results.append(len(rows) - 1)
    rows.append({"role": "assistant", "content": "done"})
    rows.append(user("[System: reminder]"))
    return rows, results


def scaffolding(msg):
    """The predicate ``build_query`` hands the engine, written out independently."""
    text = text_of(msg["content"])
    return is_synthetic_user_row(text) or is_compaction_summary(text)


def test_the_memo_does_not_change_the_query(make_tool_turn):
    rows, results = many_rounds(make_tool_turn)
    cache: dict = {}
    for idx in results:
        plain = task_query(rows, idx, skip_user=scaffolding)
        assert build_query(rows, idx, cache=cache) == build_query(rows, idx) == plain
    assert build_query(rows, results[0], focus="x", cache=cache)[0] == "x"


@pytest.mark.parametrize("max_chars", [40, 60, 2000])
def test_a_user_message_that_leaves_no_words_does_not_stand_in_for_another(
    make_tool_turn, max_chars,
):
    """A pasted blob is a user row the engine accepts and cleans to nothing; the last real message
    must not take its place as the 'first' message (a tight budget would show a clipped copy)."""
    rows, idx = conversation(make_tool_turn, head=("x" * 900,), tail=[user("continue")])
    assert build_query(rows, idx, max_chars=max_chars, cache={}) == task_query(
        rows, idx, max_chars=max_chars, skip_user=scaffolding,
    )


def random_transcript(rng: random.Random) -> list[dict]:
    """User rows of every kind (scaffolding, blobs, multimodal, steer), parallel tool rounds with
    composite ids, orphan results and bare assistant rows, in a random order."""
    texts = [
        TASK, "continue", SCAFFOLDING[0], "  ", "", "Why [System: x]?", "x" * 900,
        "data:image/png;base64," + "A" * 500 + " words after the image", "B" * 300 + " tail words",
        "multi\nline\ttext   with   spaces " * 30, SUMMARY_PREFIX + "summary body",
        "Cronjob Response: ok",
    ]
    rows: list[dict] = []
    call = 0
    for _ in range(rng.randint(0, 25)):
        draw = rng.random()
        if draw < 0.25:
            content = rng.choice(texts)
            if rng.random() < 0.2:
                url = "data:image/png;base64," + "Z" * 300
                content = [
                    {"type": "text", "text": content},
                    {"type": "image_url", "image_url": {"url": url}},
                ]
            row = {"role": "user", "content": content}
            if rng.random() < 0.1:
                row["display_kind"] = "steer"
            rows.append(row)
        elif draw < 0.75:
            calls = []
            for _ in range(rng.randint(1, 3)):
                call += 1
                arguments = json.dumps({"path": f"f{call}.py", "cmd": "q" * rng.randint(1, 300)})
                name = rng.choice(["read_file", "terminal"])
                function = {"name": name, "arguments": arguments}
                calls.append({"id": f"c{call}", "type": "function", "function": function})
            rows.append({
                "role": "assistant", "content": rng.choice(["", "thinking it over", "z" * 700]),
                "tool_calls": calls,
            })
            for c in calls:
                if rng.random() < 0.9:  # now and then a result is missing
                    ident = c["id"] if rng.random() < 0.8 else c["id"] + "|fc_9"
                    rows.append({"role": "tool", "tool_call_id": ident, "content": "out " * 50})
        else:
            rows.append({"role": "assistant", "content": rng.choice(["ok", "", "done here"])})
    return rows


BUDGETS = ((None, 2000), ("topic", 2000), (None, 300), ("f" * 700, 900), (None, 40))


def test_the_cached_query_is_the_uncached_query_on_random_transcripts():
    for seed in range(100):
        rows = random_transcript(random.Random(seed))
        cache: dict = {}
        for idx, msg in enumerate(rows):
            if msg["role"] != "tool":
                continue
            for focus, max_chars in BUDGETS:
                kw = {"focus": focus, "max_chars": max_chars}
                expected = task_query(rows, idx, skip_user=scaffolding, **kw)
                assert build_query(rows, idx, cache=cache, **kw) == expected, (seed, idx, kw)


def test_user_rows_are_judged_once_per_pass(make_tool_turn, monkeypatch):
    judged = []
    real = query.is_synthetic_user_row

    def counting(text):
        judged.append(len(text))
        return real(text)

    monkeypatch.setattr(query, "is_synthetic_user_row", counting)
    rows, results = many_rounds(make_tool_turn)

    for idx in results:
        build_query(rows, idx)
    unmemoized = len(judged)
    assert unmemoized >= len(results), "without a cache every result judges the user rows again"

    judged.clear()
    cache: dict = {}
    for idx in results:
        build_query(rows, idx, cache=cache)
    users_in_play = 3  # the injected reminder, the follow-up and the task
    assert len(judged) <= users_in_play
    assert len(judged) * 5 < unmemoized


def test_a_cache_may_outlive_the_list_it_was_filled_from(make_tool_turn):
    first, idx = conversation(make_tool_turn, tail=[user(SCAFFOLDING[0])])
    second, idx2 = conversation(make_tool_turn, tail=[user(FOLLOW_UP)])
    cache: dict = {}
    assert SCAFFOLDING[0] not in build_query(first, idx, cache=cache)
    assert FOLLOW_UP in build_query(second, idx2, cache=cache)
    assert build_query(first, idx, cache=cache) == build_query(first, idx)


def test_the_cache_follows_a_list_that_grew_and_a_changed_budget(make_tool_turn):
    rows, idx = conversation(make_tool_turn)
    cache: dict = {}
    assert FOLLOW_UP not in build_query(rows, idx, cache=cache)
    rows.append(user(FOLLOW_UP))
    assert FOLLOW_UP in build_query(rows, idx, cache=cache)
    short = build_query(rows, idx, max_chars=20, cache=cache)
    assert short == build_query(rows, idx, max_chars=20)
    assert sum(map(len, short)) + len(short) - 1 <= 20


def test_a_call_issued_before_another_assistant_row_is_still_found():
    def call(i, name, args):
        return {"id": i, "type": "function", "function": {"name": name, "arguments": args}}

    messages = [
        {"role": "user", "content": "fix thing"},
        {"role": "assistant", "content": "", "tool_calls": [call("c1", "read_file", '{"path": "foo.py"}')]},
        {"role": "assistant", "content": "thinking aloud"},
        {"role": "tool", "tool_call_id": "c1", "content": "x" * 50},
        {"role": "assistant", "content": "done"},
    ]
    plain = task_query(messages, 3, skip_user=lambda m: False)
    assert "foo.py" in " ".join(plain)
    assert build_query(messages, 3, cache={}) == plain


def test_usable_query_drops_the_part_that_names_something_absent_from_the_text():
    from hermes_tameru_plugin.query import usable_query

    text = "payment timeout is 5 seconds\nbackup host is lunar-db-2"
    parts = ["cat report.txt", "what is the payment timeout?", "pytest tests/test_x.py"]
    assert usable_query(parts, text) == ["what is the payment timeout?"]


def test_usable_query_keeps_a_part_whose_identifier_is_in_the_text():
    from hermes_tameru_plugin.query import usable_query

    text = "ERROR svc-3 timeout after 30s\nINFO ok"
    assert usable_query(["why did svc-3 time out?", "grep -n svc-3 app.log"], text) == [
        "why did svc-3 time out?", "grep -n svc-3 app.log",
    ]


def test_usable_query_keeps_parts_with_nothing_distinctive_and_is_case_blind():
    from hermes_tameru_plugin.query import usable_query

    assert usable_query(["continue", "what is the host?"], "anything") == ["continue", "what is the host?"]
    assert usable_query(["Read MyService.Config"], "see myservice.config here") == ["Read MyService.Config"]
