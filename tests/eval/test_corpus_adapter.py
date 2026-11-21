"""The round-2 corpus adapter (``corpus_adapter``): mapping counts, the fact check, determinism and the holdout refusal.

Stub-safe: a small synthetic export in the corpus's schema. When a local dev export exists (``build/session_corpus/dev/full``,
built with the engine's ``python -m benchmarks.session_corpus.build --split dev --out ...``), every dev scenario is also mapped and
checked against the corpus's own gold.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import corpus_adapter as ca

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / "build" / "session_corpus"


def _step(i, text, facts=(), kind=None, tool="bash", args=None):
    return {"index": i, "tool": tool, "args": args or {"command": f"run step {i % 3}"}, "kind": kind or ("fact" if facts else "filler"),
            "chars": len(text), "planned_chars": len(text), "output_text": text, "facts": list(facts)}


def _raw(family="pytest", language="en"):
    steps = []
    for i in range(14):
        lines = [f"filler line {j} of step {i}" for j in range(5)]
        facts = []
        if i == 2:
            lines[3] = 'msg="retry" cfg.retries=7'
            facts = [{"id": "f00", "kind": "natural", "key": "cfg.retries", "value": "7", "line_index": 3}]
        if i == 4:
            lines[1] = "override cache.ttl -> 11"
            facts = [{"id": "f01", "kind": "stale", "key": "cache.ttl", "value": "11", "line_index": 1, "superseded": True}]
        if i == 9:
            lines[2] = "override cache.ttl -> 42"
            facts = [{"id": "f02", "kind": "buried", "key": "cache.ttl", "value": "42", "line_index": 2}]
        if i == 12:
            lines[0] = "    def handler(request):  # the real line"
            facts = [{"id": "r001.0", "kind": "real", "key": "Find the handler", "value": "    def handler(request):  # the real line",
                      "line_index": 0}]
        tool, args = ("read_file", {"path": f"src/m{i}.py"}) if i == 12 else ("bash", None)
        steps.append(_step(i, "\n".join(lines), facts, tool=tool, args=args))
    return {
        "schema": 1, "generator": ca.GENERATOR, "id": f"{family}-00", "family": family, "index": 0, "seed": 1234,
        "language": language, "task_style": "specific", "task": "Run the tests and report cfg.retries and cache.ttl.",
        "parameters": {}, "windows": {"128000": {"steps": 10, "chars": 1}, "252000": {"steps": 14, "chars": 1}},
        "questions": [
            {"id": "q-f00", "text": "What is cfg.retries?", "fact_ids": ["f00"], "stratum": "single", "fact_kind": "natural", "first_window": 128000},
            {"id": "q-f02", "text": "Final cache.ttl?", "fact_ids": ["f02"], "stratum": "superseded", "fact_kind": "buried", "first_window": 128000},
            {"id": "q-agg", "text": "Both?", "fact_ids": ["f00", "f02"], "stratum": "aggregate", "fact_kind": "pair", "first_window": 128000},
            {"id": "q-absent", "text": "What is cfg.nothing?", "fact_ids": [], "stratum": "absent", "fact_kind": "none", "first_window": 128000,
             "expected": "not present"},
            {"id": "q-r", "text": "Find the handler", "fact_ids": ["r001.0"], "stratum": "real", "fact_kind": "real", "first_window": 252000},
        ],
        "steps": steps,
    }


def _transcript(m) -> str:
    """Every tool result of the mapped session, untouched (what an arm that never shortens or compacts shows)."""
    return "\n".join(s.result for t in m.scenario.turns for r in t.rounds for s in r.steps)


def test_mapping_counts_are_exact():
    raw = _raw()
    for window, steps, qs, facts in ((128000, 10, 4, 2), (252000, 14, 5, 3)):
        m = ca.map_scenario(raw, window)
        assert m.steps == steps == sum(len(r.steps) for t in m.scenario.turns for r in t.rounds)
        assert len(m.scenario.turns) == math.ceil(steps / ca.STEPS_PER_TURN)
        assert all(len(r.steps) == 1 for t in m.scenario.turns for r in t.rounds)
        assert [len(t.rounds) for t in m.scenario.turns][:-1] == [ca.STEPS_PER_TURN] * (len(m.scenario.turns) - 1)
        assert len(m.questions) == qs and len(m.facts) == len(m.scenario.gold) == facts
        assert m.scenario.turns[0].user == raw["task"] and all(t.user == "continue" for t in m.scenario.turns[1:])
        assert m.scenario.final_question.count("\n") == qs - 1
    zh = ca.map_scenario(_raw(language="mixed"), 128000)
    assert all(t.user == "继续" for t in zh.scenario.turns[1:])


def test_envelopes_and_call_keys():
    m = ca.map_scenario(_raw(), 252000)
    steps = [s for t in m.scenario.turns for r in t.rounds for s in r.steps]
    assert steps[0].name == "terminal" and json.loads(steps[0].result)["exit_code"] == 0
    assert steps[12].name == "read_file" and json.loads(steps[12].result)["content"].startswith("1|    def handler")
    # the same command again gets ``#<step index>``; the first use has no suffix
    assert steps[0].key == "terminal:run step 0" and steps[3].key == "terminal:run step 0#3"
    assert len(set(s.key for s in steps)) == len(steps)
    for tool, args, want in (("kubectl_logs", {"namespace": "data", "pod": "p-1", "tail": "5000"}, "kubectl logs -n data p-1 --tail=5000"),
                             ("sql_query", {"query": "SELECT 1"}, 'psql -c "SELECT 1"'),
                             ("ansible_playbook", {"limit": "gw", "playbook": "site.yml"}, "ansible-playbook site.yml --limit gw"),
                             ("gh_run_view", {"log": "true", "run": "201"}, "gh run view 201 --log")):
        name, a, result = ca.hermes_call(_step(0, "x\ny", kind="error", tool=tool, args=args))
        assert (name, a["command"], json.loads(result)["exit_code"]) == ("terminal", want, 1)


def test_fact_check_agrees_with_the_corpus_gold():
    raw = _raw()
    m = ca.map_scenario(raw, 252000)
    text = _transcript(m)
    by_id = {f["id"]: (s, f) for s in raw["steps"] for f in s["facts"]}
    for fact, gold in zip(m.facts, m.scenario.gold):
        s, f = by_id[fact.id]
        assert fact.line == s["output_text"].split("\n")[f["line_index"]]
        assert gold.needle == ca.escaped(fact.line) and gold.needle in m.step_results[fact.step]
        assert fact.turn == fact.step // ca.STEPS_PER_TURN + 1 and gold.call_key == m.step_keys[fact.step]
        assert ca.fact_held(fact, text), fact.id                          # untouched session: every fact held
        assert not ca.fact_held(fact, text.replace(gold.needle, "")), fact.id
    f00 = m.facts[0]
    assert ca.escaped(f00.line) != f00.line                               # the JSON-escaped form is what the envelope shows
    assert ca.fact_held(f00, 'an extract: msg="retry" cfg.retries=7')     # raw, as an engine extract shows it
    assert ca.fact_held(f00, "kept: cfg.retries=7   (msg retry)")        # one line holds key and value
    assert not ca.fact_held(f00, "cfg.retries\n= 7")                     # not across lines
    newest = next(f for f in m.facts if f.id == "f02")
    assert not ca.fact_held(newest, "override cache.ttl -> 11")          # the stale line alone does not hold the superseded fact
    real = next(f for f in m.facts if f.id == "r001.0")
    assert ca.fact_held(real, "12|  def   handler(request):  # the real line")
    held = {"f00": True, "f02": False, "r001.0": True}
    assert [ca.question_held(q, held) for q in m.questions] == [True, False, False, None, True]


def test_mapping_is_deterministic():
    a, b = ca.map_scenario(_raw(), 252000), ca.map_scenario(json.loads(json.dumps(_raw())), 252000)
    assert a.scenario == b.scenario and a.facts == b.facts and a.step_results == b.step_results


def test_loader_refuses_the_holdout(tmp_path):
    with pytest.raises(ca.HoldoutRefused):
        ca.split_dir(tmp_path, "holdout")
    with pytest.raises(ca.HoldoutRefused):
        ca.map_scenario(_raw(family="terraform"), 128000)
    d = tmp_path / "holdout" / "full"
    d.mkdir(parents=True)
    (d / "x.json").write_text(json.dumps(_raw()), encoding="utf-8")
    with pytest.raises(ca.HoldoutRefused):
        ca.load_raw(d / "x.json")
    ok = tmp_path / "dev" / "full"
    ok.mkdir(parents=True)
    (ok / "index.json").write_text(json.dumps([{"id": "terraform-00", "family": "terraform"}]), encoding="utf-8")
    with pytest.raises(ca.HoldoutRefused):
        ca.load_index(tmp_path, "dev")
    assert {"terraform", "tsc", "docker"} <= ca.HOLDOUT_FAMILIES and not ({"pytest", "gcc", "ansible"} & ca.HOLDOUT_FAMILIES)


def test_stratified_subsample_is_fixed():
    index = [{"id": f"{fam}-{i:02d}", "family": fam} for fam in ("a", "b", "c") for i in range(11)]
    one = ca.stratified_subsample(index, 4, "seed-x")
    assert one == ca.stratified_subsample(list(reversed(index)), 4, "seed-x")
    assert [sum(i.startswith(f) for i in one) for f in "abc"] == [4, 4, 4]


@pytest.mark.skipif(not (LOCAL / "dev" / "full" / "index.json").is_file(), reason="no local dev export")
def test_local_dev_export_maps_against_its_gold():
    index = ca.load_index(LOCAL, "dev")
    assert len(index) == 132
    for row in index[::7]:
        raw = ca.load_scenario(LOCAL, "dev", row["id"])
        for window in (128000, 1000000):
            m = ca.map_scenario(raw, window)
            assert m.steps == raw["windows"][str(window)]["steps"]
            want = {f for q in raw["questions"] if q["first_window"] <= window for f in q["fact_ids"]}
            assert {f.id for f in m.facts} == want
            for fact, gold in zip(m.facts, m.scenario.gold):
                assert gold.needle in m.step_results[fact.step]
                if fact.kind != "real":
                    assert fact.key in fact.line and fact.value in fact.line
                assert ca.fact_held(fact, m.step_results[fact.step])


def test_scoring_grep_matches_the_recovery_child():
    """corpus_run scores read-backs with the recovery tool's grep in-process; it must answer exactly as the timed child does."""
    import re
    import subprocess
    import sys as _sys

    import corpus_run
    from hermes_tameru_plugin import recovery

    texts = ["Alpha ok", "beta FAILED here", "gamma\nDELTA 7", "nothing", "版本 v3.27.15"]
    pattern = re.compile("failed|delta|3\.27", re.IGNORECASE)
    payload = json.dumps({"p": pattern.pattern, "t": texts}).encode("ascii")
    child = json.loads(subprocess.run([_sys.executable, "-I", "-S", "-c", recovery._GREP_CHILD], input=payload,
                                      capture_output=True, check=True).stdout)
    original = recovery._grep_hits
    corpus_run._scoring_grep(True)
    try:
        assert recovery._grep_hits(pattern, texts, 0.001) == (child, "")
    finally:
        corpus_run._scoring_grep(False)
    assert recovery._grep_hits is original


