"""``ledger``: the artifact trail (files with their operations, commands with their exit codes)."""
from __future__ import annotations

import json
import re

import pytest

from hermes_tameru_plugin.hermes_compat import (
    DUPLICATE_PREFIX,
    PERSISTED_OUTPUT_TAG,
    PRUNED_PLACEHOLDER,
)
from hermes_tameru_plugin.ledger import HEADING, MAX_CHARS, build_ledger, merge_into_summary
from hermes_tameru_plugin.recovery import OriginalStore
from hermes_tameru_plugin.render import RenderStats, header_only, make_ref, render

OK = json.dumps({"success": True, "diff": "--- a\n+++ b"})
BAD = json.dumps({"success": False, "error": "could not find old_string"})
SECRET = "gh" + "p_" + "A1b2C3d4E5" * 3        # shaped like a GitHub token, built at runtime
USER_ASKED = re.compile(r"\bUser\s+asked\s*:", re.IGNORECASE)


def run(output: str = "", exit_code: int = 0, error=None) -> str:
    return json.dumps({"output": output, "exit_code": exit_code, "error": error})


def stats(rung: str = "extract") -> RenderStats:
    return RenderStats(3, 90, 400, 9000, (("log", 87),), rung)


@pytest.fixture
def trail(make_tool_turn, payloads):
    """``trail((tool, args, content), ..., **build_kwargs) -> ledger text``."""

    def build(*calls, **kwargs):
        turns = [{"role": "user", "content": "fix it"}]
        for tool, args, content in calls:
            turns += list(make_tool_turn(tool, args, content))
        return build_ledger(turns, kwargs.pop("store", None), **kwargs)

    return build


def lines_of(text: str) -> list[str]:
    return text.split("\n")


