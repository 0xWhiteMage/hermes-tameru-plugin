"""The replay over the extra session shapes: ``oncall``, ``refactor``, ``smallrows``, ``i18n``.

The default 30-turn session (``test_session_replay.py``) is one shape, and every default of the plugin was tuned on
it. These scenarios are different shapes of agent work; the plugin as shipped must beat Hermes as shipped on each
of them, or the tuning is a property of one script.

Needs real Hermes (``HERMES_REPO_ROOT``). Baselines live in ``baselines/<scenario>/<engine>.json`` and are compared
exactly (deterministic metrics only). Record them with ``TAMERU_WRITE_BASELINES=1`` (run THIS file alone: the
default session's baselines are not to be re-recorded as a side effect).

Acceptance (per scenario, plugin as shipped = ``tameru_1_4_product`` against Hermes as shipped = ``stock_defaults``):
cost after cache pricing <=; gold facts retained >= in every probe category; error templates retained >=; no
pairing violations; no tool row rewritten between commits. The equal-gates plugin (``tameru_1_4``) is held to the
same cost / gold / error checks against ``stock`` (Hermes with the prune on at 48K).

A check the plugin loses is an ``xfail(strict=True)`` in ``KNOWN_LOSSES`` with the measured numbers, never a loosened
assertion; strict means the day the plugin wins it again the test fails and the entry has to be deleted.
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

SCENARIOS = tuple(n for n in registry.scenario_names() if n != registry.DEFAULT_SCENARIO)
MIN_COMPACTIONS = {"oncall": 2, "refactor": 1, "smallrows": 1, "i18n": 1}  # of Hermes as shipped, on a 64K window

#: ``(scenario, check, engine)`` -> why the plugin loses it (measured numbers; see docs/REPORT-1.4.1.md section 9).
KNOWN_LOSSES: dict[tuple[str, str, str], str] = {
    ("smallrows", "cost", "tameru_1_4_product"): (
        "LOSS +1.7%: 1,725,663 vs Hermes as shipped 1,696,390 char-eq (seed 0; was +2.0% before the summarizer read a Tameru row "
        "as Hermes' line: summary prompts 69,360 chars, were 81,833, Hermes 53,085). Since the plugin's prune floor is Hermes' own "
        "8,000 chars (it was 2,000: +21.7% here, small prunes paying full-suffix cache rewrites), no row of this scenario is "
        "pruned and the run equals the equal-gates one: the loss is the summary's Artifact Trail (2.5K) and the Anchor Index "
        "lines for the error templates it keeps (79,378 vs 75,264 chars after the first compaction; the same run without the "
        "ledger is +0.3%), which buys 21/38 error templates against 10/38."
    ),
    ("smallrows", "gold:decision", "tameru_1_4_product"): (
        "LOSS 3/4 vs 4/4 decision facts (seed 0): D3, as for tameru_1_4 below (the run is identical since the 8,000-char prune "
        "floor): the plugin's first compaction keeps 68 messages against Hermes' 94, so the user turn carrying D3 falls into the "
        "first summary and the fake summarizer drops it."
    ),
    ("refactor", "cost", "tameru_1_4"): (
        "LOSS +1.8%: 1,526,442 vs stock (prune on at 48K) 1,499,065 char-eq (seed 0; +1.9..+6.7% over seeds 0-3 before the summary view). Same single prune (request 29) "
        "and compaction (57) as stock, but every context after a rewrite is 3-4K chars bigger: 86,310 vs 83,078 after the "
        "prune (brief/errors/header text where stock keeps 80-character one-liners) and 56,153 vs 52,430 after the compaction "
        "(Artifact Trail + Errors ledger in the summary). That is the price of 9/9 error templates (stock 1/9)."
    ),
    ("smallrows", "cost", "tameru_1_4"): (
        "LOSS +1.7%: 1,725,663 vs stock 1,696,390 char-eq (seed 0; was +2.0%, +1.3..2.1% over seeds 0-3, before the summary view). Neither engine "
        "prunes (no 8,000-char result exists); both compact at requests 41 and 79. The plugin's summary carries the Artifact "
        "Trail and the Anchor Index lines of the error templates: 79,378 vs 75,264 chars after the first compaction and 78,657 vs "
        "75,760 after the second, bought 21/38 error templates against 10/38."
    ),
    ("smallrows", "gold:decision", "tameru_1_4"): (
        "LOSS 3/4 vs 4/4 decision facts (HEAD b7bb68c, seed 0): D3 (user, 'Do not bump its base image until its schema migration "
        "has shipped') is missing. Hermes quotes the user messages of the compacted region verbatim; the plugin's first "
        "compaction keeps 68 messages (stock: 94, whose old tool rows are 80-character stubs), so the 'inventory.' and 'ledger.' "
        "user turns fall into the first summary, not the second one's verbatim section, and the fake summarizer drops them. The "
        "plugin's own larger rows (briefs, extracts) take the token budget of the tail walk."
    ),
}

_CACHE: dict[tuple[str, str], list[dict]] = {}


def runs(scenario: str, name: str) -> list[dict]:
    """Two independent runs of the engine on the scenario (cached for the module): the second proves determinism."""
    key = (scenario, name)
    if key not in _CACHE:
        _CACHE[key] = [
            replay.run_session(lambda: engines.make_engine(name), scenario=scenario) for _ in range(2)
        ]
    return _CACHE[key]


def metrics(scenario: str, name: str) -> dict:
    return runs(scenario, name)[0]["metrics"]


def matrix_param(check: str, engine: str):
    """``(scenario, category)`` params of a per-category check, strict-xfailed where ``KNOWN_LOSSES`` says so."""
    out = []
    for scn in SCENARIOS:
        for cat in CATEGORIES:
            reason = KNOWN_LOSSES.get((scn, f"{check}:{cat}", engine))
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            out.append(pytest.param(scn, cat, id=f"{scn}-{cat}", marks=marks))
    return out


def scenario_param(check: str, engine: str):
    """``pytest.param`` per scenario, strict-xfailed when the plugin is known to lose ``check`` there."""
    out = []
    for scn in SCENARIOS:
        reason = KNOWN_LOSSES.get((scn, check, engine))
        marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        out.append(pytest.param(scn, id=scn, marks=marks))
    return out


# ---- the harness on every scenario ---------------------------------------------------------------------
@pytest.mark.parametrize("name", engines.BASELINE_ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_pairing_integrity_in_every_request(scenario, name):
    m = metrics(scenario, name)
    assert m["pairing"]["violations"] == 0, m["pairing"]["details"]
    assert m["pairing"]["requests_checked"] == m["requests"] > registry.get_spec(scenario).turns


@pytest.mark.parametrize("name", engines.BASELINE_ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_replay_is_deterministic(scenario, name):
    first, second = runs(scenario, name)
    assert replay.deterministic_part(first) == replay.deterministic_part(second)


@pytest.mark.parametrize("name", engines.BASELINE_ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_metrics_are_complete_and_sane(scenario, name):
    spec = registry.get_spec(scenario)
    sc = spec.build(0, spec.turns)
    m = metrics(scenario, name)
    assert m["turns"] == spec.turns and m["context_length"] == 64_000
    assert runs(scenario, name)[0]["meta"]["scenario"] == scenario
    assert 0 < m["cost_ratio_vs_uncached"] < 1 and m["cost_cache_priced"] < m["cost_uncached"]
    assert m["cached_chars_total"] < m["input_chars_total"]
    assert m["compress_calls"] >= m["compactions"] >= m["compactions_shrinking"]
    assert m["summary_llm_calls"] <= m["compress_calls"]
    assert m["summary_unparsed_prompts"] == 0
    assert m["input_chars_max"] >= m["input_chars_final"] > 0
    gold = m["gold"]
    assert gold["total"] == len(sc.gold) and set(gold["by_category"]) == set(CATEGORIES)
    assert all(gold["by_category"][c]["total"] >= 2 for c in CATEGORIES)
    assert sum(c["total"] for c in gold["by_category"].values()) == gold["total"]
    assert sum(c["retained"] for c in gold["by_category"].values()) == gold["retained"]
    assert m["error_fingerprints"]["total"] == len(replay.error_fingerprints("\n".join(sc.error_lines))) >= 6
    assert m["refetch"]["needed_facts"] == sum(g.needed for g in sc.gold)
    assert set(m["refetch"]["missing_ids"]) <= {g.id for g in sc.gold if g.needed}
    assert m["byte_stability"]["requests_checked"] == m["requests"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_scenario_makes_hermes_as_shipped_compact(scenario):
    """On a 64K window a session shape that never fills the window would measure nothing."""
    m = metrics(scenario, "stock_defaults")
    assert m["compactions"] == m["compactions_shrinking"] == m["summary_llm_calls"] >= MIN_COMPACTIONS[scenario]
    assert m["prune_commits"] == 0, "Hermes as shipped never prunes"
    assert m["gold"]["by_category"]["recall"]["retention"] < 1, "tool-derived facts are what compaction loses"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_equal_gates_twin_runs_the_same_prune_gates_as_stock(scenario):
    for attr in ("proactive_prune_tokens", "proactive_prune_min_result_chars"):
        assert getattr(engines.make_engine("tameru_1_4"), attr) == getattr(engines.make_engine("stock"), attr)
    assert metrics(scenario, "tameru_1_4")["threshold_tokens"] == metrics(scenario, "stock")["threshold_tokens"]


# ---- exact comparison with the saved baselines --------------------------------------------------------------
@pytest.mark.parametrize("name", engines.BASELINE_ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_matches_saved_baseline(scenario, name):
    result = runs(scenario, name)[0]
    if WRITE:
        write_baseline(name, result, scenario)
    path = baseline_path(name, scenario)
    assert path.is_file(), f"{path} missing; run this file once with TAMERU_WRITE_BASELINES=1"
    assert replay.deterministic_part(result) == load_baseline(name, scenario), (
        f"the {name} replay of {scenario!r} differs from {path.relative_to(path.parents[2])} "
        "(Hermes, the engine or the harness changed; if intended, re-record with TAMERU_WRITE_BASELINES=1)"
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_saved_baselines_cover_the_four_engines(scenario):
    for name in engines.BASELINE_ENGINES:
        assert baseline_path(name, scenario).is_file() or WRITE, (scenario, name)


# ---- acceptance: the plugin as shipped against Hermes as shipped -----------------------------------------------
@pytest.mark.parametrize("scenario", scenario_param("cost", "tameru_1_4_product"))
def test_acceptance_shipped_plugin_costs_no_more_than_hermes_as_shipped(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product"), metrics(scenario, "stock_defaults")
    assert ours["cost_cache_priced"] <= theirs["cost_cache_priced"]


@pytest.mark.parametrize(("scenario", "category"), matrix_param("gold", "tameru_1_4_product"))
def test_acceptance_shipped_plugin_keeps_at_least_as_many_gold_facts_in_every_category(scenario, category):
    ours = metrics(scenario, "tameru_1_4_product")["gold"]["by_category"][category]
    theirs = metrics(scenario, "stock_defaults")["gold"]["by_category"][category]
    assert ours["retained"] >= theirs["retained"], (ours["missing"], theirs["missing"])


@pytest.mark.parametrize("scenario", scenario_param("errors", "tameru_1_4_product"))
def test_acceptance_shipped_plugin_keeps_at_least_as_many_error_templates(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4_product"), metrics(scenario, "stock_defaults")
    assert ours["error_fingerprints"]["retained"] >= theirs["error_fingerprints"]["retained"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_acceptance_shipped_plugin_has_no_pairing_violations(scenario):
    assert metrics(scenario, "tameru_1_4_product")["pairing"]["violations"] == 0


@pytest.mark.parametrize("name", ("stock_defaults", "stock", "tameru_1_4", "tameru_1_4_product"))
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_acceptance_no_tool_row_is_rewritten_between_commits(scenario, name):
    stability = metrics(scenario, name)["byte_stability"]
    assert stability["rewrites_outside_commits"] == 0, stability["details"]


# ---- the equal-gates plugin against Hermes with the same prune switched on ------------------------------------------
@pytest.mark.parametrize("scenario", scenario_param("cost", "tameru_1_4"))
def test_equal_gates_plugin_costs_no_more_than_stock(scenario):
    assert metrics(scenario, "tameru_1_4")["cost_cache_priced"] <= metrics(scenario, "stock")["cost_cache_priced"]


@pytest.mark.parametrize(("scenario", "category"), matrix_param("gold", "tameru_1_4"))
def test_equal_gates_plugin_keeps_at_least_as_many_gold_facts_in_every_category(scenario, category):
    ours = metrics(scenario, "tameru_1_4")["gold"]["by_category"][category]
    theirs = metrics(scenario, "stock")["gold"]["by_category"][category]
    assert ours["retained"] >= theirs["retained"], (ours["missing"], theirs["missing"])


@pytest.mark.parametrize("scenario", scenario_param("errors", "tameru_1_4"))
def test_equal_gates_plugin_keeps_at_least_as_many_error_templates(scenario):
    ours, theirs = metrics(scenario, "tameru_1_4"), metrics(scenario, "stock")
    assert ours["error_fingerprints"]["retained"] >= theirs["error_fingerprints"]["retained"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_equal_gates_plugin_has_no_pairing_violations(scenario):
    assert metrics(scenario, "tameru_1_4")["pairing"]["violations"] == 0
