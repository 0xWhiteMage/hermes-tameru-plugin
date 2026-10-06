"""``supersede``: which tool results a later call made stale or redundant (G14).

A transcript is built from ``(tool, args, content)`` triples by ``Transcript``; ``why(i)`` is
``superseded_by`` for the i-th triple, as ``(index of the later triple, reason)``. The closing
assistant message keeps the last triple out of the pending tool round.
"""
from __future__ import annotations

import json
import time

import pytest

from conftest import read_file_payload, terminal_payload
from hermes_tameru_plugin import supersede
from hermes_tameru_plugin.supersede import REPEATABLE, build_index, superseded_by

OK = json.dumps({"success": True, "diff": "--- a\n+++ b"})
FAILED = json.dumps({"success": False, "error": "could not find old_string"})
WROTE = json.dumps({"bytes_written": 12, "dirs_created": False})
REFUSED_WRITE = json.dumps({"error": "stale write refused"})


def read(text="x = 1", **kw):
    """A ``read_file`` result."""
    return read_file_payload(text, **kw)


def run(output="", exit_code=0):
    """A ``terminal`` result."""
    return terminal_payload(output, exit_code=exit_code)


class Transcript:
    """``user, (assistant, tool)*, assistant`` rows plus the position of every triple's tool row."""

    def __init__(self, make_tool_turn, *triples, closed=True):
        self.messages = [{"role": "user", "content": "fix the bug"}]
        self.rows: list[int] = []
        for tool, args, content in triples:
            assistant, result = make_tool_turn(tool, args, content)
            self.messages += [assistant, result]
            self.rows.append(len(self.messages) - 1)
        if closed:
            self.messages.append({"role": "assistant", "content": "done"})
        self.index = build_index(self.messages)

    def why(self, i: int):
        """``(triple index of the superseding row, reason)`` for triple ``i``, or None."""
        found = superseded_by(self.index, self.rows[i])
        return None if found is None else (self.rows.index(found[0]), found[1])


@pytest.fixture
def transcript(make_tool_turn):
    return lambda *triples, **kw: Transcript(make_tool_turn, *triples, **kw)


# ---- stale after write -----------------------------------------------------------------------
def test_read_then_patch_then_read_makes_the_first_read_stale(transcript):
    t = transcript(
        ("read_file", {"path": "a.py"}, read("x = 1")),
        ("patch", {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, OK),
        ("read_file", {"path": "a.py"}, read("x = 2")),
    )
    assert t.why(0) == (1, "stale-after-write")
    assert t.why(1) is None, "a write is not superseded by anything"
    assert t.why(2) is None, "the newest read has nothing after it"


def test_write_file_makes_an_earlier_read_stale(transcript):
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("write_file", {"path": "a.py", "content": "y"}, WROTE),
    )
    assert t.why(0) == (1, "stale-after-write")


def test_a_failed_write_does_not_supersede(transcript):
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("patch", {"path": "a.py", "old_string": "q", "new_string": "r"}, FAILED),
        ("write_file", {"path": "a.py", "content": "y"}, REFUSED_WRITE),
    )
    assert t.why(0) is None


def test_a_patch_that_changed_nothing_does_not_supersede(transcript):
    noop = json.dumps({"success": True, "no_change": True, "note": "already applied"})
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("patch", {"path": "a.py", "old_string": "x", "new_string": "x"}, noop),
    )
    assert t.why(0) is None


def test_a_write_to_another_path_does_not_supersede(transcript):
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("write_file", {"path": "b.py", "content": "y"}, WROTE),
    )
    assert t.why(0) is None


@pytest.mark.parametrize("read_path", ["./a.py", "src/../a.py", "a.py"])
def test_paths_are_compared_after_normalisation(transcript, read_path):
    t = transcript(
        ("read_file", {"path": read_path}, read()),
        ("write_file", {"file_path": "a.py", "content": "y"}, WROTE),
    )
    assert t.why(0) == (1, "stale-after-write")


