"""Named scenarios for ``replay.run_session(scenario=...)``, ``compare.py --scenario`` and the tests.

``default`` is the original scripted 30-turn session (``scenario.build_scenario``); its behaviour and its saved
baselines are unchanged. The others (``scenarios_extra``) are different session shapes, written to check that
what the default session shows is not a property of that one script. ``SCENARIOS`` maps a name to a
``ScenarioSpec``; a test can add one with ``register_scenario``.
"""
from __future__ import annotations

import scenario as _original
from scenario import N_TURNS
from scenario_kit import ScenarioSpec
from scenarios_extra import EXTRA_SPECS
from scenarios_refactor import REFACTOR_SPEC
from scenarios_rows import I18N_SPEC, SMALLROWS_SPEC

DEFAULT_SCENARIO = "default"

SCENARIOS: dict[str, ScenarioSpec] = {}


def register_scenario(spec: ScenarioSpec) -> ScenarioSpec:
    SCENARIOS[spec.name] = spec
    return spec


def get_spec(name: str = DEFAULT_SCENARIO) -> ScenarioSpec:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise KeyError(f"unknown scenario {name!r}; registered: {sorted(SCENARIOS)}") from None


def scenario_names() -> tuple[str, ...]:
    """Registered names, ``default`` first."""
    return tuple(SCENARIOS)


register_scenario(ScenarioSpec(
    DEFAULT_SCENARIO, _original.build_scenario, N_TURNS, 4,
    "30 turns, one incident story: source reads, a patch and re-reads, test runs, CI and log dumps, a CJK log",
))
for _spec in (*EXTRA_SPECS, REFACTOR_SPEC, SMALLROWS_SPEC, I18N_SPEC):
    register_scenario(_spec)
