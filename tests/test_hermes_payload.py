"""``payload``: Hermes' tool-result envelopes, the metadata line and the content hints.

The envelope tests run everywhere. The summarizer round trips (``real_hermes``) run Hermes' own
``_summarize_tool_result`` / ``_json_dict`` / ``_result_failure_suffix`` on rendered rows: the
metadata line is only worth emitting if those readers still get the exit code, the match count and
the failure of the original out of it.
"""
from __future__ import annotations

import dataclasses
import importlib
import json

import pytest
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.payload import (
    META_KEYS,
    HermesPayload,
    content_hint,
    meta_line,
    parse_payload,
)
from hermes_tameru_plugin.render import RenderStats, build_header, make_ref, render

PYTEST_LOG = "\n".join(
    [f"tests/test_mod_{i}.py::test_case_{i} PASSED" for i in range(30)]
    + ["tests/test_pay.py::test_total FAILED", "E   AssertionError: assert 1 == 2", "1 failed, 30 passed in 1.2s"]
)
NUMBERED = "1|import os\n2|import sys\n3|\n4|def main():\n5|    return 1"


# ---- parse_payload -----------------------------------------------------------------------------
def test_terminal_result_is_unwrapped():
    pl = parse_payload(hp.terminal_result(PYTEST_LOG, exit_code=1))
    assert pl == HermesPayload(inner=PYTEST_LOG, field="output", meta={"exit_code": 1, "error": None}, wrapped=True)


def test_read_file_result_is_unwrapped():
    text = "\n".join(f"line {i} of the file with some words" for i in range(40))
    pl = parse_payload(hp.read_file_result(text))
    assert pl.wrapped and pl.field == "content" and pl.inner.startswith("1|line 0 of")
    assert pl.meta["total_lines"] == 40 and pl.meta["truncated"] is False


def test_a_result_without_content_or_output_keeps_its_meta_keys():
    raw = hp.search_files_result([(f"src/m{i}.py", i, f"def handler_{i}(): pass") for i in range(12)])
    pl = parse_payload(raw)
    assert (pl.inner, pl.field, pl.wrapped) == (raw, None, False)
    assert pl.meta == {"total_count": 12}


def test_patch_result_keeps_success():
    raw = hp.patch_result("--- a/x\n+++ b/x\n" + "+line\n" * 80, ["x.py"])
    pl = parse_payload(raw)
    assert pl.inner == raw and not pl.wrapped
    assert pl.meta == {"success": True}


def test_a_short_wrapped_result_still_yields_its_meta_keys():
    raw = json.dumps({"output": "short", "exit_code": 3, "error": "boom", "huge": "x" * 500})
    pl = parse_payload(raw)
    assert (pl.inner, pl.wrapped) == (raw, False)
    assert pl.meta == {"exit_code": 3, "error": "boom"}


@pytest.mark.parametrize("content", [
    "plain text " * 100, "", "{not json", "[1, 2, 3]", '"a string"', "42", "null", "{" * 5000,
])
def test_anything_else_is_one_unwrapped_payload(content):
    assert parse_payload(content) == HermesPayload(content, None, {}, False)


def test_non_string_content_does_not_raise():
    assert parse_payload(None).wrapped is False


def test_payload_is_frozen():
    pl = parse_payload("text")
    with pytest.raises(dataclasses.FrozenInstanceError):
        pl.inner = "other"


# ---- meta_line ---------------------------------------------------------------------------------
def test_meta_keys_are_the_contract():
    assert META_KEYS == (
        "exit_code", "error", "success", "status", "total_lines", "total_count", "truncated", "path",
    )


def test_meta_line_is_compact_json_in_meta_keys_order():
    meta = {"path": "src/app.py", "total_lines": 220, "error": None, "exit_code": 1}
    line = meta_line(meta)
    assert line == '{"exit_code":1,"error":null,"total_lines":220,"path":"src/app.py"}'
    assert list(json.loads(line)) == ["exit_code", "error", "total_lines", "path"]