def test_v4a_patch_touches_every_file_it_names(transcript):
    body = (
        "*** Begin Patch\n*** Update File: a.py\n@@ x @@\n-x\n+y\n"
        "*** Add File: new.py\n+z\n*** Move File: old.py -> moved.py\n*** End Patch"
    )
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("read_file", {"path": "old.py"}, read()),
        ("read_file", {"path": "moved.py"}, read()),
        ("read_file", {"path": "other.py"}, read()),
        ("patch", {"mode": "patch", "patch": body}, OK),
    )
    assert [t.why(i) is not None for i in range(4)] == [True, True, True, False]


def test_a_v4a_content_line_that_mentions_a_header_is_not_a_path(transcript):
    body = "*** Begin Patch\n*** Update File: a.py\n@@ x @@\n+*** Update File: b.py\n*** End Patch"
    t = transcript(
        ("read_file", {"path": "b.py"}, read()),
        ("patch", {"mode": "patch", "patch": body}, OK),
    )
    assert t.why(0) is None


def test_search_files_is_stale_only_when_its_own_path_was_written(transcript):
    hit = json.dumps({"total_count": 1, "matches": [{"path": "a.py", "line": 1, "content": "x"}]})
    t = transcript(
        ("search_files", {"pattern": "x", "path": "a.py"}, hit),
        ("search_files", {"pattern": "x", "path": "src", "target": "content"}, hit),
        ("search_files", {"pattern": "*.py", "target": "files"}, hit),
        ("write_file", {"path": "a.py", "content": "y"}, WROTE),
    )
    assert t.why(0) == (3, "stale-after-write")
    assert t.why(1) is None, "a directory search is not made stale by one file"
    assert t.why(2) is None, "the search mode is not a path"


def test_a_failed_read_is_not_made_stale(transcript):
    missing = json.dumps({"error": "File not found: a.py"})
    t = transcript(
        ("read_file", {"path": "a.py"}, missing),
        ("write_file", {"path": "a.py", "content": "y"}, WROTE),
    )
    assert t.why(0) is None, "the error is what the agent may need"


# ---- re-read ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "first, later, superseded",
    [
        ({}, {}, True),  # whole file twice
        ({"offset": 1, "limit": 100}, {}, True),  # whole file covers a range
        ({"offset": 1, "limit": 100}, {"offset": 1, "limit": 200}, True),
        ({"offset": 50, "limit": 50}, {"offset": 1, "limit": 200}, True),
        ({"limit": 50}, {"offset": 1, "limit": 50}, True),  # missing offset means 1
        ({"offset": 10}, {"offset": 1}, True),  # missing limit means to the end
        ({"offset": 1, "limit": 100}, {"offset": 50, "limit": 150}, False),  # partial overlap
        ({"offset": 1, "limit": 100}, {"offset": 1, "limit": 50}, False),  # narrower
        ({"offset": 1, "limit": 100}, {"offset": 101, "limit": 100}, False),  # adjacent
        ({}, {"offset": 1, "limit": 2000}, False),  # a whole read is not covered by a limited one
        ({"offset": 5, "limit": 10}, {"offset": "abc"}, False),  # unusable range
    ],
)
def test_re_read_needs_the_later_range_to_cover_the_earlier(transcript, first, later, superseded):
    t = transcript(
        ("read_file", {"path": "a.py", **first}, read()),
        ("read_file", {"path": "a.py", **later}, read()),
    )
    assert t.why(0) == ((1, "re-read") if superseded else None)


def test_a_truncated_later_read_covers_only_what_it_returned(transcript):
    cut = json.dumps(
        {"content": "1|x", "truncated": True, "truncated_by": "bytes", "next_offset": 301},
    )
    t = transcript(
        ("read_file", {"path": "a.py", "offset": 500, "limit": 100}, read()),
        ("read_file", {"path": "a.py", "offset": 1, "limit": 50}, read()),
        ("read_file", {"path": "a.py"}, cut),
    )
    assert t.why(0) is None, "lines 500-599 were never returned by the cut read"
    assert t.why(1) == (2, "re-read"), "lines 1-50 were"