# ---- the trail itself --------------------------------------------------------------------------
def test_files_and_commands_are_listed_in_first_seen_order(trail, payloads):
    text = trail(
        ("read_file", {"path": "a.py"}, payloads.read_file("x = 1")),
        ("patch", {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, OK),
        ("terminal", {"command": "pytest -q"}, run("1 failed", exit_code=1)),
        ("patch", {"path": "a.py", "old_string": "x = 2", "new_string": "x = 3"}, BAD),
        ("read_file", {"path": "tests/t.py"}, payloads.read_file("def test(): ...")),
        ("terminal", {"command": "git  status"}, run("clean")),
        ("terminal", {"command": "pytest -q"}, run("1 passed")),
    )
    assert text == "\n".join([
        HEADING,
        "Files:",
        "- `a.py`: read, patch x2; last patch FAILED",
        "- `tests/t.py`: read; last read ok",
        "Commands:",
        "- `pytest -q` -> exit 0 (x2)",
        "- `git status` -> exit 0",
    ])


def test_every_file_operation_is_recorded(trail, payloads):
    text = trail(
        ("search_files", {"pattern": "TODO", "path": "src"}, payloads.search_files([("src/a.py", 1, "TODO")])),
        ("write_file", {"path": "new.py", "content": "x"}, json.dumps({"bytes_written": 1})),
        ("read_file", {"file_path": "new.py"}, payloads.read_file("x")),
        ("patch", {"filename": "new.py", "old_string": "x", "new_string": "y"}, OK),
    )
    assert "- `src`: search; last search ok" in text
    assert "- `new.py`: write, read, patch; last patch ok" in text


def test_a_v4a_patch_names_every_file_it_touches(trail):
    patch = "\n".join([
        "*** Begin Patch", "*** Update File: src/a.py", "@@ def f @@", "-x", "+y",
        "*** Add File: src/new.py", "+z", "*** Delete File: old.py", "*** Move File: a.txt -> b.txt",
        "*** End Patch",
    ])
    text = trail(("patch", {"mode": "patch", "patch": patch}, OK))
    for path in ("src/a.py", "src/new.py", "old.py", "a.txt", "b.txt"):
        assert f"- `{path}`: patch; last patch ok" in text
    failed = trail(("patch", {"mode": "patch", "patch": patch}, BAD))
    assert failed.count("last patch FAILED") == 5


def test_other_tools_and_unpaired_rows_are_ignored(trail, make_tool_turn):
    assert trail(
        ("web_search", {"query": "x"}, "results"),
        ("skill_view", {"name": "x"}, "body"),
        ("read_file", {}, "{}"),
        ("terminal", {"command": "  "}, run()),
        ("terminal", {}, run()),
    ) == ""
    assistant, tool = make_tool_turn("read_file", {"path": "late.py"}, OK)
    orphan = {**tool, "tool_call_id": "unknown_call"}
    assert build_ledger([assistant, orphan]) == ""
    assert build_ledger([assistant]) == "", "a call without a result in the window says nothing"
    assert build_ledger([]) == ""
    assert build_ledger(["junk", None, {"role": "tool"}]) == ""


def test_tool_calls_given_as_objects_and_string_arguments_are_read(make_tool_turn):
    from types import SimpleNamespace

    assistant, tool = make_tool_turn("terminal", {"command": "ls"}, run("a b"))
    call = assistant["tool_calls"][0]
    assistant["tool_calls"] = [SimpleNamespace(
        id=call["id"], function=SimpleNamespace(name="terminal", arguments=json.dumps({"command": "ls -la"})),
    )]
    assert "- `ls -la` -> exit 0" in build_ledger([assistant, tool])


def test_multimodal_result_content_is_read_as_text(make_tool_turn):
    assistant, tool = make_tool_turn("terminal", {"command": "make"}, "")
    tool["content"] = [{"type": "text", "text": run("boom", exit_code=2)}]
    assert "- `make` -> exit 2" in build_ledger([assistant, tool])


# ---- outcomes ----------------------------------------------------------------------------------
def test_file_outcomes_follow_hermes_failure_rules(trail, payloads):
    text = trail(
        ("read_file", {"path": "ok.py"}, payloads.read_file("x")),
        ("read_file", {"path": "missing.py"}, json.dumps({"error": "File not found", "not_found": True})),
        ("write_file", {"path": "refused.py", "content": "x"},
         json.dumps({"status": "blocked", "error": "BLOCKED: path is protected"})),
        ("patch", {"path": "bad.py", "old_string": "a", "new_string": "b"}, BAD),
    )
    assert "- `ok.py`: read; last read ok" in text
    assert "- `missing.py`: read; last read FAILED" in text
    assert "- `refused.py`: write; last write FAILED" in text
    assert "- `bad.py`: patch; last patch FAILED" in text


def test_command_outcomes(trail):
    text = trail(
        ("terminal", {"command": "false"}, run("", exit_code=1)),
        ("terminal", {"command": "sleep 99"}, run("", exit_code=-1, error="timed out")),
        ("terminal", {"command": "sudo reboot"}, json.dumps({"status": "pending_approval", "command": "sudo reboot"})),
        ("terminal", {"command": "rm -rf /"}, json.dumps({"status": "blocked", "error": "BLOCKED: dangerous"})),
        ("terminal", {"command": "server &"}, json.dumps({"status": "started", "session_id": "p1"})),
        ("terminal", {"command": "boom"}, json.dumps({"error": "no such file"})),
        ("terminal", {"command": "big"}, run("x" * 5000, exit_code=7)),
    )
    assert "- `false` -> exit 1" in text
    assert "- `sleep 99` -> exit -1" in text
    assert "- `sudo reboot` -> not run" in text
    assert "- `rm -rf /` -> not run" in text
    assert "- `server &` -> exit ?" in text
    assert "- `boom` -> FAILED" in text
    assert "- `big` -> exit 7" in text


def test_hermes_summary_stubs_still_carry_the_outcome(trail):
    text = trail(
        ("terminal", {"command": "pytest"}, "[terminal] ran `pytest` -> exit 2, 40 lines output"),
        ("terminal", {"command": "ls"}, "[terminal] ran `ls` -> exit 0, 3 lines output"),
        ("patch", {"path": "a.py"}, "[patch] patched a.py (82 chars) FAILED: could not find old_string"),
        ("read_file", {"path": "b.py"}, "[read_file] read b.py from line 1 (2,400 chars)"),
    )
    assert "- `pytest` -> exit 2" in text and "- `ls` -> exit 0" in text
    assert "- `a.py`: patch; last patch FAILED" in text
    assert "- `b.py`: read; last read ok" in text


def test_a_raw_plain_text_result_is_not_read_as_a_stub(trail):
    text = trail(
        ("terminal", {"command": "make"}, "build FAILED -> exit 7\nrest"),
        ("read_file", {"path": "a.py"}, "x = 'FAILED'\n"),
    )
    assert "- `make` -> exit ?" in text
    assert "- `a.py`: read; last read ok" in text


def test_rows_with_nothing_left_to_read_are_unknown_not_ok(trail):
    lean = "[terminal output demoted at compaction — 5,000 chars preserved in session history.]"
    text = trail(
        ("read_file", {"path": "a.py"}, PRUNED_PLACEHOLDER),
        ("read_file", {"path": "b.py"}, f"{DUPLICATE_PREFIX} - same as an earlier call]"),
        ("read_file", {"path": "c.py"}, f"{PERSISTED_OUTPUT_TAG}\nsaved to /tmp/x\n</persisted-output>"),
        ("terminal", {"command": "make"}, lean),
        ("terminal", {"command": "make test"}, PRUNED_PLACEHOLDER),
    )
    for path in ("a.py", "b.py", "c.py"):
        assert f"- `{path}`: read; last read ?" in text
    assert "- `make` -> exit ?" in text and "- `make test` -> exit ?" in text


# ---- Tameru-rendered rows ----------------------------------------------------------------------
def test_a_rendered_row_takes_its_outcome_from_the_store(trail):
    original = run("line\n" * 400, exit_code=3)
    row = {"tool_call_id": "c1", "message_uid": "m1"}
    ref = make_ref(row, original)
    store = OriginalStore(10, 10**6)
    store.put(ref, original, tool="terminal", meta={"exit_code": 3, "error": None})
    stub = header_only("[terminal] ran `pytest` -> exit 3", stats("header"), ref)
    assert "- `pytest` -> exit 3" in trail(("terminal", {"command": "pytest"}, stub), store=store)

    unknown_ref = header_only("[terminal] ran `pytest`", stats("header"), "0badc0de")
    text = trail(("terminal", {"command": "pytest"}, unknown_ref), store=store)
    assert "- `pytest` -> exit ?" in text, "a ref the store lost leaves only the row itself"


def test_a_rendered_row_without_the_store_reads_its_meta_line(trail):
    meta_line = json.dumps({"exit_code": 2, "error": None}, separators=(",", ":"))
    row = render("[terminal] ran `make`", stats(), "abcdef12", meta_line=meta_line, body="boom\n[…]")
    assert "- `make` -> exit 2" in trail(("terminal", {"command": "make"}, row))
    failing = render("[read_file] read a.py", stats(), "abcdef12", meta_line='{"error":"No such file"}')
    assert "- `a.py`: read; last read FAILED" in trail(("read_file", {"path": "a.py"}, failing))


def test_a_body_line_that_looks_like_json_is_not_mistaken_for_the_envelope(trail):
    body = '{"event":"x","error":"bad request","exit_code":9}\nmore'
    row = render("[terminal] ran `job`", stats(), "abcdef12", body=body)
    assert "- `job` -> exit ?" in trail(("terminal", {"command": "job"}, row))
    assert "- `a.py`: read; last read ok" in trail(("read_file", {"path": "a.py"}, render(
        "[read_file] read a.py", stats(), "abcdef12", body='{"error": "in the file text"}',
    )))


def test_a_failed_parent_line_marks_a_rendered_file_row_failed(trail):
    row = header_only("[patch] patched a.py (82 chars) FAILED: no match", stats("header"), "abcdef12")
    assert "- `a.py`: patch; last patch FAILED" in trail(("patch", {"path": "a.py"}, row))


# ---- hygiene -----------------------------------------------------------------------------------
def test_a_credential_in_a_command_is_redacted(trail):
    text = trail(
        ("terminal", {"command": f"curl -H 'Authorization: token {SECRET}' https://x"}, run("{}")),
        ("terminal", {"command": f"export TOKEN={SECRET}"}, run("", exit_code=1)),
        ("terminal", {"command": "ls"}, run("a")),
    )
    assert SECRET not in text
    assert "- `<redacted>` -> exit 1 (x2)" in text and "- `ls` -> exit 0" in text


def test_a_credential_in_a_path_is_redacted(trail, payloads):
    assert SECRET not in trail(("read_file", {"path": f"/tmp/{SECRET}.txt"}, payloads.read_file("x")))


def test_the_text_never_says_user_asked(trail):
    text = trail(
        ("terminal", {"command": "echo 'User asked: why'"}, run("User asked: why")),
        ("terminal", {"command": 'echo "user  ASKED : x"'}, run("")),
        ("read_file", {"path": "notes/User asked: later.md"}, OK),
    )
    assert not USER_ASKED.search(text)
    assert "User asked -" in text, "the phrase is neutralised, not dropped"
    mangled = f"## x\n\n{HEADING}\nFiles:\n- `User asked: z.py`: read; last read ok\n"
    assert not USER_ASKED.search(build_ledger([], None, previous=mangled))


@pytest.mark.real_hermes
def test_hermes_summary_validator_accepts_a_ledger_over_a_user_free_session(trail):
    from agent.context_compressor import (
        HISTORICAL_TASK_HEADING,
        _NO_USER_TASK_SENTINEL,
        ContextCompressor,
    )

    text = trail(("terminal", {"command": "echo 'User asked: why'"}, run("")))
    summary = f"{HISTORICAL_TASK_HEADING}\n{_NO_USER_TASK_SENTINEL}\n\n{text}"
    ContextCompressor._validate_summary_user_provenance(summary, False)


def test_labels_are_one_short_line_without_backticks(trail, payloads):
    command = "echo `date`\n" + "x" * 300
    path = "/very/long/" + "dir/" * 60 + "file.py"
    text = trail(
        ("terminal", {"command": command}, run("")),
        ("read_file", {"path": path}, payloads.read_file("x")),
    )
    (command_line,) = [line for line in lines_of(text) if "-> exit" in line]
    label = command_line.split("`")[1]
    assert label.startswith("echo 'date' xxx") and label.endswith("…") and len(label) == 120
    (file_line,) = [line for line in lines_of(text) if "last read" in line]
    shown = file_line.split("`")[1]
    assert shown.startswith("…") and shown.endswith("dir/file.py") and len(shown) == 120


# ---- the cap -----------------------------------------------------------------------------------
def many(count: int, kind: str, payloads):
    if kind == "file":
        return [("read_file", {"path": f"src/module_{i:02d}.py"}, payloads.read_file("x")) for i in range(count)]
    return [("terminal", {"command": f"make target_{i:02d}"}, run("")) for i in range(count)]


def test_the_ledger_stays_within_the_cap_and_keeps_the_newest(trail, payloads):
    assert MAX_CHARS == 1500
    text = trail(*many(60, "file", payloads), *many(30, "command", payloads))
    assert len(text) <= MAX_CHARS and text.startswith(HEADING + "\nFiles:\n")
    assert "\nCommands:\n" in text
    assert re.search(r"- \(\+\d+ more\)", text)
    assert "src/module_59.py" in text and "target_29" in text, "the most recent entries survive"
    assert "src/module_00.py" not in text and "target_00" not in text
    kept = [int(n) for n in re.findall(r"module_(\d+)\.py", text)]
    assert kept == sorted(kept), "kept entries keep their first-seen order"
    assert text.count("(+") == 2, "one 'more' line per section"


def test_each_section_gets_room_when_both_overflow(trail, payloads):
    text = trail(*many(80, "command", payloads), *many(80, "file", payloads))
    files, commands = text.split("\nCommands:\n")
    assert files.count("\n- `") >= 8 and commands.count("\n- `") + 1 >= 6
    assert len(text) <= MAX_CHARS


def test_a_small_trail_is_never_cut(trail, payloads):
    text = trail(*many(5, "file", payloads), *many(5, "command", payloads))
    assert "more)" not in text and text.count("- `") == 10


@pytest.mark.parametrize("cap", [200, 400, 800, 1500, 3000])
def test_a_custom_cap_is_honoured(trail, payloads, cap):
    text = trail(*many(40, "file", payloads), *many(40, "command", payloads), max_chars=cap)
    assert 0 < len(text) <= cap


def test_a_cap_too_small_for_anything_gives_an_empty_ledger(trail, payloads):
    assert trail(*many(3, "file", payloads), max_chars=10) == ""
    assert trail(*many(3, "file", payloads), max_chars=0) == ""


# ---- accumulation across compactions -----------------------------------------------------------
def test_an_earlier_summarys_trail_seeds_the_next_one(trail, payloads):
    first = trail(
        ("read_file", {"path": "a.py"}, payloads.read_file("x")),
        ("patch", {"path": "a.py"}, OK),
        ("terminal", {"command": "pytest"}, run("", exit_code=1)),
    )
    summary = merge_into_summary("## Goal\nfix the bug", first)
    second = trail(
        ("patch", {"path": "a.py"}, BAD),
        ("read_file", {"path": "b.py"}, payloads.read_file("y")),
        ("terminal", {"command": "pytest"}, run("", exit_code=0)),
        previous=summary,
    )
    assert second == "\n".join([
        HEADING,
        "Files:",
        "- `a.py`: read, patch x2; last patch FAILED",
        "- `b.py`: read; last read ok",
        "Commands:",
        "- `pytest` -> exit 0 (x2)",
    ])


def test_a_trail_survives_its_own_round_trip(trail, payloads):
    first = trail(*many(6, "file", payloads), *many(4, "command", payloads))
    assert build_ledger([], None, previous=first) == first


def test_a_previous_summary_without_a_section_or_with_junk_adds_nothing(trail, payloads):
    assert build_ledger([], None, previous="## Goal\nnothing here") == ""
    junk = f"{HEADING}\nFiles:\n- not a line\n- `x`: nonsense\nCommands:\n- `ls` -> maybe\n"
    assert build_ledger([], None, previous=junk) == ""


def test_the_trail_keeps_newest_entries_when_old_ones_crowd_it(trail, payloads):
    summary = ""
    for round_ in range(4):
        calls = [
            ("read_file", {"path": f"r{round_}/module_{i:02d}.py"}, payloads.read_file("x")) for i in range(15)
        ]
        summary = merge_into_summary(summary, trail(*calls, previous=summary))
        assert summary.count(HEADING) == 1 and len(summary) <= MAX_CHARS
    assert "r3/module_14.py" in summary and "r0/module_00.py" not in summary


# ---- merging into a summary --------------------------------------------------------------------
LEDGER = f"{HEADING}\nFiles:\n- `a.py`: read; last read ok"


def test_the_ledger_is_appended_when_there_is_no_section():
    assert merge_into_summary("## Goal\nship it", LEDGER) == f"## Goal\nship it\n\n{LEDGER}"
    assert merge_into_summary("## Goal\nship it\n\n\n", LEDGER) == f"## Goal\nship it\n\n{LEDGER}"
    assert merge_into_summary("", LEDGER) == LEDGER


def test_an_existing_section_is_replaced_in_place():
    old = f"{HEADING}\nFiles:\n- `old.py`: write; last write ok\n"
    summary = f"## Goal\nship it\n\n{old}\n## Next\ndo more\n"
    merged = merge_into_summary(summary, LEDGER)
    assert merged == f"## Goal\nship it\n\n{LEDGER}\n\n## Next\ndo more\n"
    assert "old.py" not in merged
    last = f"## Goal\nship it\n\n{old}"
    assert merge_into_summary(last, LEDGER) == f"## Goal\nship it\n\n{LEDGER}\n"


def test_repeated_sections_collapse_into_one():
    old = f"{HEADING}\nFiles:\n- `old.py`: write; last write ok\n"
    summary = f"{old}\n## Mid\nx\n\n{old}\n## End\ny"
    merged = merge_into_summary(summary, LEDGER)
    assert merged.count(HEADING) == 1 and "old.py" not in merged
    assert merged == f"{LEDGER}\n\n## Mid\nx\n\n## End\ny"


def test_merging_is_idempotent_and_an_empty_ledger_changes_nothing():
    once = merge_into_summary("## Goal\nx", LEDGER)
    assert merge_into_summary(once, LEDGER) == once
    assert merge_into_summary("## Goal\nx", "") == "## Goal\nx"
    assert merge_into_summary(once, "") == once


def test_a_heading_quoted_mid_line_is_not_a_section():
    summary = f"## Notes\nsee {HEADING} below\n"
    merged = merge_into_summary(summary, LEDGER)
    assert merged == f"{summary}\n{LEDGER}"


def test_iterative_compactions_never_duplicate_the_section(trail, payloads):
    summary = "## Goal\nship it"
    for round_ in range(5):
        text = trail(
            ("read_file", {"path": f"f{round_}.py"}, payloads.read_file("x")),
            ("terminal", {"command": f"step {round_}"}, run("")),
            previous=summary,
        )
        summary = merge_into_summary(summary, text)
        assert summary.count(HEADING) == 1
    assert summary.startswith("## Goal\nship it\n\n" + HEADING)
    assert all(f"f{n}.py" in summary and f"step {n}" in summary for n in range(5))
