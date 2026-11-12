"""Cross-chat learning: a bounded, damped memory that new chats start from and calm chats ease back."""
from __future__ import annotations

import json
import os

import pytest
from engine_support import make_engine

from hermes_tameru_plugin import learned
from hermes_tameru_plugin.autotune import Tuner, astuple
from hermes_tameru_plugin.config import TameruSettings
from test_autotune_engine import _MetaDB, bind_meta


@pytest.fixture(autouse=True)
def _no_daily_cap(monkeypatch):
    """The daily cap is tested on its own below; every other test reasons about single steps."""
    monkeypatch.setattr(learned, "DAILY_CAP", 10.0)


def _engine(sid="s1", db=None, **settings):
    settings.setdefault("learned_priors", True)         # off by default; these tests are about the feature
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **settings))
    bind_meta(engine, db, sid)
    return engine


def test_nothing_is_learned_at_first_and_a_new_chat_starts_neutral():
    assert learned.load() is None and learned.seed() is None
    assert astuple(_engine()._policy.tuner.tuning()) == astuple(Tuner().tuning())


def test_record_moves_the_prior_a_quarter_of_the_way_and_load_returns_it():
    assert learned.record([0.8, 1.0, 1.2, 1.0, 1.0]) is True
    assert learned.load() == pytest.approx([0.95, 1.0, 1.05, 1.0, 1.0])
    learned.record([0.8, 1.0, 1.2, 1.0, 1.0])
    assert learned.load() == pytest.approx([0.9125, 1.0, 1.0875, 1.0, 1.0])


def test_the_prior_stays_inside_its_bounds_whatever_is_recorded():
    for _ in range(80):
        learned.record([0.1, 9.0, 0.1, 9.0, 1.0])
    low, high = learned.BOUNDS
    values = learned.load()
    assert all(low <= v <= high for v in values)
    assert values[0] == pytest.approx(low) and values[1] == pytest.approx(high)


def test_a_new_chat_starts_from_a_damped_share_of_the_prior():
    for _ in range(80):
        learned.record([0.7, 1.0, 1.0, 1.0, 1.0])
    seed = learned.seed()
    assert seed[0] == pytest.approx(1.0 + (0.7 - 1.0) * learned.SEED_SHARE, abs=0.02) and seed[1] == 1.0


def test_calm_chats_pull_the_prior_back_toward_neutral():
    for _ in range(40):
        learned.record([0.7, 1.0, 1.0, 1.0, 1.0])
    stressed = learned.load()[0]
    for _ in range(40):
        learned.record([1.0, 1.0, 1.0, 1.0, 1.0])
    assert learned.load()[0] > stressed
    assert learned.load()[0] == pytest.approx(1.0, abs=0.02)


@pytest.mark.parametrize("content", [
    "", "not json", "[]", '{"version": 2, "offsets": [1,1,1,1,1]}', '{"version": 1, "offsets": [1,1]}',
    '{"version": 1, "offsets": [1,1,1,1,"x"]}', '{"version": 1, "offsets": [1,1,1,1,NaN]}'])
def test_a_corrupt_or_foreign_file_means_nothing_learned(content):
    learned._path().parent.mkdir(parents=True, exist_ok=True)
    learned._path().write_text(content, encoding="utf-8")
    assert learned.load() is None and learned.seed() is None
    assert learned.record([0.8, 1, 1, 1, 1]) is True          # and it is simply replaced by a clean record
    assert learned.load() is not None


def test_an_unwritable_location_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("TAMERU_LEARNED_DIR", str(blocker / "sub"))
    assert learned.record([0.8, 1, 1, 1, 1]) is False and learned.load() is None


def test_bad_observations_are_ignored():
    assert learned.record([1, 1, 1]) is False and learned.record([float("nan")] * 5) is False
    assert learned.record(None) is False
    assert learned.load() is None


def test_a_tiny_change_does_not_touch_the_disk():
    learned.record([0.8, 1, 1, 1, 1])
    before = os.stat(learned._path()).st_mtime_ns
    assert learned.record(learned.load()) is False
    assert os.stat(learned._path()).st_mtime_ns == before


def test_the_file_is_five_numbers_and_nothing_else():
    learned.record([0.8, 1.1, 1, 1, 1])
    data = json.loads(learned._path().read_text(encoding="utf-8"))
    assert set(data) == {"version", "offsets", "day", "moved"} and len(data["offsets"]) == len(data["moved"]) == 5


def test_a_pristine_tuner_accepts_a_seed_and_a_used_one_does_not():
    tuner = Tuner()
    assert tuner.seed([0.9, 1, 1, 0.9, 1]) is True and tuner.seeded
    assert tuner.seed([0.8, 1, 1, 1, 1]) is False                   # only once
    used = Tuner()
    used.signal("host_summarized")
    assert used.seed([0.9, 1, 1, 1, 1]) is False
    for bad in (None, [1, 1], [1, 1, 1, 1, "x"], [float("nan")] * 5):
        assert Tuner().seed(bad) is False