def test_a_read_with_a_limit_hermes_clamped_covers_nothing_past_the_clamp(transcript):
    cut = json.dumps({"content": "1|x", "truncated": True, "total_lines": 3000})
    t = transcript(
        ("read_file", {"path": "a.py", "offset": 2500, "limit": 50}, read()),
        ("read_file", {"path": "a.py", "offset": 1, "limit": 5000}, cut),
    )
    assert t.why(0) is None, "only lines 1-2000 came back"


def test_an_unchanged_stub_does_not_replace_the_read_it_points_at(transcript):
    stub = json.dumps({
        "status": "unchanged", "message": "File unchanged since last read.", "path": "a.py",
        "dedup": True, "content_returned": False,
    })
    t = transcript(
        ("read_file", {"path": "a.py"}, read("the only copy of the content")),
        ("read_file", {"path": "a.py"}, stub),
    )
    assert t.why(0) is None


def test_a_later_read_that_failed_does_not_supersede(transcript):
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("read_file", {"path": "a.py"}, json.dumps({"error": "permission denied"})),
        ("read_file", {"path": "a.py"}, "BLOCKED: read this region 4 times"),
    )
    assert t.why(0) is None


def test_a_later_row_already_reduced_to_a_stub_does_not_supersede(transcript):
    for stub in (
        "[Duplicate tool output — same content as a more recent call]",
        "[Old tool output cleared to save context space]",
        "[read_file] path=a.py (1,234 chars result)",
        "[read_file output demoted at compaction — 1,234 chars preserved in session history.]",
        "<persisted-output>\nThis tool result was too large\n</persisted-output>",
    ):
        t = transcript(
            ("read_file", {"path": "a.py"}, read()),
            ("read_file", {"path": "a.py"}, stub),
        )
        assert t.why(0) is None, stub


def test_a_stubbed_write_still_makes_a_read_stale_unless_it_failed(transcript):
    for stub, stale in (
        ("[write_file] wrote to a.py (3 lines)", True),
        ("[patch] edited a.py (400 chars result)", True),
        ("[write_file] a.py FAILED: stale write refused", False),
    ):
        t = transcript(
            ("read_file", {"path": "a.py"}, read()),
            ("write_file", {"path": "a.py", "content": "y"}, stub),
        )
        assert (t.why(0) is not None) is stale, stub


def test_the_reasons_are_checked_in_the_documented_order(transcript):
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("write_file", {"path": "a.py", "content": "y"}, WROTE),
        ("read_file", {"path": "a.py"}, read("y")),
    )
    assert t.why(0) == (1, "stale-after-write"), "a later covering read does not outrank the write"


def test_the_nearest_qualifying_later_row_is_reported(transcript):
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("read_file", {"path": "a.py"}, json.dumps({"error": "transient"})),
        ("read_file", {"path": "a.py"}, read()),
        ("read_file", {"path": "a.py"}, read()),
    )
    assert t.why(0) == (2, "re-read"), "the failed row is skipped, the next one is not"


