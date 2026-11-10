"""The replay on a 32K window, over every session shape: where the context-overflow path and the clock matter.

On 64K and larger windows a 30-turn session never sends a request the provider would reject, so the simulated clock
and the overflow path (``replay.run_session(realistic=True)``, the default) change nothing there. On 32K they decide the
outcome: Hermes' compaction threshold is 27,200 tokens and one large tool result lands a request past the window. The
provider rejects it; Hermes compresses with ``bypass_cooldown=True`` and retries, or, behind its 300 s structural
no-op backoff, defers and the turn fails. Failed turns are not billed, so a cheaper cost can mean fewer answered
turns: read ``cost`` together with ``failed_turns``.

Acceptance (the plugin as shipped, ``tameru_1_4_product``, against Hermes as shipped, ``stock_defaults``; seed 0, 20 s
per request): cost per answered turn <= (raw cost is not comparable when the failed turns differ), gold >= per category, error templates >=, compactions <=, overflow errors <=, failed turns <=. A
check the plugin loses is a strict ``xfail`` in ``KNOWN_LOSSES`` with the measured numbers, never a loosened assertion.
Invariants that hold for every engine: no request over the window is ever sent, pairs stay intact, rows stay stable.

Baselines: ``baselines/w32k/<scenario>/<engine>.json``, compared exactly; ``TAMERU_WRITE_BASELINES=1`` re-records them.
Needs real Hermes (``HERMES_REPO_ROOT``).
"""
from __future__ import annotations

import os

import pytest

if os.environ.get("HERMES_REPO_ROOT", "").strip():
    import agent.context_compressor  # noqa: F401  (a broken checkout must fail the run, not skip it)
else:
    pytest.importorskip("agent.context_compressor")

import engines
import replay
import scenario_registry as registry
from scenario import CATEGORIES
from scenario_baselines import WRITE, baseline_path, load_baseline, write_baseline

pytestmark = pytest.mark.real_hermes

WINDOW = 32_000
SCENARIOS = tuple(registry.scenario_names())
#: Held-out recipes are adversarial additions; the "the shape must make Hermes compact" contract binds the dev four.
DEV = ("default", "oncall", "refactor", "smallrows", "i18n")
ENGINES = ("stock_defaults", "tameru_1_4_product")

#: ``(scenario, check, engine)`` -> why the plugin loses it (measured numbers, seed 0, 20 s per request, 32K window).
KNOWN_LOSSES: dict[tuple[str, str, str], str] = {
    # Why each check is lost. The measured numbers are in known_loss_numbers.json (test_known_loss_numbers.py keeps them current).
    ('smallrows', 'cost', 'tameru_1_4_product'): 'one more compaction than stock and larger summaries (the trail and anchor lines); nothing is prunable',
    ('smallrows', 'compactions', 'tameru_1_4_product'): 'the plugin compacts a little earlier each time, so a sixth compaction fits before the end',
    ('longform', 'cost', 'tameru_1_4_product'): "under the trigger: the only difference is the plugin's tool schema on every request",
    ('mixedlang', 'cost', 'tameru_1_4_product'): "under the trigger: the only difference is the plugin's tool schema on every request",
    ('diagnostics', 'cost', 'tameru_1_4_product'): "under the trigger: the only difference is the plugin's tool schema on every request",
    ('rereads', 'cost', 'tameru_1_4_product'): "under the trigger: the only difference is the plugin's tool schema on every request",
    ('dense', 'cost', 'tameru_1_4_product'): "under the trigger: the only difference is the plugin's tool schema on every request",
}

WINDOW_48K = 48_000
#: the same for a 48K window (``test_48k_*``): totals only (gold facts, error templates), no exact baselines.
KNOWN_LOSSES_48K: dict[tuple[str, str], str] = {
    # Why each check is lost. The measured numbers are in known_loss_numbers.json (test_known_loss_numbers.py keeps them current).
    ('refactor', 'cost'): 'more prune commits, each a cache break, than the compactions they replace',
    ('smallrows', 'cost'): 'the trail overhead in the summaries; nothing is prunable',
    ('longform', 'cost'): "under the trigger: the only difference is the plugin's tool schema on every request",
    ('mixedlang', 'cost'): "under the trigger: the only difference is the plugin's tool schema on every request",
    ('diagnostics', 'cost'): "under the trigger: the only difference is the plugin's tool schema on every request",
    ('rereads', 'cost'): "under the trigger: the only difference is the plugin's tool schema on every request",
    ('dense', 'cost'): "under the trigger: the only difference is the plugin's tool schema on every request",
}

