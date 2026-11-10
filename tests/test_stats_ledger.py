"""``stats_ledger``: daily numbers only, 90 days, locked, shown never read back, and one command deletes it."""
from __future__ import annotations

import json
import time

import pytest
from engine_support import make_engine
from test_autotune_engine import _MetaDB

from hermes_tameru_plugin import stats_ledger as ledger
from hermes_tameru_plugin.config import TameruSettings


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMERU_STATS_DIR", str(tmp_path))
    return tmp_path


def test_a_cleanup_adds_to_todays_totals_and_nothing_else_is_stored(folder):
    assert ledger.record_cleanup(1_500) and ledger.record_cleanup(500)
    record = json.loads((folder / "stats-ledger.json").read_text())
    (day, values), = record["days"].items()
    assert values == {"cleanups": 2, "tokens": 2_000}
    assert set(record) == {"version", "days"} and list(folder.glob("*.lock")) == []


@pytest.mark.parametrize("bad", [0, -5, True, 1.5, "7", None])
def test_only_positive_whole_numbers_are_recorded(bad):
    assert ledger.record_cleanup(bad) is False and ledger.summary() is None


def test_days_older_than_ninety_are_dropped_on_the_next_write(folder):
    old = time.time() - 120 * 86400
    ledger.record_cleanup(100, now=old)
    ledger.record_cleanup(100)
    assert len(json.loads((folder / "stats-ledger.json").read_text())["days"]) == 1


def test_the_summary_covers_only_the_asked_days():
    ledger.record_cleanup(100, now=time.time() - 10 * 86400)
    ledger.record_cleanup(300)
    assert ledger.summary(7) == {"days": 7, "active_days": 1, "cleanups": 1, "tokens": 300}
    assert ledger.summary(30)["cleanups"] == 2


def test_a_corrupt_file_reads_as_empty_and_is_replaced(folder):
    (folder / "stats-ledger.json").write_text("{not json")
    assert ledger.summary() is None
    assert ledger.record_cleanup(50) and ledger.summary()["tokens"] == 50


def test_a_stale_lock_does_not_block_forever(folder):
    lock = folder / "stats-ledger.lock"
    lock.write_text("")
    old = time.time() - 60
    import os
    os.utime(lock, (old, old))
    assert ledger.record_cleanup(10)


def test_a_held_lock_makes_the_write_give_up_quietly(folder):
    (folder / "stats-ledger.lock").write_text("")
    assert ledger.record_cleanup(10) is False


def test_purge_removes_the_file():
    ledger.record_cleanup(10)
    assert ledger.purge() is True and ledger.summary() is None and ledger.purge() is False


def _command(*words, **settings):
    args = " ".join(words)
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **settings))
    engine.bind_session_state(session_db=_MetaDB(), session_id="s1")
    return engine._activity_registry.command(args, session_id="s1", platform="desktop")


def test_stats_show_the_week_only_when_the_ledger_is_on():
    ledger.record_cleanup(2_000)
    assert "Last 7 days" not in _command("stats")
    assert "Last 7 days" in _command("stats", stats_ledger=True)


def test_reset_stats_asks_first_then_deletes():
    ledger.record_cleanup(2_000)
    asked = _command("reset", "stats")
    assert "confirm" in asked and ledger.summary() is not None
    assert "deleted" in _command("reset", "stats", "confirm") and ledger.summary() is None
    assert "empty" in _command("reset", "stats")


def test_no_policy_code_reads_the_ledger():
    import pathlib
    root = pathlib.Path(ledger.__file__).parent
    readers = [p.name for p in root.glob("*.py") if p.name not in ("stats_ledger.py",) and "stats_ledger" in p.read_text(encoding="utf-8")]
    assert sorted(readers) == ["adaptive.py", "config.py", "info_commands.py"]