# ---- repeated calls --------------------------------------------------------------------------
def test_a_repeated_read_only_command_supersedes_the_earlier_one(transcript):
    t = transcript(
        ("terminal", {"command": "rg -n TODO src"}, run("src/a.py:3: TODO")),
        ("terminal", {"command": "rg  -n   TODO src"}, run("src/a.py:3: TODO")),
        ("terminal", {"command": "rg -n FIXME src"}, run("")),
    )
    assert t.why(0) == (1, "repeated-call"), "whitespace does not make a command different"
    assert t.why(1) is None
    assert t.why(2) is None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",  # not read-only
        "python run.py",  # not in the read-only set
        "git commit -m x",  # git, but it writes
        "git push",
        "git branch -D old",
        "git diff --output=out.patch",
        "cat a.txt > b.txt",  # redirect
        "ls | wc -l",  # pipe
        "ls; rm -rf x",
        "ls && rm x",
        "echo $(rm x)",
        "cat <(rm x)",
        "find . -name x -delete",
        "find . -exec rm {} +",
        "tail -f app.log",
        "tail -F app.log",
        "env FOO=1 make",
        "cat `rm x`",
        "cat rm",
    ],
)
def test_a_command_that_may_write_is_never_superseded(transcript, command):
    t = transcript(
        ("terminal", {"command": command}, run("out")),
        ("terminal", {"command": command}, run("out")),
    )
    assert t.why(0) is None


@pytest.mark.parametrize(
    "command",
    [
        "git status", "git log --oneline -5", "git show HEAD", "ls -la", "pwd", "cat a.txt",
        "head -n 5 a", "tail -n 20 app.log", "grep -rn x .", "rg x", "find . -name '*.py'",
        "wc -l a", "stat a", "ps aux", "df -h", "du -sh .", "env", "which python",
        "grep -rn format .",  # "rm" inside a word is not the rm command
    ],
)
def test_read_only_commands_are_repeatable(transcript, command):
    t = transcript(
        ("terminal", {"command": command}, run("out")),
        ("terminal", {"command": command}, run("out")),
    )
    assert t.why(0) == (1, "repeated-call")


def test_a_background_terminal_call_is_not_repeatable(transcript):
    started = json.dumps({"session_id": "bg1", "status": "started"})
    t = transcript(
        ("terminal", {"command": "ps aux", "background": True}, started),
        ("terminal", {"command": "ps aux", "background": True}, started),
    )
    assert t.why(0) is None


def test_the_same_command_in_another_directory_is_another_call(transcript):
    t = transcript(
        ("terminal", {"command": "git log -3", "workdir": "/a"}, run("a")),
        ("terminal", {"command": "git log -3", "workdir": "/b"}, run("b")),
    )
    assert t.why(0) is None


@pytest.mark.parametrize("tool", sorted(REPEATABLE - {"terminal"}))
def test_every_repeatable_tool_supersedes_on_identical_arguments(transcript, tool):
    args = {"path": "a.py"} if tool == "read_file" else {"query": "q", "pattern": "p"}
    t = transcript((tool, args, "result text"), (tool, dict(reversed(args.items())), "result text"))
    assert t.why(0) is not None, "argument order is canonicalised"
    assert t.why(0)[0] == 1


def test_other_tools_are_never_superseded(transcript):
    t = transcript(
        ("vision_analyze", {"image": "a.png"}, "a cat"),
        ("vision_analyze", {"image": "a.png"}, "a cat"),
        ("write_file", {"path": "a"}, WROTE),
        ("write_file", {"path": "a"}, WROTE),
    )
    assert [t.why(i) for i in range(4)] == [None] * 4


def test_a_failed_row_is_superseded_only_by_an_identical_call_that_succeeded(transcript):
    t = transcript(
        ("terminal", {"command": "grep -n needle a.txt"}, run("", exit_code=1)),
        ("terminal", {"command": "grep -n needle b.txt"}, run("b:1: needle")),
        ("terminal", {"command": "grep -n needle a.txt"}, run("", exit_code=1)),
        ("terminal", {"command": "grep -n needle a.txt"}, run("a.txt:4: needle")),
    )
    assert t.why(0) == (3, "repeated-call"), "the later failure and the other file are skipped"
    assert t.why(2) == (3, "repeated-call")
    assert t.why(3) is None