_CACHE: dict[tuple[str, str, int], list[dict]] = {}


def runs(scenario: str, name: str, window: int = WINDOW) -> list[dict]:
    """Two independent runs (the second proves determinism), cached for the module."""
    key = (scenario, name, window)
    if key not in _CACHE:
        _CACHE[key] = [
            replay.run_session(lambda: engines.make_engine(name), scenario=scenario, context_length=window)
            for _ in range(2)
        ]
    return _CACHE[key]


def metrics(scenario: str, name: str, window: int = WINDOW) -> dict:
    return runs(scenario, name, window)[0]["metrics"]


def cost_per_answered_turn(m: dict) -> float:
    """Cache-priced cost over the turns that got an answer.

    A turn that fails with a context overflow bills nothing, so raw cost rewards the engine that fails more turns.
    Where both engines answer every turn this is the raw cost scaled by the same constant.
    """
    return m["total_simulated_input_cost"] / max(1, m["turns"] - m["failed_turns"])


def scenario_param(check: str, engine: str = "tameru_1_4_product"):
    out = []
    for scn in SCENARIOS:
        reason = KNOWN_LOSSES.get((scn, check, engine))
        marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        out.append(pytest.param(scn, id=scn, marks=marks))
    return out


def matrix_param(check: str, engine: str = "tameru_1_4_product"):
    out = []
    for scn in SCENARIOS:
        for cat in CATEGORIES:
            reason = KNOWN_LOSSES.get((scn, f"{check}:{cat}", engine))
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            out.append(pytest.param(scn, cat, id=f"{scn}-{cat}", marks=marks))
    return out