def test_meta_line_scalar_types():
    meta = {"success": False, "status": "blocked", "truncated": True, "total_count": 0, "error": "naïve 日本"}
    assert json.loads(meta_line(meta)) == {
        "error": "naïve 日本", "success": False, "status": "blocked", "total_count": 0, "truncated": True,
    }
    assert "naïve 日本" in meta_line(meta), "no ascii escaping"


def test_meta_line_ignores_other_keys_and_non_scalars():
    assert meta_line({"file_size": 10, "is_binary": False, "output": "x"}) == ""
    assert meta_line({"error": {"nested": 1}, "exit_code": 2, "path": ["a"]}) == '{"exit_code":2}'
    assert meta_line({}) == ""


def test_meta_line_cuts_long_strings():
    line = meta_line({"error": "e" * 400, "path": "/" + "p" * 400})
    parsed = json.loads(line)
    assert parsed["error"] == "e" * 119 + "…" and parsed["path"] == "/" + "p" * 118 + "…"
    assert all(len(v) == 120 for v in parsed.values())


def test_meta_line_stays_valid_and_within_max_chars():
    meta = {
        "exit_code": 137, "error": "e" * 2000, "success": False, "status": "s" * 2000,
        "total_lines": 12345, "total_count": 678, "truncated": True, "path": "/" + "p" * 2000,
    }
    line = meta_line(meta)
    assert len(line) <= 300 and "\n" not in line
    parsed = json.loads(line)
    assert parsed["exit_code"] == 137 and parsed["success"] is False and parsed["total_lines"] == 12345
    assert list(parsed) == list(META_KEYS), "long strings shrink before any key is dropped"


def test_meta_line_drops_trailing_keys_when_even_short_strings_do_not_fit():
    meta = {"exit_code": 1, "error": "boom", "total_lines": 220, "path": "src/app.py"}
    assert meta_line(meta, max_chars=34) == '{"exit_code":1,"error":"boom"}'
    assert meta_line(meta, max_chars=15) == '{"exit_code":1}'
    assert meta_line(meta, max_chars=14) == ""
    assert meta_line(meta, max_chars=0) == ""


def test_meta_line_does_not_mutate_its_input():
    meta = {"exit_code": 1, "path": "p" * 500}
    before = dict(meta)
    meta_line(meta, max_chars=20)
    assert meta == before


# ---- content_hint ------------------------------------------------------------------------------
def terminal(command: str, inner: str = "output") -> tuple:
    return "terminal", {"command": command}, inner


