"""Adaptive summary threshold and protected tail: bounded, explained, never written to config, and safe to switch off."""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest
from engine_support import Chat, demote, make_engine, pytest_failure_with_passes
from test_autotune_engine import _MetaDB

from hermes_tameru_plugin import host_tuning as ht
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.engine import TameruContextEngine
from hermes_tameru_plugin.host_tuning import HostTuning

WINDOW = 200_000
BASE = 0.50
BASE_N = 20


def _run(tuning, prompts, *, base=BASE, base_n=BASE_N, cache=None, window=WINDOW):
    outcome = None
    for prompt in prompts:
        outcome = tuning.step(prompt_tokens=prompt, window=window, base_percent=base, base_last_n=base_n, cache_hit_rate=cache)
    return outcome


def _steady(start, per_turn, turns):
    return [start + per_turn * i for i in range(turns)]


# ---- the summary threshold -------------------------------------------------------------------------------
def test_nothing_moves_before_there_is_data():
    tuning = HostTuning()
    out = _run(tuning, [50_000, 50_100])
    assert out["percent"] == BASE and out["last_n"] == BASE_N and out["reasons"] == []


def test_a_slowly_growing_chat_pushes_the_summary_later_but_never_past_the_bound():
    tuning = HostTuning()
    out = _run(tuning, _steady(20_000, 1_500, 40))
    assert BASE < out["percent"] <= min(ht.CEILING, BASE + ht.ABOVE) + 1e-9
    assert any("later" in reason and "grows slowly" in reason for reason in tuning_reasons(_steady(20_000, 1_500, 40)))


def tuning_reasons(prompts, **kw):
    tuning, found = HostTuning(), []
    for prompt in prompts:
        found += tuning.step(prompt_tokens=prompt, window=kw.get("window", WINDOW), base_percent=kw.get("base", BASE),
                             base_last_n=BASE_N, cache_hit_rate=kw.get("cache"))["reasons"]
    return found


def test_a_chat_that_jumps_in_big_steps_pulls_the_summary_earlier_but_never_under_the_bound():
    tuning = HostTuning()
    out = _run(tuning, _steady(20_000, 60_000, 6))              # 30% of the window per reply
    assert max(ht.FLOOR, BASE - ht.BELOW) - 1e-9 <= out["percent"] < BASE
    assert any("earlier" in reason for reason in tuning_reasons(_steady(20_000, 60_000, 6)))


def test_each_turn_moves_the_threshold_by_at_most_one_step():
    tuning, last = HostTuning(), BASE
    for prompt in _steady(20_000, 1_000, 60):
        out = tuning.step(prompt_tokens=prompt, window=WINDOW, base_percent=BASE, base_last_n=BASE_N)
        assert abs(out["percent"] - last) <= ht.STEP + 1e-9
        last = out["percent"]


def test_a_cold_cache_never_lets_the_threshold_rise_above_the_configured_value():
    tuning = HostTuning()
    out = _run(tuning, _steady(20_000, 1_000, 40), cache=10)
    assert out["percent"] <= BASE + 1e-9


def test_a_warm_cache_is_allowed_to_rise():
    out = _run(HostTuning(), _steady(20_000, 1_000, 40), cache=90)
    assert out["percent"] > BASE


def test_after_hermes_summarised_on_its_own_the_threshold_stops_rising_for_a_while():
    tuning = HostTuning()
    _run(tuning, _steady(20_000, 1_000, 30))
    assert tuning.offset > 0
    tuning.note_host_summary()
    assert tuning.offset <= 0
    held = _run(tuning, _steady(60_000, 1_000, ht.COOLDOWN_TURNS - 1))
    assert held["percent"] <= BASE + 1e-9
    later = _run(tuning, _steady(70_000, 1_000, 30))
    assert later["percent"] > BASE, "it may rise again once the cooldown is over"


def test_the_threshold_never_leaves_its_bounds_whatever_the_numbers_are():
    rng = random.Random(11)
    for _ in range(60):
        tuning, base = HostTuning(), rng.choice([0.3, 0.5, 0.75, 0.85])
        low, high = max(ht.FLOOR, base - ht.BELOW), min(ht.CEILING, base + ht.ABOVE)
        window = rng.choice([32_000, 136_000, 1_000_000])
        prompt = 10_000
        for _ in range(80):
            prompt = max(1, prompt + rng.randint(-20_000, 60_000))
            out = tuning.step(prompt_tokens=prompt, window=window, base_percent=base, base_last_n=20,
                              cache_hit_rate=rng.choice([None, 5, 50, 99]))
            assert low - 1e-9 <= out["percent"] <= max(high, base) + 1e-9
            assert math.isfinite(out["percent"])
            if rng.random() < 0.05:
                tuning.note_host_summary()