# ---- the harness on a small window -----------------------------------------------------------------------------
@pytest.mark.parametrize("name", ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_no_request_over_the_window_is_ever_sent(scenario, name):
    m = metrics(scenario, name)
    assert m["requests_over_window"] == 0
    assert 0 < m["max_request_window_ratio"] <= 1.0
    assert m["max_request_window_ratio"] <= m["max_attempted_window_ratio"]
    assert m["failed_turns"] <= m["overflow_errors"]
    assert len(m["failed_turn_reasons"]) == m["failed_turns"]


@pytest.mark.parametrize("name", ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_pairs_stay_intact_and_rows_stable(scenario, name):
    m = metrics(scenario, name)
    assert m["pairing"]["violations"] == 0, m["pairing"]["details"]
    assert m["byte_stability"]["rewrites_outside_commits"] == 0, m["byte_stability"]["details"]
    assert m["byte_stability"]["input_mutations"] == 0, m["byte_stability"]["input_mutation_details"]


@pytest.mark.parametrize("name", ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_replay_is_deterministic(scenario, name):
    first, second = runs(scenario, name)
    assert replay.deterministic_part(first) == replay.deterministic_part(second)


@pytest.mark.parametrize("scenario", DEV)
def test_hermes_as_shipped_compacts_on_a_small_window(scenario):
    """If Hermes never compacted on 32K the rows below would measure nothing."""
    m = metrics(scenario, "stock_defaults")
    assert m["context_length"] == WINDOW
    assert m["compactions"] >= 2 and m["max_attempted_window_ratio"] > 0.8


# ---- exact comparison with the saved baselines ---------------------------------------------------------------------
@pytest.mark.parametrize("name", ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_matches_saved_baseline(scenario, name):
    result = runs(scenario, name)[0]
    if WRITE:
        write_baseline(name, result, scenario, WINDOW)
    path = baseline_path(name, scenario, WINDOW)
    assert path.is_file(), f"{path} missing; run this file once with TAMERU_WRITE_BASELINES=1"
    assert replay.deterministic_part(result) == load_baseline(name, scenario, WINDOW), (
        f"the {name} replay of {scenario!r} on {WINDOW} differs from {path.relative_to(path.parents[3])} "
        "(Hermes, the engine or the harness changed; if intended, re-record with TAMERU_WRITE_BASELINES=1)"
    )


# ---- acceptance: the plugin as shipped against Hermes as shipped ---------------------------------------------------------
@pytest.mark.parametrize("scenario", scenario_param("cost"))
def test_acceptance_cost(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product"), metrics(scenario, "stock_defaults")
    assert cost_per_answered_turn(ours) <= cost_per_answered_turn(theirs)


@pytest.mark.parametrize(("scenario", "category"), matrix_param("gold"))
def test_acceptance_gold_per_category(scenario, category):
    ours = metrics(scenario, "tameru_1_4_product")["gold"]["by_category"][category]
    theirs = metrics(scenario, "stock_defaults")["gold"]["by_category"][category]
    assert ours["retained_ids"] is not None and theirs["retained_ids"] is not None, "Closing probe not observed"
    assert set(theirs["retained_ids"]) <= set(ours["retained_ids"])
    assert ours["retained"] >= theirs["retained"], (ours["missing"], theirs["missing"])


@pytest.mark.parametrize("scenario", scenario_param("errors"))
def test_acceptance_error_templates(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product"), metrics(scenario, "stock_defaults")
    assert ours["error_fingerprints"]["retained"] >= theirs["error_fingerprints"]["retained"]


@pytest.mark.parametrize("scenario", scenario_param("compactions"))
def test_acceptance_compactions(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product"), metrics(scenario, "stock_defaults")
    assert ours["compactions"] <= theirs["compactions"]


@pytest.mark.parametrize("scenario", scenario_param("overflow"))
def test_acceptance_overflow_errors(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product"), metrics(scenario, "stock_defaults")
    assert ours["overflow_errors"] <= theirs["overflow_errors"]


@pytest.mark.parametrize("scenario", scenario_param("failed_turns"))
def test_acceptance_failed_turns(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product"), metrics(scenario, "stock_defaults")
    assert ours["failed_turns"] <= theirs["failed_turns"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_plugin_answers_every_turn_on_a_small_window(scenario):
    """The prune must keep the prompt under the window: no overflow, no failed turn, whatever the stock engine does."""
    m = metrics(scenario, "tameru_1_4_product")
    assert m["overflow_errors"] == 0 and m["failed_turns"] == 0, m["failed_turn_reasons"]


# ---- a 48K window (threshold 40,800: the fixed 48,000-token prune trigger is above it) --------------------------------
def _param_48k(check: str):
    return [
        pytest.param(scn, id=scn, marks=[pytest.mark.xfail(strict=True, reason=KNOWN_LOSSES_48K[(scn, check)])]
                     if (scn, check) in KNOWN_LOSSES_48K else [])
        for scn in SCENARIOS
    ]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_48k_the_plugin_never_fails_more_turns_than_hermes(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product", WINDOW_48K), metrics(scenario, "stock_defaults", WINDOW_48K)
    assert ours["failed_turns"] == 0 and ours["overflow_errors"] <= theirs["overflow_errors"]
    assert ours["requests_over_window"] == 0


@pytest.mark.parametrize("scenario", _param_48k("gold"))
def test_48k_gold_errors_and_compactions(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product", WINDOW_48K), metrics(scenario, "stock_defaults", WINDOW_48K)
    assert ours["gold"]["observed"] and theirs["gold"]["observed"], "Closing probe not observed"
    assert set(theirs["gold"]["retained_ids"]) <= set(ours["gold"]["retained_ids"])
    assert ours["gold"]["retained"] >= theirs["gold"]["retained"]
    assert ours["error_fingerprints"]["retained"] >= theirs["error_fingerprints"]["retained"]
    assert ours["compactions"] <= theirs["compactions"]


@pytest.mark.parametrize("scenario", _param_48k("cost"))
def test_48k_cost(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product", WINDOW_48K), metrics(scenario, "stock_defaults", WINDOW_48K)
    assert cost_per_answered_turn(ours) <= cost_per_answered_turn(theirs)
