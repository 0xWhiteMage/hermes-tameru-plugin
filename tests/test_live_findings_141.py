from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from engine_support import Chat, demote, make_engine
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.hermes_compat import is_persisted_block
from hermes_tameru_plugin.payload import parse_payload
from hermes_tameru_plugin.render import classify_render, parse_header

COMPARE_PATH = Path(__file__).resolve().parent / "eval" / "compare.py"


def test_is_persisted_block() -> None:
    spill = hp.persisted_output("p" * 1500, 90_000, "/tmp/spill.txt")
    assert is_persisted_block(spill)
    assert is_persisted_block("   \n" + spill)
    middle = "before\n<persisted-output> rows keep their spill path in the header.\nafter"
    assert not is_persisted_block(middle)
    assert not is_persisted_block(None)
    assert not is_persisted_block(["<persisted-output>"])


def _git_log_text() -> str:
    lines: list[str] = []
    for i in range(1, 21):
        lines.append(f"commit {i:040x}")
        lines.append("Author: A <a@b.c>")
        lines.append("Date:   Fri Oct 10 12:00:00 2026 +0000")
        lines.append("")
        if i == 5:
            lines.append("    chore: re-vendor engine 1.4.0")
        else:
            lines.append(f"    feat: change {i}")
        lines.append("")
        if i == 5:
            lines.append("    - <persisted-output> rows keep their spill path in the header.")
            lines.append("")
        for j in range(10):
            lines.append(f"     path/file_{i}_{j}.py | 12 +++--")
        lines.append("     10 files changed, 6 insertions(+), 6 deletions(-)")
    return "\n".join(lines)


def test_quoted_persisted_tag_is_not_spill() -> None:
    chat = Chat("Which commit message mentions re-vendoring the engine?")
    idx = chat.tool("terminal", {}, hp.terminal_result(_git_log_text()))
    msgs = chat.finish()
    engine = make_engine()
    changed, out = demote(engine, msgs, idx)
    row = out[idx]["content"]
    assert changed
    assert classify_render(row) == "v1"
    header = parse_header(row)
    assert header["rung"] in {"extract", "brief"}
    # Before 1.4.1 the quoted tag sent this row to Hermes' one-line summary: no body, no ref.
    assert engine._store.get(header["ref"]) == parse_payload(msgs[idx]["content"]).inner
    assert "commit " in row


def test_real_spill_header() -> None:
    chat = Chat("show spill")
    idx = chat.tool("terminal", {}, hp.persisted_output("p" * 1500, 90_000, "/tmp/spill.txt"))
    msgs = chat.finish()
    engine = make_engine()
    changed, out = demote(engine, msgs, idx)
    row = out[idx]["content"]
    assert changed
    assert classify_render(row) == "v1"
    assert parse_header(row)["rung"] == "header"
    assert "spilled to /tmp/spill.txt" in row


def test_inline_short_gaps_in_extract() -> None:
    lines: list[str] = []
    for i in range(1, 81):
        if i == 40:
            lines.append("ERROR payment gateway timeout order=991")
        elif i == 41:
            lines.append("retry 1/3")
        elif i == 42:
            lines.append("ERROR payment gateway timeout order=992")
        else:
            lines.append(
                f"2026-10-11 12:00:{i:02d} INFO worker-{i % 10} processed batch {i:03d} in 12ms "
                + "x" * 40
            )
    chat = Chat("why did the payment gateway time out?")
    idx = chat.tool("terminal", {}, hp.terminal_result("\n".join(lines)))
    msgs = chat.finish()
    engine = make_engine()
    changed, out = demote(engine, msgs, idx)
    row = out[idx]["content"]
    assert changed
    assert classify_render(row) == "v1"
    rung = parse_header(row)["rung"]
    assert rung in {"extract", "brief"}
    if rung == "extract":
        assert "retry 1/3" in row


def test_compare_bootstrap_isolates_hermes_home(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TAMERU_EVAL_HERMES_HOME", raising=False)
    monkeypatch.delenv("TAMERU_EVAL_KEEP_HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "user_home"))
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("tameru_eval_compare", COMPARE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module._bootstrap()
    assert os.environ["HERMES_HOME"] != str(tmp_path / "user_home")
    monkeypatch.setenv("TAMERU_EVAL_HERMES_HOME", str(tmp_path / "chosen"))
    module._bootstrap()
    assert os.environ["HERMES_HOME"] == str(tmp_path / "chosen")
    monkeypatch.delenv("TAMERU_EVAL_HERMES_HOME")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "user_home"))
    monkeypatch.setenv("TAMERU_EVAL_KEEP_HERMES_HOME", "0")
    module._bootstrap()
    assert os.environ["HERMES_HOME"] != str(tmp_path / "user_home")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "user_home"))
    monkeypatch.setenv("TAMERU_EVAL_KEEP_HERMES_HOME", "1")
    module._bootstrap()
    assert os.environ["HERMES_HOME"] == str(tmp_path / "user_home")