# ---- the protected tail -------------------------------------------------------------------------------------
def test_revisiting_old_outputs_widens_the_protected_tail_up_to_double():
    tuning = HostTuning()
    widths = []
    for _ in range(10):
        tuning.note_reach(1.0)
        widths.append(_run(tuning, [50_000])["last_n"])
    assert widths == sorted(widths) and widths[-1] == BASE_N * 2 == 40
    assert any("revisited" in reason for reason in tuning_reasons_reach())


def tuning_reasons_reach():
    tuning, found = HostTuning(), []
    for _ in range(8):
        tuning.note_reach(1.0)
        found += tuning.step(prompt_tokens=50_000, window=WINDOW, base_percent=BASE, base_last_n=BASE_N)["reasons"]
    return found


def test_the_tail_relaxes_back_to_the_configured_size_when_nothing_is_revisited():
    tuning = HostTuning()
    for _ in range(6):
        tuning.note_reach(1.0)
        _run(tuning, [50_000])
    assert tuning.scale > 1.5
    out = _run(tuning, [50_000] * 40)
    assert out["last_n"] == BASE_N


def test_a_quiet_fast_growing_chat_may_shrink_the_tail_a_little_but_not_below_three_quarters():
    tuning = HostTuning()
    out = _run(tuning, _steady(10_000, 12_000, 40), window=WINDOW)       # 6% of the window per reply, nobody goes back
    assert BASE_N * 0.75 - 1 <= out["last_n"] <= BASE_N


def test_the_tail_stays_inside_six_to_sixty_messages():
    for base_n in (2, 6, 20, 45, 200):
        tuning = HostTuning()
        for _ in range(20):
            tuning.note_reach(1.0)
            out = tuning.step(prompt_tokens=50_000, window=WINDOW, base_percent=BASE, base_last_n=base_n)
        assert out["last_n"] <= max(ht.TAIL_MAX, base_n) and out["last_n"] >= min(ht.TAIL_MIN, base_n)


# ---- bad input, saving ------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, "lots", -5, 0, True, float("nan"), 1e30, [], {}])
def test_bad_numbers_never_raise_and_never_move_anything(bad):
    tuning = HostTuning()
    out = tuning.step(prompt_tokens=bad, window=WINDOW, base_percent=BASE, base_last_n=BASE_N, cache_hit_rate=bad)
    assert out["percent"] == BASE and out["last_n"] == BASE_N


def test_saved_numbers_round_trip_and_are_clamped_on_load():
    tuning = HostTuning()
    _run(tuning, _steady(20_000, 1_000, 40))
    again = HostTuning()
    assert again.load(tuning.state()) and again.offset == pytest.approx(tuning.offset, abs=1e-3)
    assert again.load({"offset": 9, "scale": 99}) and again.offset == ht.ABOVE and again.scale == ht.SCALE_MAX
    for bad in (None, {}, {"offset": "x", "scale": 1}, {"offset": float("nan"), "scale": 1}, 5):
        assert HostTuning().load(bad) is False


# ---- the engine --------------------------------------------------------------------------------------------
def _engine(db=None, **settings):
    settings.setdefault("adaptive_host", True)           # off by default; these tests are about the feature
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **settings))
    engine.bind_session_state(session_db=db or _MetaDB(), session_id="s1")
    _ = engine.context_length          # real Hermes applies its small-window floor when the window is first read
    return engine


def _grow(engine, turns=40, start=10_000, per_turn=900):
    for i in range(turns):
        engine.last_real_prompt_tokens = start + per_turn * i
        engine.adapt_host_settings()


def test_a_slow_chat_moves_the_engines_own_values_and_leaves_hermes_to_derive_the_tokens():
    engine = _engine()
    base, base_n = engine.threshold_percent, engine.protect_last_n
    _grow(engine)
    assert base < engine.threshold_percent <= min(ht.CEILING, base + ht.ABOVE) + 1e-9
    assert engine.threshold_tokens == int(engine.context_length * engine.threshold_percent)
    assert engine.protect_last_n == base_n


def test_tameru_prunes_from_the_configured_threshold_not_the_tuned_one():
    """Host tuning changes when Hermes summarises; the size Tameru's own pruning is sized from stays what the user configured."""
    engine = _engine()
    configured, base = engine._policy_threshold_tokens(), engine.threshold_percent
    assert configured == engine.threshold_tokens
    _grow(engine)
    assert engine.threshold_percent > base                            # Hermes' trigger moved later
    assert engine._policy_threshold_tokens() == configured  # Tameru's did not


def test_revisiting_an_old_output_widens_the_engines_protected_tail():
    from test_edits import _shortened
    engine, out, idx, ref, original = _shortened(adaptive_host=True)
    engine.threshold_tokens = 10_000_000                                  # room, so the Restore is accepted
    base_n = engine.protect_last_n
    for _ in range(4):                                                    # revisits on several turns
        engine._host.note_reach(2.0)
        engine.adapt_host_settings()
    assert engine.protect_last_n > base_n and engine.protect_last_n <= base_n * 2


def test_the_effective_values_are_never_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    engine = _engine()
    before = sorted(p.name for p in tmp_path.rglob("*"))
    _grow(engine)
    assert sorted(p.name for p in tmp_path.rglob("*")) == before, "no config or state file is created or changed"