def test_memo_answers_identical_calls_once():
    import corpus_run

    class Engine:
        calls = 0

        def get_tool_schemas(self):
            return []

        def handle_tool_call(self, name, args):
            Engine.calls += 1
            return f"{name}:{args['ref']}"

    m = corpus_run._Memo(Engine())
    assert [m.handle_tool_call("t", {"ref": r}) for r in "aab"] == ["t:a", "t:a", "t:b"] and Engine.calls == 2


def test_row_level_names_the_rung_a_row_was_cut_to():
    """corpus_run's row_rung field: the rung of a rendered row, else 'hermes' for a changed row with no Tameru header."""
    import corpus_run
    from hermes_tameru_plugin.render import RenderStats, header_only, render

    stats = RenderStats(kept_lines=3, total_lines=12, kept_chars=120, total_chars=4800,
                        omitted=(("stdout", 9),), rung="extract")
    extract_row = render("[terminal] ran `pytest -x` -> exit 1, 12 lines output", stats, "0123456789abcdef",
                         body="FAILED tests/test_x.py::test_a - assert 1 == 2")
    assert corpus_run.row_level(extract_row) == "extract"

    header_stats = RenderStats(kept_lines=0, total_lines=12, kept_chars=0, total_chars=4800,
                               omitted=(("all", 12),), rung="header")
    assert corpus_run.row_level(header_only(extract_row, header_stats, "0123456789abcdef")) == "header"

    assert corpus_run.row_level("[terminal] ran `pytest -x` -> exit 1, 12 lines output (earlier output trimmed)") == "hermes"
