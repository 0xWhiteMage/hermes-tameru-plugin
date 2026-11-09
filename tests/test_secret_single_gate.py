"""One gate for secrets: every original Tameru shows, restores or hands on passes ``screen_original``."""
from __future__ import annotations

import json
import re
from pathlib import Path

from engine_support import Chat, demote, make_engine, pytest_failure_with_passes
from test_autotune_engine import _MetaDB

from hermes_tameru_plugin import recovery
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.recovery import (
    SECRET_SOURCE, OriginalStore, lookup_original, screen_original,
)

SECRET = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 3
REF16 = "0123456789abcdef"
REF8 = "01234567"


def test_screen_original_refuses_a_credential_and_passes_plain_text():
    before = recovery.SCREEN_REFUSALS["count"]
    assert screen_original(f"export KEY={SECRET}\n") is None
    assert screen_original("plain output\n") == "plain output\n"
    assert screen_original(None) is None
    assert recovery.SCREEN_REFUSALS["count"] == before + 1


def test_screen_original_fails_closed_when_the_check_itself_breaks(monkeypatch):
    def boom(_text):
        raise RuntimeError("scanner down")
    monkeypatch.setattr(recovery, "contains_secret", boom)
    assert screen_original("anything") is None


def test_a_16_hex_archive_hit_with_a_secret_is_refused():
    store = OriginalStore(5, 10_000)
    entry, source = lookup_original(store, REF16, lambda ref: f"token {SECRET}", cache=True)
    assert entry is None and source == SECRET_SOURCE
    assert REF16 not in store


def test_an_8_hex_native_archive_hit_with_a_secret_is_refused(monkeypatch):
    class Native(recovery._SessionLookup):
        def __init__(self):
            pass

        def candidates(self, ref):
            return {"k" * 16: f"token {SECRET}"}

    store = OriginalStore(5, 10_000)
    entry, source = lookup_original(store, REF8, Native(), cache=True)
    assert entry is None and source == SECRET_SOURCE


def test_a_clean_archive_hit_is_still_served():
    store = OriginalStore(5, 10_000)
    entry, source = lookup_original(store, REF16, lambda ref: "all clear\n", cache=True)
    assert entry is not None and entry.text == "all clear\n" and source == "archive"


def _engine_with_row():
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    engine.bind_session_state(session_db=_MetaDB(), session_id="s1")
    chat = Chat("why did the gateway time out?")
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    messages = chat.finish()
    changed, out = demote(engine, messages, idx)
    assert changed
    engine._observe_activity(out)
    return engine, engine.activity_snapshot()["items"][0]["ref"]


def test_the_desktop_viewer_refuses_an_archived_original_that_holds_a_secret(monkeypatch):
    engine, ref = _engine_with_row()
    engine._store = OriginalStore(5, 10_000)        # nothing in memory: the archive is the only source
    monkeypatch.setattr(engine, "_session_original_lookup", lambda: (lambda r: f"token {SECRET}\nrest"))
    page = engine.inspect_original(ref)
    assert "content" not in page and "error" in page
    assert SECRET not in json.dumps(page)


def test_no_module_reads_an_original_without_the_gate():
    """A new code path must not skip the gate: raw archive reads stay inside recovery.py's lookup_original."""
    root = Path(recovery.__file__).parent
    raw_read = re.compile(r"(?<![\w.])(?:session_lookup|lookup)\(\s*ref\s*\)")
    for path in root.glob("*.py"):
        if path.name == "recovery.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if raw_read.search(line):
                assert "screen_original(" in line, f"{path.name}:{number} reads an original without screen_original"