def test_a_seed_is_clamped_like_any_other_offset():
    tuner = Tuner()
    tuner.seed([100, -100, 1, 1, 1])
    assert all(0.55 <= o <= 1.45 for o in tuner.offsets())


def test_tick_reports_when_it_relaxed():
    tuner = Tuner()
    tuner.signal("host_summarized")
    results = [tuner.tick() for _ in range(12)]
    assert results.count(True) >= 1 and results[0] is False
    assert Tuner().tick() is False                                  # nothing to relax at neutral


def test_one_chat_adjusting_teaches_the_next_chat():
    first = _engine("s1")
    first.observe_host_compaction(session_id="s1", kind="auto")     # Hermes summarized before Tameru acted
    assert learned.load() is not None and learned.load()[0] < 1.0
    second = _engine("s2")                                          # a different chat, never adjusted
    assert second._policy.tuner.seeded and second._policy.tuner.offsets()[0] < 1.0
    assert second._policy.tuner.tuning().activation < Tuner().tuning().activation
    assert second.activity_snapshot()["progress"]["tuner"]["learned"] is True
    assert first.activity_snapshot()["progress"]["tuner"]["learned"] is False


def test_a_chats_own_saved_state_beats_the_seed():
    db = _MetaDB()
    chat = _engine("s1", db)
    chat.observe_host_compaction(session_id="s1", kind="auto")
    saved = astuple(chat._policy.tuner.tuning())
    for _ in range(40):
        learned.record([0.7, 1.4, 1.4, 1.0, 1.0])                   # the prior drifts far away
    again = _engine("s1", db)                                       # the same chat comes back
    assert astuple(again._policy.tuner.tuning()) == pytest.approx(saved)


def test_autotune_off_neither_teaches_nor_learns():
    off = _engine("s1", autotune=False)
    off.observe_host_compaction(session_id="s1", kind="auto")
    assert learned.load() is None
    learned.record([0.8, 1, 1, 1, 1])
    assert not _engine("s2", autotune=False)._policy.tuner.seeded


def test_relax_eases_the_prior_toward_neutral_and_does_nothing_before_anything_is_learned():
    assert learned.relax() is False and learned.load() is None
    for _ in range(40):
        learned.record([0.7, 1.3, 1.0, 1.0, 1.0])
    before = learned.load()
    assert learned.relax() is True
    after = learned.load()
    assert before[0] < after[0] < 1.0 and 1.0 < after[1] < before[1]
    for _ in range(200):
        learned.relax()
    assert learned.load() == pytest.approx([1.0] * 5, abs=0.03)


def test_a_chat_that_calms_down_eases_the_prior_back_through_the_runtime_path():
    chat = _engine("s1")
    chat.observe_host_compaction(session_id="s1", kind="auto")
    after_adjust = learned.load()[0]
    assert after_adjust < 1.0
    tuner = chat._policy.tuner
    relaxed = 0
    for _ in range(30):                       # the same lines _adaptive_prune runs for every policy check
        with chat._activity.lifecycle_lock:
            if tuner.tick() and chat._settings.autotune:
                learned.relax()
                relaxed += 1
    assert relaxed >= 1 and learned.load()[0] > after_adjust


def test_the_seeded_flag_is_not_inherited_by_a_clone_of_the_engine():
    import copy
    _engine("s1").observe_host_compaction(session_id="s1", kind="auto")
    seeded = _engine("s2")
    assert seeded._policy.tuner.seeded
    clone = copy.deepcopy(seeded)
    assert clone._policy.tuner.seeded is False and clone._policy.tuner.pristine()


def test_one_day_of_reports_cannot_move_the_prior_past_the_daily_cap(tmp_path, monkeypatch):
    """1.7.1: however many chats report, a stored offset moves at most DAILY_CAP per UTC day."""
    monkeypatch.setenv("TAMERU_LEARNED_DIR", str(tmp_path))
    monkeypatch.setattr(learned, "DAILY_CAP", 0.12)
    for _ in range(40):
        learned.record([1.45, 0.55, 1.45, 0.55, 1.45])
    prior = learned.load()
    assert prior is not None
    assert all(abs(v - 1.0) <= learned.DAILY_CAP + 1e-6 for v in prior), prior


def test_the_daily_cap_resets_on_a_new_day(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMERU_LEARNED_DIR", str(tmp_path))
    monkeypatch.setattr(learned, "DAILY_CAP", 0.12)
    for _ in range(40):
        learned.record([1.45] * 5)
    capped = learned.load()
    monkeypatch.setattr(learned, "_today", lambda: "2099-01-01")
    for _ in range(40):
        learned.record([1.45] * 5)
    assert learned.load()[0] > capped[0] + 0.05