def test_a_failed_row_with_no_successful_repeat_is_kept(transcript):
    t = transcript(
        ("terminal", {"command": "pytest -x"}, run("1 failed", exit_code=1)),
        ("terminal", {"command": "pytest -x"}, run("1 failed", exit_code=1)),
        ("read_file", {"path": "a.py"}, json.dumps({"error": "no such file"})),
        ("write_file", {"path": "a.py", "content": "y"}, WROTE),
    )
    assert t.why(0) is None
    assert t.why(2) is None, "a failed read keeps its error even after a write"


def test_spacing_inside_quotes_makes_a_command_different(transcript):
    t = transcript(
        ("terminal", {"command": 'grep "foo  bar" app.log'}, run("a")),
        ("terminal", {"command": 'grep "foo bar" app.log'}, run("b")),
        ("terminal", {"command": 'grep "foo bar" app.log'}, run("b")),
    )
    assert t.why(0) is None, "two spaces inside the pattern is another search"
    assert t.why(1) == (2, "repeated-call")


@pytest.mark.parametrize("change_dir", ["cd sub", "pushd sub", "popd", "cd"])
@pytest.mark.parametrize("command", ["ls", "cat config.yaml", "git status"])
def test_a_directory_change_between_two_identical_commands_keeps_the_earlier_result(
    transcript, command, change_dir,
):
    """Hermes' terminal keeps its working directory between calls: the second ``ls`` lists elsewhere."""
    t = transcript(
        ("terminal", {"command": command}, run("root listing")),
        ("terminal", {"command": change_dir}, run("")),
        ("terminal", {"command": command}, run("sub listing")),
        ("terminal", {"command": command}, run("sub listing")),
    )
    assert t.why(0) is None
    assert t.why(2) == (3, "repeated-call")


@pytest.mark.parametrize("command", ["cat /srv/app/config.yaml", "ls /srv/app", "tail -n 20 /var/log/app.log", "ps aux", "df -h"])
def test_a_command_that_cannot_depend_on_the_directory_survives_a_cd(transcript, command):
    t = transcript(
        ("terminal", {"command": command}, run("out")),
        ("terminal", {"command": "cd /srv/app && pytest -q"}, run("passed")),
        ("terminal", {"command": command}, run("out")),
    )
    assert t.why(0) is not None


def test_a_recursive_listing_is_not_replaced_by_a_shallow_one(transcript):
    t = transcript(
        ("terminal", {"command": "ls -R src"}, run("src:\na.py\nsrc/pkg:\nb.py")),
        ("terminal", {"command": "ls src"}, run("a.py pkg")),
        ("terminal", {"command": "ls -laR src"}, run("src:\na.py")),
    )
    assert t.why(0) == (2, "snapshot"), "another recursive listing does replace it"
    assert t.why(1) is None


# ---- snapshots -------------------------------------------------------------------------------
def test_a_snapshot_is_superseded_by_any_later_snapshot_of_the_same_tool(transcript):
    t = transcript(
        ("browser_snapshot", {}, "- button [@e1]"),
        ("browser_snapshot", {"full": True}, "- button [@e1]\n- link [@e2]"),
        ("browser_snapshot", {"full": True}, "- link [@e2]"),
    )
    assert t.why(0) == (1, "snapshot"), "different arguments: not identical, still a snapshot"
    assert t.why(1) == (2, "repeated-call")
    assert t.why(2) is None


@pytest.mark.parametrize(
    "first, later",
    [
        ("git status", "git status -s"),
        ("git status", "git status --short --branch"),
        ("ls", "ls -la"),
        ("ls src", "ls -la src"),
        ("git diff --stat", "git diff --stat --color"),
        ("git diff --stat main..HEAD", "git diff --shortstat main..HEAD --stat"),
    ],
)
def test_terminal_state_snapshots_form_families(transcript, first, later):
    t = transcript(
        ("terminal", {"command": first}, run("state 1")),
        ("terminal", {"command": later}, run("state 2")),
    )
    assert t.why(0) == (1, "snapshot")