@pytest.mark.parametrize("call, expected", [
    # the table of the contract
    (("read_file", {"path": "a.py"}, NUMBERED), "numbered_code"),
    (("search_files", {"pattern": "x", "path": "."}, "{}"), "grep"),
    (("web_extract", {"urls": ["https://example.com"]}, "page text"), "text"),
    (terminal("git diff"), "diff"),
    (terminal("git show HEAD~1"), "diff"),
    (terminal("diff -u a.txt b.txt"), "diff"),
    (terminal("rg handler src"), "grep"),
    (terminal("grep -n handler src/app.py"), "grep"),
    (terminal("grep -r handler ."), "grep"),
    (terminal("pytest -x tests/"), "test"),
    (terminal("jest --ci"), "test"),
    (terminal("vitest run"), "test"),
    (terminal("go test ./..."), "test"),
    (terminal("cargo test --all"), "test"),
    (terminal("npm test"), "test"),
    (terminal("docker logs web"), "log"),
    (terminal("kubectl logs pod-1"), "log"),
    (terminal("journalctl -u nginx"), "log"),
    (terminal("tail -n 200 /var/log/app.log"), "log"),
    (terminal("cat -n app.py", NUMBERED), "numbered_code"),
    (terminal("nl -ba app.py", NUMBERED), "numbered_code"),
    # variants
    (terminal("git -C /repo diff --stat"), "diff"),
    (terminal("git --no-pager diff HEAD"), "diff"),
    (terminal("diff -ru a b"), "diff"),
    (terminal("diff -U5 a b"), "diff"),
    (terminal("rg -n 'a|b' src"), "grep"),
    (terminal("grep -rn handler ."), "grep"),
    (terminal("egrep -n handler file"), "grep"),
    (terminal("python -m pytest tests/ -q"), "test"),
    (terminal("python3 -m pytest"), "test"),
    (terminal("uv run pytest -x"), "test"),
    (terminal("npx vitest run"), "test"),
    (terminal("npm run test"), "test"),
    (terminal("yarn test"), "test"),
    (terminal("FORCE_COLOR=1 pytest"), "test"),
    (terminal("sudo docker compose logs -f api"), "log"),
    (terminal("kubectl -n prod logs pod-1"), "log"),
    (terminal("tail -f out.log"), "log"),
    # compound commands: the last command of a pipeline produced the output
    (terminal("cd repo && git diff HEAD | head -50"), "diff"),
    (terminal("pytest -q 2>&1 | tail -20"), "test"),
    (terminal("git diff | grep -n handler"), "grep"),
    (terminal("tail -n 50 app.log | cat"), "log"),
    # no hint
    (terminal("ls -la"), None),
    (terminal("echo hi"), None),
    (terminal("git status"), None),
    (terminal("git commit -m 'fix diff'"), None),
    (terminal("grep handler file"), None),
    (terminal("diff a b"), None),
    (terminal("tail -n 5 notes.txt"), None),
    (terminal("cat app.log"), None),
    (terminal("cat app.py"), None),
    (terminal(""), None),
    (("read_file", {"path": "a.py"}, "no gutter here"), None),
    (("read_file", {"path": "a.py"}, ""), None),
    (terminal("cat -n app.py", "no gutter here"), None),
    (("patch", {"path": "a.py"}, "x"), None),
    (("browser_snapshot", {}, "x"), None),
    (("unknown_tool", {"command": "pytest"}, "x"), None),
])
def test_content_hint(call, expected):
    assert content_hint(*call) == expected


@pytest.mark.parametrize("args", [{}, None, "pytest", ["pytest"], {"command": None}, {"command": 5}])
def test_content_hint_survives_odd_terminal_args(args):
    assert content_hint("terminal", args, "x") is None


def test_content_hint_survives_unbalanced_quotes():
    assert content_hint("terminal", {"command": "pytest -k 'unterminated"}, "") == "test"


def test_gutter_sniffing_needs_every_leading_line_numbered():
    assert content_hint("read_file", {}, "     1\tfirst\n     2\t\n     3\tthird") == "numbered_code"
    assert content_hint("read_file", {}, "\n\n1|first\n2|second") == "numbered_code"
    assert content_hint("read_file", {}, "1|first\nplain second line\n3|third") is None
    assert content_hint("read_file", {}, "12 apples were bought") is None


def test_gutter_sniffing_ignores_a_line_cut_by_the_sample():
    body = "\n".join(f"{i}|" + "x" * 90 for i in range(1, 60))
    assert content_hint("read_file", {}, body) == "numbered_code"


# ---- Hermes' summarizers read a rendered row ---------------------------------------------------
@pytest.fixture(scope="module")
def cc():
    return importlib.import_module("agent.context_compressor")


def rendered_row(cc, tool, args, original, *, with_meta=True, body="⟪extract⟫\nkept line"):
    """The row the engine commits: Hermes' summary of the original as the parent line, then the
    envelope as the metadata line, then the extract."""
    parent = cc._summarize_tool_result(tool, json.dumps(args), original)
    stats = RenderStats(2, 40, 120, len(original), (("log", 38),), "extract")
    ref = make_ref({"tool_call_id": "call_1"}, original)
    return render(
        parent, stats, ref, meta_line=meta_line(parse_payload(original).meta) if with_meta else "", body=body,
    )


