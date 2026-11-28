"""Read-only session handoff export."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from engine_support import pytest_failure_with_passes
from test_info_commands import _cmd, _live

from hermes_tameru_plugin.activity import SURFACE_REFUSAL
from hermes_tameru_plugin.handoff import HandoffOptions, choose_tail, export_handoff, is_compaction_summary, pairing_open_ends, render_handoff

SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, title TEXT, model TEXT, started_at REAL, ended_at REAL, end_reason TEXT,
    message_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, cwd TEXT
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT,
    tool_calls TEXT, tool_name TEXT, timestamp REAL, _compressed_summary INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0
);
"""


def make_db(path: Path, session_id: str = "old-session") -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, "Checkout rescue", "test-model", 100.0, None, None, 5, 600_000, 20_000, "/repo/checkout"),
    )
    long_user = "Fix the checkout regression without losing the stock reservation behavior.\n" + "detail\n" * 500
    secret = "API_KEY=sk-testabcdef1234567890\nAuthorization: Basic dXNlcjpwYXNzd29yZA=="
    rows = [
        (session_id, "user", long_user + secret, None, None, None, 101.0, 0, 1, 0),
        (session_id, "assistant", "thinking " + "x" * 4_000, None, json.dumps([{
            "id": "call-1", "type": "function",
            "function": {"name": "terminal", "arguments": json.dumps({"command": "pytest -q"})},
        }]), None, 102.0, 0, 1, 0),
        (session_id, "tool", pytest_failure_with_passes(), "call-1", None, "terminal", 103.0, 0, 1, 0),
        (session_id, "user", "[CONTEXT COMPACTION — REFERENCE ONLY] earlier state\ngoal: finish checkout patch", None, None, None, 104.0, 1, 1, 0),
        (session_id, "assistant", "I will inspect the current diff before editing.", None, None, None, 105.0, 0, 1, 0),
    ]
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp,"
        " _compressed_summary, active, compacted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def test_export_writes_a_layered_read_only_handoff(tmp_path):
    db = make_db(tmp_path / "state.db")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = export_handoff(db, "old-session", tmp_path / "exports", now=1_700_000_000)

    assert result.handoff_path.is_file() and result.digest_path.is_file() and result.manifest_path.is_file()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    handoff = result.handoff_path.read_text(encoding="utf-8")
    digest = result.digest_path.read_text(encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert "Fix the checkout regression" in handoff
    assert "detail\n" * 100 in digest          # user messages are not truncated
    assert "thinking " + "x" * 3_000 not in digest
    assert "[truncated" in digest              # assistant/tool rows are bounded
    assert "API_KEY=***" in handoff and "Authorization: ***" in handoff
    assert "sk-testabcdef1234567890" not in handoff + digest and "dXNlcjpwYXNzd29yZA==" not in handoff + digest
    assert "## Compaction Summaries" in handoff and "reference-only" in handoff
    assert "## Artifact Trail" in handoff and "pytest -q" in handoff
    assert "no tool result" not in handoff
    assert manifest["counts"] == {"rows": 5, "users": 1, "summaries": 1, "tail_rows": 5}
    assert manifest["open_ends"] == {"unanswered_tool_calls": [], "orphan_tool_results": []}


def test_export_never_overwrites_an_existing_export(tmp_path):
    db = make_db(tmp_path / "state.db")
    export_handoff(db, "old-session", tmp_path / "exports", now=1_700_000_000)
    with pytest.raises(FileExistsError):
        export_handoff(db, "old-session", tmp_path / "exports", now=1_700_000_000)


def test_missing_session_is_a_clean_error(tmp_path):
    db = make_db(tmp_path / "state.db")
    with pytest.raises(KeyError, match="session not found"):
        export_handoff(db, "other", tmp_path / "exports")


def test_tail_selection_preserves_the_final_mandate_and_tool_call_boundary():
    rows = [{"i": i, "role": "assistant", "content": "filler"} for i in range(60)]
    rows[35] = {"i": 35, "role": "user", "content": "final mandate"}
    tail, start = choose_tail(rows, HandoffOptions(tail_rows=20, tail_chars=100_000))
    assert tail[0] is rows[35] and start == 35

    bounded = [{"i": 0, "role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
               {"i": 1, "role": "tool", "content": "result", "tool_call_id": "c1"}]
    bounded += [{"i": i, "role": "assistant", "content": "x"} for i in range(2, 12)]
    tail, start = choose_tail(bounded, HandoffOptions(tail_rows=11, tail_chars=100_000))
    assert start == 0 and tail[0]["role"] == "assistant"

    oversized = [{"i": 0, "role": "user", "content": "old mandate"}]
    oversized += [{"i": i, "role": "assistant", "content": "x" * 100} for i in range(1, 40)]
    tail, start = choose_tail(oversized, HandoffOptions(tail_rows=35, tail_chars=1_000))
    assert sum(min(len(row["content"]), 1_500) + 80 for row in tail) <= 1_000 and start > 5


def test_a_user_mentioning_a_summary_is_not_mislabeled_as_one():
    assert not is_compaction_summary({"role": "user", "content": "Please compare this Conversation Summary with the diff"})
    assert is_compaction_summary({"role": "user", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] handoff"})
    assert is_compaction_summary({"role": "user", "content": "", "compressed_summary": True})


def test_rewound_user_rows_are_not_current_mandates_but_compacted_history_is():
    rows = [
        {"i": 0, "id": 1, "role": "user", "content": "withdrawn request", "active": False, "compacted": False},
        {"i": 1, "id": 2, "role": "user", "content": "archived mandate", "active": False, "compacted": True},
        {"i": 2, "id": 3, "role": "assistant", "content": "done", "active": True, "compacted": False},
    ]
    text = render_handoff({"id": "s"}, rows, rows, 0, HandoffOptions(), "digest.txt")
    mandates = text.split("## User Mandate History", 1)[1].split("## Compaction Summaries", 1)[0]
    assert "archived mandate" in mandates and "withdrawn request" not in mandates
    assert "rewound or withdrawn" in text


def test_tail_budget_counts_rendered_excerpts_not_raw_tool_results():
    rows = [{"i": i, "role": "tool", "content": "x" * 10_000} for i in range(3)]
    rows.append({"i": 3, "role": "user", "content": "finish the migration"})
    tail, start = choose_tail(rows, HandoffOptions(tail_rows=10, tail_chars=6_500, tail_excerpt_chars=1_500))
    assert start == 0 and len(tail) == 4


def test_pairing_reports_interrupted_tool_work():
    rows = [
        {"role": "assistant", "tool_calls": [{"id": "c1"}, {"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c1"},
        {"role": "tool", "tool_call_id": "orphan"},
    ]
    assert pairing_open_ends(rows) == (["c2"], ["orphan"])


def test_handoff_command_exports_paths_without_putting_the_ledger_in_chat(tmp_path):
    db = make_db(tmp_path / "state.db")
    engine = _live()
    engine._session_db = SimpleNamespace(db_path=db)
    out = _cmd(engine, f"handoff old-session --out {tmp_path / 'exports'}")
    assert "Tameru handoff exported" in out and "handoff.md" in out and "digest.txt" in out
    assert "Fix the checkout regression" not in out and "thinking" not in out

    payload = json.loads(_cmd(engine, f"--json handoff old-session --out {tmp_path / 'json-exports'}"))
    assert payload["ok"] and payload["rows"] == 5 and Path(payload["handoff_path"]).is_file()


def test_handoff_command_is_local_only_and_validates_usage(tmp_path):
    engine = _live()
    assert _cmd(engine, "handoff old-session", platform="telegram") == SURFACE_REFUSAL
    assert "Use /tameru handoff" in _cmd(engine, "handoff --wat")
    assert "positive integer" in _cmd(engine, "handoff old-session --tail-rows no")


def test_help_lists_the_handoff_verb():
    assert "/tameru handoff" in _cmd(_live(), "help")


def test_cli_exports_without_python_api(tmp_path, capsys):
    from hermes_tameru_plugin.handoff import main

    db = make_db(tmp_path / "state.db")
    assert main([str(db), "old-session", "--out", str(tmp_path / "cli")]) == 0
    out = capsys.readouterr().out
    assert "handoff:" in out and "digest:" in out and "rows: 5" in out