@pytest.mark.parametrize(
    "first, later",
    [
        ("ls src", "ls tests"),  # another directory
        ("git status", "ls"),
        ("git diff --stat", "git diff"),  # a real diff is not a snapshot
        ("ls", "ls | wc -l"),
    ],
)
def test_different_families_do_not_supersede(transcript, first, later):
    t = transcript(
        ("terminal", {"command": first}, run("state 1")),
        ("terminal", {"command": later}, run("state 2")),
    )
    assert t.why(0) is None


def test_a_failed_snapshot_does_not_replace_the_last_good_one(transcript):
    t = transcript(
        ("terminal", {"command": "git status"}, run("clean")),
        ("terminal", {"command": "git status -s"}, run("fatal: not a repository", exit_code=128)),
    )
    assert t.why(0) is None


def test_a_snapshot_is_replaced_even_by_a_later_one_that_was_pruned(transcript):
    t = transcript(
        ("browser_snapshot", {}, "- button [@e1]"),
        ("browser_snapshot", {"full": True}, "[browser_snapshot] (15,000 chars)"),
    )
    assert t.why(0) == (1, "snapshot"), "stale state is stale whether or not the newer one survived"


# ---- the pending round, outcomes and the call table ------------------------------------------
def parallel_round(make_tool_turn, *triples) -> list[dict]:
    """One assistant message with several tool calls, then one result per call."""
    turns = [make_tool_turn(tool, args, content) for tool, args, content in triples]
    assistant = turns[0][0]
    for other, _ in turns[1:]:
        assistant["tool_calls"] += other["tool_calls"]
    return [assistant, *(result for _, result in turns)]


def test_the_pending_tool_round_is_never_superseded(transcript, make_tool_turn):
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        ("write_file", {"path": "a.py", "content": "y"}, WROTE),
        closed=False,
    )
    t.messages += parallel_round(
        make_tool_turn,
        ("read_file", {"path": "a.py"}, read("y")),
        ("read_file", {"path": "a.py"}, read("y")),
    )
    index = build_index(t.messages)
    first, second = len(t.messages) - 2, len(t.messages) - 1
    assert superseded_by(index, t.rows[0]) == (t.rows[1], "stale-after-write"), "an older round"
    assert superseded_by(index, first) is None, "the model has not seen this round yet"
    assert superseded_by(index, second) is None

    t.messages.append({"role": "assistant", "content": "the file is fine"})
    index = build_index(t.messages)
    assert superseded_by(index, first) == (second, "re-read"), "answered, so fair game"


def test_a_pending_row_can_still_supersede_an_older_one(transcript, make_tool_turn):
    t = transcript(("read_file", {"path": "a.py"}, read()), closed=False)
    t.messages += parallel_round(make_tool_turn, ("read_file", {"path": "a.py"}, read()))
    assert superseded_by(build_index(t.messages), t.rows[0]) == (len(t.messages) - 1, "re-read")


def test_trailing_steer_rows_do_not_answer_the_pending_round(transcript, make_tool_turn):
    t = transcript(closed=False)
    t.messages += parallel_round(
        make_tool_turn,
        ("read_file", {"path": "a.py"}, read()),
        ("read_file", {"path": "a.py"}, read()),
    )
    first = len(t.messages) - 2
    t.messages += [
        {"role": "user", "content": "also check b.py", "display_kind": "steer"},
        {"role": "user", "content": "and c.py", "display_kind": "steer"},
    ]
    assert superseded_by(build_index(t.messages), first) is None
    t.messages.append({"role": "user", "content": "thanks"})
    assert superseded_by(build_index(t.messages), first) == (first + 1, "re-read")


def test_rows_outside_the_index_are_ignored(transcript):
    t = transcript(("read_file", {"path": "a.py"}, read()))
    assert superseded_by(t.index, 0) is None, "a user row"
    assert superseded_by(t.index, 99) is None
    assert superseded_by(t.index, -1) is None