@pytest.mark.real_hermes
@pytest.mark.parametrize("exit_code", [0, 1, 2, 127, -9])
def test_hermes_reads_the_exit_code_of_a_rendered_terminal_row(cc, exit_code):
    args = {"command": "pytest -x tests/"}
    row = rendered_row(cc, "terminal", args, hp.terminal_result(PYTEST_LOG, exit_code=exit_code))
    assert f"-> exit {exit_code}," in cc._summarize_tool_result("terminal", json.dumps(args), row)


@pytest.mark.real_hermes
def test_the_metadata_line_is_what_carries_the_exit_code(cc):
    args = {"command": "pytest -x tests/"}
    row = rendered_row(cc, "terminal", args, hp.terminal_result(PYTEST_LOG, exit_code=1), with_meta=False)
    assert "-> exit ?," in cc._summarize_tool_result("terminal", json.dumps(args), row)


@pytest.mark.real_hermes
def test_the_exit_code_is_read_from_the_metadata_line_not_the_extract(cc):
    args = {"command": "cat results.json"}
    body = '{"exit_code": 0}\n"exit_code": 5'
    row = rendered_row(cc, "terminal", args, hp.terminal_result(PYTEST_LOG, exit_code=1), body=body)
    assert "-> exit 1," in cc._summarize_tool_result("terminal", json.dumps(args), row)


@pytest.mark.real_hermes
def test_hermes_reads_the_match_count_of_a_rendered_search_row(cc):
    args = {"pattern": "handler", "path": "src"}
    original = hp.search_files_result([(f"src/m{i}.py", i, f"def handler_{i}(): pass") for i in range(12)])
    row = rendered_row(cc, "search_files", args, original, body="src/m3.py\n  3: def handler_3(): pass")
    assert "-> 12 matches" in cc._summarize_tool_result("search_files", json.dumps(args), row)
    bare = rendered_row(cc, "search_files", args, original, with_meta=False)
    assert "-> ? matches" in cc._summarize_tool_result("search_files", json.dumps(args), bare)


@pytest.mark.real_hermes
def test_hermes_json_parsers_read_the_metadata_line(cc):
    original = json.dumps({"output": PYTEST_LOG, "exit_code": 2, "error": "command failed: pytest"})
    line = meta_line(parse_payload(original).meta)
    assert cc._json_dict(line) == {"exit_code": 2, "error": "command failed: pytest"}
    assert cc._result_failure_suffix(line) == " FAILED: command failed: pytest"
    assert cc._result_failure_suffix(meta_line({"success": False, "status": "blocked"})) == " FAILED"
    assert cc._result_failure_suffix(meta_line({"exit_code": 0, "error": None, "total_lines": 3})) == ""


@pytest.mark.real_hermes
def test_the_failure_of_the_original_survives_in_the_header_and_the_metadata_line(cc):
    """Hermes parses a whole row as one JSON object (``_result_failure_suffix``), which no
    multi-line row is; so the outcome is carried twice, in the parent line Hermes wrote from the
    original (``FAILED: ...``) and in the metadata line."""
    args = {"path": "/repo/missing.py"}
    original = json.dumps({"content": "", "error": "File not found: /repo/missing.py", "success": False})
    row = rendered_row(cc, "read_file", args, original)
    header, meta, _ = row.split("\n", 2)
    assert " FAILED: File not found: /repo/missing.py [tameru:" in header
    assert cc._result_failure_suffix(meta) == " FAILED: File not found: /repo/missing.py"
    assert cc._is_summary_stub(header)


@pytest.mark.real_hermes
def test_a_long_failed_header_keeps_hermes_failure_suffix(cc):
    path = "/" + "/".join(["very-long-directory-name"] * 80)
    original = json.dumps({"content": "", "error": "File not found", "success": False})
    parent = cc._summarize_tool_result("read_file", json.dumps({"path": path}), original)
    assert parent.endswith(" FAILED: File not found") and len(parent) > 2000
    stats = RenderStats(0, 0, 0, 80, (), "header")
    header = build_header(parent, stats, "ab12cd34")
    assert len(header) <= 380 and cc._is_summary_stub(header)
    assert " chars) FAILED: File not found [tameru:header " in header
