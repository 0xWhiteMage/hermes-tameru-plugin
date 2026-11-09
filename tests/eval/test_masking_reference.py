"""Tameru must keep at least as many gold facts as naive observation masking in every development scenario at 32K.

Plain masking (``engines.plain_masking``) replaces old tool results with one fixed placeholder. It is the reference any
smarter method has to beat on what it keeps; cost is compared in ``test_cost_matrix.py``, not here.
"""
from __future__ import annotations

import os

import pytest

if os.environ.get("HERMES_REPO_ROOT", "").strip():
    import agent.context_compressor  # noqa: F401
else:
    pytest.importorskip("agent.context_compressor")

import engines
import replay

pytestmark = pytest.mark.real_hermes
DEV = ("default", "oncall", "refactor", "smallrows", "i18n")


def _gold(arm: str, scenario: str) -> int:
    m = replay.run_session(lambda: engines.make_engine(arm), scenario=scenario, context_length=32_000)["metrics"]
    return sum(c["retained"] for c in m["gold"]["by_category"].values())


@pytest.mark.parametrize("scenario", DEV)
def test_tameru_keeps_at_least_as_many_gold_facts_as_plain_masking(scenario):
    assert _gold("tameru_1_4_product", scenario) >= _gold("plain_masking", scenario)