def test_failure_is_read_from_every_shape_a_row_takes(transcript):
    """Raw payloads, Hermes' one-line stubs and the engine's render all say if a call failed."""
    failed_rows = [
        run("boom", exit_code=2),
        json.dumps({"error": "x"}),
        json.dumps({"success": False}),
        "[terminal] ran `pytest` -> exit 1, 40 lines output",
        "[patch] a.py FAILED: could not find old_string",
        "[patch] a.py FAILED [tameru:header kept 0/0 lines (0 of 0 chars) ref=0123abcd]",
        '[terminal] ran `x` [tameru:extract kept 1/2 lines (3 of 9 chars) ref=0123abcd]\n'
        '{"exit_code":1,"error":null}\nout',
        "BLOCKED: the user did NOT consent",
        json.dumps({"status": "pending_approval", "command": "rm -rf /"}),
    ]
    good_rows = [
        run("fine"),
        OK,
        "[terminal] ran `ls` -> exit 0, 3 lines output",
        '[terminal] ran `x` [tameru:extract kept 1/2 lines (3 of 9 chars) ref=0123abcd]\n'
        '{"exit_code":0,"error":null}\nout',
        "plain text output",
    ]
    for content in failed_rows:
        t = transcript(
            ("terminal", {"command": "ls"}, content),
            ("terminal", {"command": "ls"}, run("a")),
        )
        assert t.why(0) == (1, "repeated-call"), f"{content[:40]!r} failed, a success replaces it"
        t = transcript(
            ("terminal", {"command": "ls"}, run("a")),
            ("terminal", {"command": "ls"}, content),
        )
        assert t.why(0) is None, f"{content[:40]!r} is no replacement"
    for content in good_rows:
        t = transcript(
            ("terminal", {"command": "ls"}, content),
            ("terminal", {"command": "ls"}, run("a" * 500)),
        )
        assert t.why(0) is not None, f"{content[:40]!r} succeeded"


def test_tool_names_come_from_the_call_table_and_composite_ids_resolve(transcript):
    t = transcript(("read_file", {"path": "a.py"}, read()), ("read_file", {"path": "a.py"}, read()))
    # Responses transport: the result answers with ``call_id|response_item_id``.
    t.messages[t.rows[0]]["tool_call_id"] += "|fc_0001"
    table = {
        call["id"]: (call["function"]["name"], call["function"]["arguments"])
        for m in t.messages
        for call in m.get("tool_calls") or []
    }
    index = build_index(t.messages, table)
    assert superseded_by(index, t.rows[0]) == (t.rows[1], "re-read")
    assert build_index(t.messages).calls.keys() == index.calls.keys(), "the table is optional"
    assert not build_index(t.messages, {}).calls, "no call table, nothing to reason about"


def test_unusable_arguments_are_ignored(make_tool_turn):
    rows = [{"role": "user", "content": "go"}]
    for raw in ("{not json", "[1, 2]", '"text"'):
        assistant, result = make_tool_turn("read_file", raw, read())
        rows += [assistant, result]
    rows.append({"role": "assistant", "content": "done"})
    assert not build_index(rows).calls


def test_a_call_without_arguments_is_still_indexed(make_tool_turn):
    rows = [{"role": "user", "content": "go"}]
    for _ in range(2):
        assistant, result = make_tool_turn("browser_snapshot", "", "- page")
        rows += [assistant, result]
    rows.append({"role": "assistant", "content": "done"})
    assert superseded_by(build_index(rows), 2) == (4, "repeated-call")


def test_multimodal_results_are_read_as_text(transcript):
    parts = [
        {"type": "text", "text": json.dumps({"error": "x"})},
        {"type": "image_url", "image_url": {}},
    ]
    t = transcript(
        ("browser_snapshot", {}, parts),
        ("browser_snapshot", {}, "- page"),
    )
    assert t.why(0) == (1, "repeated-call")


