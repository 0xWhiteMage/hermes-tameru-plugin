"""The upstream engine's QA corpus, pushed through the plugin as Hermes tool results.

``tests/fixtures/engine_benchmark_cases.json`` is the corpus behind tameru-compaction-system's
head-to-head benchmark (``benchmarks/jev_comparison.py``, MIT): one document, one question, the gold
strings the answer needs and the distractors that must not survive. The engine alone keeps every gold
string and no distractor there. This test holds the plugin to the same: whatever the ladder keeps of a
result must still answer the question, and must not carry the stale, excluded or injected text.

A result under ``min_tool_chars`` goes to Hermes' own one-line summary (stock behaviour) and is not
judged here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from engine_support import Chat, demote, make_engine

from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.render import parse_header

CASES = json.loads((Path(__file__).parent / "fixtures" / "engine_benchmark_cases.json").read_text("utf-8"))
JUDGED = [c for c in CASES if len(c["ctx"]) >= TameruSettings().min_tool_chars]


def _through_the_ladder(case: dict) -> tuple[str, str]:
    """``(rung, content)`` of the case's document as a terminal result asked about by its question."""
    chat = Chat(task=case["q"])
    idx = chat.tool("terminal", {"command": "cat report.txt"}, case["ctx"])
    _, out = demote(make_engine(), chat.finish(), idx)
    content = out[idx]["content"]
    return (parse_header(content) or {}).get("rung", "none"), content


@pytest.fixture(scope="module")
def results() -> dict[str, tuple[str, str]]:
    return {c["name"]: _through_the_ladder(c) for c in JUDGED}


@pytest.mark.parametrize("case", JUDGED, ids=lambda c: c["name"])
def test_every_gold_string_survives_and_no_distractor_does(case, results):
    rung, content = results[case["name"]]
    missing = [g for g in case["gold"] if g not in content]
    leaked = [f for f in case["forbid"] if f in content]
    assert not missing, f"{case['name']} ({rung}) lost {missing}"
    assert not leaked, f"{case['name']} ({rung}) kept {leaked}"


def test_the_query_aware_extract_is_what_answers_these(results):
    """The brief knows nothing of the question, so it can keep a stale value or drop the answer; the
    corpus is built so that only the engine's own extract gets it right."""
    briefs = [n for n, (rung, _) in results.items() if rung == "brief"]
    assert len(briefs) <= 2, f"too many results fell to the query-blind brief: {briefs}"


def test_the_plugin_keeps_most_of_the_engine_s_savings(results):
    saved = [100 * (1 - len(results[c["name"]][1]) / len(c["ctx"])) for c in JUDGED]
    assert sum(saved) / len(saved) >= 60, f"mean savings {sum(saved) / len(saved):.1f}%"
