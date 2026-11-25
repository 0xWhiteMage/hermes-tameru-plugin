"""Hermes accepts or declines an engine result only through the engine's ``decide()`` under ``HERMES_POLICY``.

The plugin keeps its ladder and its fixed-budget retry loop, but the thresholds (risk ceiling, error fingerprints that must survive,
confidence, savings) belong to the engine's policy data. This test reads ``engine.py`` and fails if a threshold of that kind is
written there outside the policy values handed to ``decide()``.
"""
from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "hermes_tameru_plugin" / "engine.py"
# The brief rung's own check that its rendered body kept every error template of the original: ``brief_context`` returns
# text, not a ``CompressResult``, so ``decide`` does not apply (and the engine owns no policy for it).
BRIEF_CHECK = "if fingerprints.keys() <= error_fingerprints(body, status_rows=False).keys():"
THRESHOLDS = re.compile(
    r"confidence|_RISK_ORDER|min_saved|min_tokens_saved|compression_risk\s*[<>=]|\brisk\s*[<>]|\.risk\b.*[<>]"
    r"|fingerprints.*\.keys\(\)\s*<=|\.keys\(\)\s*<=.*fingerprints",
)


def _code_lines():
    for number, line in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield number, stripped


def test_engine_py_has_no_acceptance_threshold_of_its_own():
    found = [f"{n}: {line}" for n, line in _code_lines() if THRESHOLDS.search(line) and line != BRIEF_CHECK]
    assert found == []


def test_the_acceptance_goes_through_decide_with_the_hermes_policy():
    text = SOURCE.read_text(encoding="utf-8")
    assert "from .tameru.decide import HERMES_POLICY, decide" in text
    body = text[text.index("def _acceptable("):text.index("def _body_row(")]
    assert "decide(out, dataclass_replace(HERMES_POLICY, max_risk=ceiling), fingerprints=fingerprints)" in body
    # the extract row and its fixed-budget retries accept only through ``_acceptable``;
    # ``out.fail_open`` may only gate whether the retries run, never acceptance
    row = text[text.index("def _extract_row("):text.index("def _brief_row(")]
    assert row.count("self._acceptable(") == 2
    assert row.count("out.fail_open") == 1 and "not out.fail_open" in row


def test_the_brief_exception_is_the_only_fingerprint_comparison():
    assert sum(1 for _, line in _code_lines() if line == BRIEF_CHECK) == 1
