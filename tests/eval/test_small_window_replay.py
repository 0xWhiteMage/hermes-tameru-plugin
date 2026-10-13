"""The replay on a 32K window, over every session shape: where the context-overflow path and the clock matter.

On 64K and larger windows a 30-turn session never sends a request the provider would reject, so the simulated clock
and the overflow path (``replay.run_session(realistic=True)``, the default) change nothing there. On 32K they decide the
outcome: Hermes' compaction threshold is 27,200 tokens and one large tool result lands a request past the window. The
provider rejects it; Hermes compresses with ``bypass_cooldown=True`` and retries, or, behind its 300 s structural
no-op backoff, defers and the turn fails. Failed turns are not billed, so a cheaper cost can mean fewer answered
turns: read ``cost`` together with ``failed_turns``.

Acceptance (the plugin as shipped, ``tameru_1_4_product``, against Hermes as shipped, ``stock_defaults``; seed 0, 20 s
per request): cost <=, gold >= per category, error templates >=, compactions <=, overflow errors <=, failed turns <=. A
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
ENGINES = ("stock_defaults", "tameru_1_4_product")

#: ``(scenario, check, engine)`` -> why the plugin loses it (measured numbers, seed 0, 20 s per request, 32K window).
KNOWN_LOSSES: dict[tuple[str, str, str], str] = {
    ("default", "cost", "tameru_1_4_product"): (
        "LOSS +3.7%: 610,747 vs Hermes as shipped 589,184 char-eq (seed 0). Same 49 requests, same compactions (requests 17, "
        "25, 39 after a no-op at 35) and the same 6 failed turns (the 27,200-token threshold sits under one large tool "
        "result; Hermes' 300 s structural no-op backoff then defers the turn); the plugin's requests are 4% bigger (2.70M vs "
        "2.59M input chars) and it keeps 7/7 error templates against 0/7. Its proactive prune never fires: the 48,000-token "
        "trigger is above the 32K window (REPORT-F item F)."
    ),
    ("oncall", "cost", "tameru_1_4_product"): (
        "LOSS +2.4%: 1,043,688 vs 1,018,981 char-eq (seed 0). Same 75 requests, 6 compactions, 6 failed turns and the same "
        "3.94M input chars; the difference is prefix rewrites (449,291 vs 432,008 invalidated chars) for 11/17 error templates "
        "against 0/17 and one more gold fact. Its proactive prune never fires (48,000-token trigger above the window)."
    ),
    ("smallrows", "cost", "tameru_1_4_product"): (
        "LOSS +8.9%: 1,350,518 vs 1,240,606 char-eq (seed 0). Nothing overflows (99 requests, no failed turn); the plugin "
        "compacts 6 times (requests 20, 31, 40, 60, 73, 96) against 5 (20, 32, 44, 64, 77): 6.85M vs 6.36M input chars and "
        "530,969 vs 445,253 invalidated chars, for 11/38 error templates against 10/38."
    ),
    ("smallrows", "compactions", "tameru_1_4_product"): (
        "LOSS 6 compactions vs 5 (seed 0): the plugin compacts earlier each time (31 vs 32, 40 vs 44, 60 vs 64, 73 vs 77) "
        "and a sixth one at 96 falls before the end."
    ),
    ("i18n", "cost", "tameru_1_4_product"): (
        "LOSS +2.5%: 394,233 vs 384,792 char-eq (seed 0). 16 (plugin) and 17 (Hermes) of 29 turns fail behind the structural "
        "backoff (CJK rows are token-dense), so only 31 requests are billed on both sides; the plugin compacts 3 times "
        "against 2 (requests 5, 16, 29 against 5, 19) and invalidates 187,115 chars against 114,078."
    ),
    ("i18n", "gold:artifact", "tameru_1_4_product"): (
        "LOSS 1/4 vs 3/4 artifact facts (seed 0): A2 and A3 are missing from the closing request, which follows the plugin's "
        "third compaction (request 29, an overflow compression); Hermes' last two compressions are no-ops behind the backoff "
        "and keep the text."
    ),
    ("i18n", "compactions", "tameru_1_4_product"): (
        "LOSS 3 compactions vs 2 (seed 0): the third is the overflow compression at request 29; Hermes' attempts at 27 and 31 "
        "are structural no-ops."
    ),
}

_CACHE: dict[tuple[str, str], list[dict]] = {}


def runs(scenario: str, name: str) -> list[dict]:
    """Two independent runs (the second proves determinism), cached for the module."""
    key = (scenario, name)
    if key not in _CACHE:
        _CACHE[key] = [
            replay.run_session(lambda: engines.make_engine(name), scenario=scenario, context_length=WINDOW)
            for _ in range(2)
        ]
    return _CACHE[key]


def metrics(scenario: str, name: str) -> dict:
    return runs(scenario, name)[0]["metrics"]


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


@pytest.mark.parametrize("name", ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_replay_is_deterministic(scenario, name):
    first, second = runs(scenario, name)
    assert replay.deterministic_part(first) == replay.deterministic_part(second)


@pytest.mark.parametrize("scenario", SCENARIOS)
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
    assert ours["cost_cache_priced"] <= theirs["cost_cache_priced"]


@pytest.mark.parametrize(("scenario", "category"), matrix_param("gold"))
def test_acceptance_gold_per_category(scenario, category):
    ours = metrics(scenario, "tameru_1_4_product")["gold"]["by_category"][category]
    theirs = metrics(scenario, "stock_defaults")["gold"]["by_category"][category]
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