@pytest.mark.parametrize("setting", [{"adaptive_host": False}, {"enabled": False}])
def test_switched_off_means_the_values_never_move(setting):
    engine = _engine(**setting)
    base, base_n = engine.threshold_percent, engine.protect_last_n
    _grow(engine)
    assert engine.threshold_percent == base and engine.protect_last_n == base_n


def test_other_policy_modes_are_left_alone():
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="legacy"))
    engine.bind_session_state(session_db=_MetaDB(), session_id="s1")
    _ = engine.context_length
    base = engine.threshold_percent
    _grow(engine)
    assert engine.threshold_percent == base


def test_pausing_puts_the_configured_values_back_exactly():
    engine = _engine()
    base, base_n = engine.threshold_percent, engine.protect_last_n
    _grow(engine)
    engine._host.note_reach(1.0); engine.adapt_host_settings()
    assert engine.threshold_percent != base or engine.protect_last_n != base_n
    engine._policy.paused = True
    engine.adapt_host_settings()
    assert engine.threshold_percent == base and engine.protect_last_n == base_n


def test_a_hermes_summary_stops_the_threshold_rising_again_for_a_while():
    engine = _engine()
    _grow(engine)
    risen = engine.threshold_percent
    engine.observe_host_compaction(session_id="s1", kind="auto", reason="threshold", before_tokens=100_000, after_tokens=40_000)
    assert engine._host.offset <= 0
    engine.adapt_host_settings()
    assert engine.threshold_percent <= risen


def test_the_numbers_are_saved_per_chat_and_come_back_after_a_restart():
    db = _MetaDB()
    engine = _engine(db)
    _grow(engine)
    assert db.config.get(("s1", "tameru_host_v1")), "saved under the chat's own metadata"
    restarted = _engine(db)
    assert restarted._host.offset == pytest.approx(engine._host.offset, abs=1e-3)


def test_the_snapshot_says_what_hermes_is_using_next_to_what_was_configured():
    engine = _engine()
    host = engine.activity_snapshot()["progress"]["host"]
    assert host["enabled"] and host["adapted"] is False and host["base_percent"] == host["threshold_percent"]
    _grow(engine)
    host = engine.activity_snapshot()["progress"]["host"]
    assert host["adapted"] is True and host["threshold_percent"] > host["base_percent"]
    assert host["base_last_n"] == engine.protect_last_n


def test_each_turn_start_takes_one_step():
    engine = _engine()
    engine.last_real_prompt_tokens = 30_000
    engine._activity_registry.pre_llm_call(session_id="s1", conversation_history=[])
    assert engine._host.last_prompt == 30_000


def test_a_fault_inside_the_tuning_never_reaches_the_turn(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(engine._host, "step", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    engine.adapt_host_settings()                                            # must not raise


class _RealShape:
    """The shape of Hermes' compressor: threshold_tokens is a lazily derived property."""
    protect_last_n = 20
    threshold_percent = 0.5
    context_length = 100_000

    def __init__(self):
        self._threshold_tokens = 50_000
        self._tail_token_budget = 7_000

    @property
    def threshold_tokens(self):
        return self._threshold_tokens

    @threshold_tokens.setter
    def threshold_tokens(self, value):
        self._threshold_tokens = value

    @staticmethod
    def _effective_threshold_percent(window, percent):
        return max(percent, 0.75) if window < 512_000 else percent


def test_with_hermes_style_derived_tokens_only_the_percent_is_set_and_hermes_floors_still_apply():
    fake = _RealShape()
    TameruContextEngine._apply_host(fake, 0.60, 30)
    assert fake.protect_last_n == 30
    assert fake.threshold_percent == 0.75, "Hermes' own raise-only floor for small windows still wins"
    assert fake._threshold_tokens is None and fake._tail_token_budget is None, "cleared so Hermes derives them again"


@pytest.mark.real_hermes
def test_hermes_applies_the_threshold_percent_to_the_effective_input_window():
    """The tuner's ``window`` is that effective window (context less the reply reserve), because Hermes multiplies
    ``threshold_percent`` by it, not by the whole context: a target taken against any other size would be wrong."""
    from agent.context_compressor import ContextCompressor
    assert ContextCompressor._compute_threshold_tokens(400_000, 0.6, 100_000) == int((400_000 - 100_000) * 0.6)
    assert ContextCompressor._compute_threshold_tokens(400_000, 0.6, None) == int(400_000 * 0.6)


def test_an_error_that_is_swallowed_is_still_counted_and_shown():
    engine = _engine()
    engine._host.step = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    engine.adapt_host_settings()                                  # never raises into the chat turn
    where = engine._telemetry.snapshot()["events"]["error"]["labels"]["where"]
    assert where.get("adapt_host_settings") == 1
    assert engine.activity_snapshot()["progress"]["doctor"]["errors_ignored"] >= 1
