"""Pure unit tests for the blind scenarios (no replay, no product code)."""
from __future__ import annotations

import json
import sys
import types

import pytest

import scenarios_blind
from scenarios_blind import BLIND_SPECS, register_blind

NAMES = ("blind_migration", "blind_perf", "blind_audit", "blind_support")
SPECS = {s.name: s for s in BLIND_SPECS}
CATS = {"recall", "artifact", "continuation", "decision"}


def _results(sc):
    return [s.result for t in sc.turns for r in t.rounds for s in r.steps]


def _dump(sc):
    return [(t.user, t.final, [(r.say, [(s.name, json.dumps(s.args), s.result, s.key) for s in r.steps])
                               for r in t.rounds]) for t in sc.turns]


def test_four_specs():
    assert tuple(s.name for s in BLIND_SPECS) == NAMES
    for s in BLIND_SPECS:
        assert 36 <= s.turns <= 48


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_builds_deterministically(name, seed):
    spec = SPECS[name]
    a, b = spec.build(seed, spec.turns), spec.build(seed, spec.turns)
    assert _dump(a) == _dump(b)
    assert [g.needle for g in a.gold] == [g.needle for g in b.gold]
    assert len(a.turns) == spec.turns - 1


@pytest.mark.parametrize("name", NAMES)
def test_seeds_differ(name):
    spec = SPECS[name]
    assert _dump(spec.build(0, spec.turns)) != _dump(spec.build(1, spec.turns))


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_needles_present(name, seed):
    spec = SPECS[name]
    sc = spec.build(seed, spec.turns)
    for g in sc.gold:
        t = sc.turns[g.turn - 1]
        if g.source == "user":
            hays = [t.user]
        elif g.source == "assistant":
            hays = [t.final, *(r.say for r in t.rounds)]
        else:
            hays = [s.result for r in t.rounds for s in r.steps if s.key == g.call_key]
        esc = json.dumps(g.needle)[1:-1]
        assert any(g.needle in h or esc in h for h in hays), (name, g.id)


@pytest.mark.parametrize("name", NAMES)
def test_size_and_shape(name):
    spec = SPECS[name]
    sc = spec.build(0, spec.turns)
    sizes = [len(x) for x in _results(sc)]
    assert sum(sizes) >= 900_000
    assert all(6_000 <= n <= 40_000 for n in sizes), (min(sizes), max(sizes))
    assert sc.final_question


@pytest.mark.parametrize("name", NAMES)
def test_gold_requirements(name):
    spec = SPECS[name]
    for seed in (0, 1, 2):
        sc = spec.build(seed, spec.turns)
        gold = sc.gold
        needed = [g for g in gold if g.needed]
        assert len(gold) >= 14 and len(needed) >= 12
        assert {g.category for g in gold} == CATS
        assert {g.source for g in gold} == {"user", "assistant", "tool"}
        third = len(sc.turns) / 3
        assert len([g for g in needed if g.turn <= third]) >= 3
        early_decisions = [g for g in gold if g.category == "decision" and g.source == "user" and g.turn <= third]
        assert len(early_decisions) >= 2
        assert len({g.id for g in gold}) == len(gold)


@pytest.mark.parametrize("name", ("blind_audit", "blind_support"))
def test_error_lines(name):
    spec = SPECS[name]
    sc = spec.build(0, spec.turns)
    assert len(sc.error_lines) >= 3
    blob = "\n".join(_results(sc))
    assert all(line in blob for line in sc.error_lines)


def test_register_is_lazy_and_idempotent(monkeypatch):
    calls = []
    fake = types.ModuleType("scenario_registry")
    fake.register_scenario = lambda spec: calls.append(spec.name)
    monkeypatch.setitem(sys.modules, "scenario_registry", fake)
    monkeypatch.setattr(scenarios_blind, "_REGISTERED", False)
    register_blind()
    register_blind()
    assert calls == list(NAMES)