# ---- scaling ---------------------------------------------------------------------------------
def mixed_transcript(make_tool_turn, rows: int):
    """``rows`` tool results over a mix of tools that all take part in supersession."""
    triples = []
    for i in range(rows):
        kind = i % 5
        path = f"src/f{i % 40}.py"
        if kind == 0:
            triples.append(("read_file", {"path": path, "offset": 1 + i % 3, "limit": 100}, read()))
        elif kind == 1:
            triples.append(("patch", {"path": path, "old_string": "a", "new_string": "b"}, OK))
        elif kind == 2:
            triples.append(("terminal", {"command": f"rg -n item{i % 25} src"}, run("hit")))
        elif kind == 3:
            triples.append(("terminal", {"command": "git status"}, run("clean")))
        else:
            triples.append(("search_files", {"pattern": f"p{i % 30}", "path": "."}, read()))
    return Transcript(make_tool_turn, *triples)


def test_every_row_is_evaluated_at_most_once(make_tool_turn, monkeypatch):
    """Indexing evaluates nothing; a lookup then evaluates only the rows it needs, once each."""
    calls = []
    real = supersede.result_failure_suffix

    def counting(text):
        calls.append(1)
        return real(text)

    monkeypatch.setattr(supersede, "result_failure_suffix", counting)
    rows = 2000
    t = mixed_transcript(make_tool_turn, rows)
    assert not calls, "building the index parses no results"
    answers = [superseded_by(t.index, i) for i in range(len(t.messages))]
    assert len(calls) <= rows, "each result is parsed at most once, however many rows ask about it"
    assert sum(a is not None for a in answers) > rows // 2, "the mix does exercise supersession"


def test_a_lookup_looks_at_a_bounded_number_of_later_rows(transcript):
    limit = supersede.SCAN_LIMIT
    failing = ("read_file", {"path": "a.py"}, json.dumps({"error": "x"}))
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        *[failing] * limit,
        ("read_file", {"path": "a.py"}, read()),
    )
    assert t.why(0) is None, "the first success lies beyond the rows a lookup considers"
    t = transcript(
        ("read_file", {"path": "a.py"}, read()),
        *[failing] * (limit - 1),
        ("read_file", {"path": "a.py"}, read()),
    )
    assert t.why(0) == (limit, "re-read")


@pytest.mark.perf
def test_time_grows_linearly_with_the_number_of_rows(make_tool_turn):
    def seconds(rows: int) -> float:
        best = float("inf")
        for _ in range(3):
            t = mixed_transcript(make_tool_turn, rows)
            start = time.perf_counter()
            index = build_index(t.messages)
            for i in range(len(t.messages)):
                superseded_by(index, i)
            best = min(best, time.perf_counter() - start)
        return best

    small, large = seconds(500), seconds(2000)
    assert large / small < 8, f"4x the rows took {large / small:.1f}x the time (linear is 4x)"


# ---- Hermes' own summaries -------------------------------------------------------------------
@pytest.mark.real_hermes
def test_hermes_stubs_of_failed_calls_are_read_as_failures():
    from agent.context_compressor import _summarize_tool_result

    failed_patch = _summarize_tool_result(
        "patch", json.dumps({"path": "a.py"}), json.dumps({"success": False, "error": "no match"}),
    )
    failed_run = _summarize_tool_result("terminal", json.dumps({"command": "make"}), run("x", 2))
    ran = _summarize_tool_result("terminal", json.dumps({"command": "make"}), run("x", 0))
    wrote = _summarize_tool_result(
        "write_file", json.dumps({"path": "a.py", "content": "y"}), WROTE,
    )
    stubs = {"patch": failed_patch, "run": failed_run, "ran": ran, "wrote": wrote}
    outcomes = {
        name: supersede.ToolCall(0, "terminal", stub).assess() for name, stub in stubs.items()
    }
    assert not outcomes["patch"].ok and not outcomes["run"].ok
    assert outcomes["ran"].ok and outcomes["wrote"].ok
    assert not any(o.live for o in outcomes.values()), "a stub no longer holds the output"
