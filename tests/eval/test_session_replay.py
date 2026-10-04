"""Phase 0 baselines: replay the scripted 30-turn session against stock Hermes and Tameru 1.3.0.

Needs real Hermes (``HERMES_REPO_ROOT``); skipped otherwise. Regenerate the baselines with::

    TAMERU_WRITE_BASELINES=1 HERMES_REPO_ROOT=<hermes checkout> python -m pytest -q tests/eval

The stock baseline is compared exactly: the replay is deterministic, so any difference means Hermes (or the
harness) changed. The ``tameru_1_3`` baseline freezes the 1.3.0 plugin; once the vendored engine moves to
1.4 it is no longer compared (``tameru_1_4`` gets its own baseline then).
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

if os.environ.get("HERMES_REPO_ROOT", "").strip():
    import agent.context_compressor  # noqa: F401  (a broken checkout must fail the run, not skip it)
else:
    pytest.importorskip("agent.context_compressor")

import engines
import replay
from fixtures import hermes_payloads as hp
from scenario import CATEGORIES, N_TURNS, build_scenario

pytestmark = pytest.mark.real_hermes

BASELINES = Path(__file__).resolve().parent / "baselines"
WRITE = os.environ.get("TAMERU_WRITE_BASELINES") == "1"
_CACHE: dict[str, list[dict]] = {}


def runs(name: str) -> list[dict]:
    """Two independent runs of the engine (cached for the module): the second proves determinism."""
    if name not in _CACHE:
        _CACHE[name] = [replay.run_session(lambda: engines.make_engine(name)) for _ in range(2)]
    return _CACHE[name]


def baseline_path(name: str) -> Path:
    return BASELINES / f"{name}.json"


@pytest.mark.parametrize("name", engines.BASELINE_ENGINES)
def test_pairing_integrity_in_every_request(name):
    m = runs(name)[0]["metrics"]
    assert m["pairing"]["violations"] == 0, m["pairing"]["details"]
    assert m["pairing"]["requests_checked"] == m["requests"] > N_TURNS


@pytest.mark.parametrize("name", engines.BASELINE_ENGINES)
def test_replay_is_deterministic(name):
    first, second = runs(name)
    assert replay.deterministic_part(first) == replay.deterministic_part(second)


@pytest.mark.parametrize("name", engines.BASELINE_ENGINES)
def test_metrics_are_complete_and_sane(name):
    m = runs(name)[0]["metrics"]
    assert m["turns"] == N_TURNS and m["context_length"] == 64_000
    assert 0 < m["cost_ratio_vs_uncached"] < 1  # a prefix cache always saves something in a growing transcript
    assert m["cost_cache_priced"] < m["cost_uncached"]
    assert m["cached_chars_total"] < m["input_chars_total"]
    assert m["compress_calls"] >= m["compactions"] >= m["compactions_shrinking"]
    assert m["summary_llm_calls"] <= m["compress_calls"]  # every summary call was made on behalf of a compress()
    assert m["summary_unparsed_prompts"] == 0  # Hermes' summary prompt still has the record format the fake reads
    assert m["input_chars_max"] >= m["input_chars_final"] > 0
    gold = m["gold"]
    assert gold["total"] == 17 and set(gold["by_category"]) == set(CATEGORIES)
    assert sum(c["total"] for c in gold["by_category"].values()) == gold["total"]
    assert sum(c["retained"] for c in gold["by_category"].values()) == gold["retained"]
    assert m["error_fingerprints"]["total"] == len(hp.ERROR_TEMPLATES)
    assert m["refetch"]["needed_facts"] == 11
    assert set(m["refetch"]["missing_ids"]) <= {g.id for g in build_scenario(0).gold if g.needed}
    assert runs(name)[0]["timing"]["engine_seconds"] >= 0


def test_the_session_exercises_both_stock_mechanisms():
    """The scenario must be big enough that stock Hermes prunes AND compacts (otherwise it measures nothing)."""
    m = runs("stock")[0]["metrics"]
    assert m["summary_llm_calls"] == m["compactions"] == m["compactions_shrinking"]  # stock: one summary per compaction
    assert m["prune_commits"] >= 1 and m["prune_rows_demoted"] >= 1
    assert m["compactions"] >= 1
    assert m["prefix_break_events"] >= m["prune_commits"] + m["compactions"]
    assert m["gold"]["by_category"]["recall"]["retention"] < 1  # tool-derived facts are what compaction loses


def test_stock_matches_saved_baseline():
    got = replay.deterministic_part(runs("stock")[0])
    path = baseline_path("stock")
    if WRITE:
        write_baseline("stock", runs("stock")[0])
    assert path.is_file(), f"{path} missing; run once with TAMERU_WRITE_BASELINES=1"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert got == {"metrics": saved["metrics"], "series": saved["series"]}, (
        "stock Hermes replay differs from tests/eval/baselines/stock.json "
        "(Hermes changed or the harness did; if intended, re-run with TAMERU_WRITE_BASELINES=1)"
    )


def test_tameru_1_3_matches_saved_baseline():
    version = engines.plugin_engine_version()
    if not version.startswith("1.3"):
        pytest.skip(f"vendored engine is {version}; the tameru_1_3 baseline is frozen history")
    path = baseline_path("tameru_1_3")
    if WRITE:
        write_baseline("tameru_1_3", runs("tameru_1_3")[0])
    assert path.is_file(), f"{path} missing; run once with TAMERU_WRITE_BASELINES=1"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert replay.deterministic_part(runs("tameru_1_3")[0]) == {"metrics": saved["metrics"], "series": saved["series"]}


def write_baseline(name: str, result: dict) -> None:
    BASELINES.mkdir(exist_ok=True)
    doc = {
        "_note": "Generated by tests/eval/test_session_replay.py with TAMERU_WRITE_BASELINES=1. "
        "metrics/series are deterministic and compared exactly; timing is informational.",
        "meta": result["meta"],
        "metrics": result["metrics"],
        "series": result["series"],
        "timing": result["timing"],
    }
    baseline_path(name).write_text(json.dumps(doc, indent=1, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")


# ---- harness <-> Hermes contracts -------------------------------------------------------------
def test_fake_summarizer_patches_the_name_hermes_calls():
    """If Hermes renames the summary-LLM seam the fake would silently stop intercepting (and hit the network)."""
    import agent.context_compressor as cc

    assert callable(cc.call_llm)
    src = inspect.getsource(cc.ContextCompressor._call_summary_llm)
    assert "call_llm(**call_kwargs)" in src and '"task": "compression"' in src


def test_fake_summarizer_reads_hermes_first_and_iterative_prompts():
    """The 30-turn replay compacts stock Hermes once, so its iterative-update prompt (PREVIOUS SUMMARY ...) is never
    exercised there. Drive two real compactions by hand and check the fake reads both prompt shapes."""
    seen: list[tuple[list[str], list[str]]] = []

    class Spy(replay.FakeSummarizer):
        def __call__(self, **kwargs):
            records, prev_actions, _task = self._parse(kwargs["messages"][0]["content"])
            seen.append((records, prev_actions))
            return super().__call__(**kwargs)

    def rows(start, n):
        out = []
        for i in range(start, start + n):
            out.append({"role": "user", "content": f"question {i}: " + "q" * 600})
            out.append({"role": "assistant", "content": f"answer {i}: " + "a" * 600})
        return out

    eng = engines.make_engine("stock")
    replay.prepare_engine(eng)
    spy = Spy()
    with replay.patched_summary_llm(spy):
        first_in = [{"role": "system", "content": "sys"}] + rows(0, 25)
        first_out = eng.compress(first_in, current_tokens=60_000, force=True)
        assert first_out is not first_in and len(first_out) < len(first_in)
        eng.compress(first_out + rows(25, 25), current_tokens=60_000, force=True)
    assert spy.calls == 2 and spy.unparsed == 0
    (records1, carried1), (records2, carried2) = seen
    assert len(records1) > 20 and carried1 == []  # first compaction: fresh prompt, many turn records
    assert records2 and len(carried2) > 20  # second: the previous summary's numbered actions are read back


def test_persisted_output_fixture_matches_hermes_builder():
    from tools.tool_result_storage import _build_persisted_message

    preview = hp.journal_preview(__import__("random").Random(3))
    ours = hp.persisted_output(preview, 187_402, "/home/agent/.hermes/cache/spillover/call_1.txt")
    assert ours == _build_persisted_message(preview, True, 187_402, "/home/agent/.hermes/cache/spillover/call_1.txt")


def test_hermes_loop_contract_prune_noop_returns_the_input_object():
    """The replay only commits a prune when a NEW list with n > 0 comes back; stock must follow that contract."""
    eng = engines.make_engine("stock")
    replay.prepare_engine(eng)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    out, n = eng.prune_tool_results_only(msgs, current_tokens=10)
    assert out is msgs and n == 0


def test_error_fingerprints_agree_with_the_engine_helper_when_present():
    from hermes_tameru_plugin.tameru import compress_context as cc

    helper = getattr(cc, "error_fingerprints", None)
    if helper is None:
        pytest.skip("vendored engine predates error_fingerprints (1.4.0)")
    for seed in (0, 1, 2):
        sc = build_scenario(seed)
        texts = ["\n".join(sc.error_lines)] + [
            replay.visible_text([{"role": "tool", "content": s.result}]) for t in sc.turns for r in t.rounds for s in r.steps
        ]
        for text in texts:
            assert list(helper(text)) == list(replay.error_fingerprints(text))
    for edge in ("ERROR a\r\nERROR b", "x\x0cERROR y\n", "ERROR\u2028ERROR z", "ERROR " + "z" * 5_000, "ok " * 800 + "ERROR late"):
        assert dict(helper(edge)) == dict(replay.error_fingerprints(edge))


def test_shorter_session_still_runs_and_pairs():
    r = replay.run_session(lambda: engines.make_engine("stock"), turns=12)
    assert r["metrics"]["turns"] == 12 and r["metrics"]["pairing"]["violations"] == 0
    assert r["metrics"]["gold"]["total"] < 17


def test_prune_only_runs_after_tool_results_like_hermes():
    """Hermes prunes in ``compress_after_tool_results`` only: the turn-start preflight is compress-only."""
    from agent.context_compressor import ContextCompressor

    tails: list[str] = []

    class Recording(ContextCompressor):
        def prune_tool_results_only(self, messages, current_tokens=None):
            tails.append(messages[-1]["role"])
            return super().prune_tool_results_only(messages, current_tokens)

    r = replay.run_session(
        lambda: Recording(model="test-model", proactive_prune_tokens=48_000, quiet_mode=True), turns=12
    )
    m = r["metrics"]
    assert tails and set(tails) == {"tool"}
    assert m["prune_calls"] == len(tails) < m["requests"]  # turn-start requests never prune


def test_optional_engine_hooks_are_driven_like_hermes():
    """``select_context`` replaces the request only; ``on_turn_complete`` fires once per answered user turn."""
    from agent.context_compressor import ContextCompressor

    seen = {"select": 0, "turns": 0, "usage": []}

    class Hooked(ContextCompressor):
        def select_context(self, request_messages, *, conversation_messages=None, incoming_message=None, budget_tokens=0):
            seen["select"] += 1
            assert conversation_messages is not None and budget_tokens == 64_000
            return [request_messages[0], request_messages[-1]]  # system + newest row

        def on_turn_complete(self, messages, usage=None, **kwargs):
            seen["turns"] += 1
            seen["usage"].append(usage)

    r = replay.run_session(
        lambda: Hooked(model="test-model", proactive_prune_tokens=48_000, quiet_mode=True),
        turns=6, keep_final_request=True,
    )
    assert seen["turns"] == 5 and seen["select"] == r["metrics"]["requests"]
    assert all(u and u["prompt_tokens"] > 0 for u in seen["usage"])  # the last response's usage rides along
    assert [m["role"] for m in r["final_request"]] == ["system", "user"]  # what was sent, not what is persisted
    # the cost model prices the selected request (system + one row), never the persisted transcript
    assert r["metrics"]["input_chars_max"] < 3_500 + 44_000
