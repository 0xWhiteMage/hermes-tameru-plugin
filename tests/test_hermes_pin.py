"""One Hermes commit for everything: the HERMES_PIN file, CI, and the commit the saved replay baselines were recorded on."""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIN = (REPO / "HERMES_PIN").read_text(encoding="utf-8").strip()


def test_the_pin_is_a_full_commit_hash():
    assert re.fullmatch(r"[0-9a-f]{40}", PIN), PIN


def test_every_saved_baseline_was_recorded_on_the_pinned_hermes():
    # Only the current protocol's folder is compared by the suite; the older files beside it are not read any more.
    baselines = sorted((REPO / "tests" / "eval" / "baselines" / "protocol-2").rglob("*.json"))
    assert baselines, "no saved baselines found"
    other = {str(p.relative_to(REPO)): json.loads(p.read_text(encoding="utf-8"))["meta"].get("hermes_pin")
             for p in baselines}
    assert {k: v for k, v in other.items() if v != PIN} == {}, "re-record these baselines on HERMES_PIN, or move the pin"


def test_ci_reads_the_pin_file_and_carries_no_hermes_hash_of_its_own():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "HERMES_PIN" in ci and "steps.pin.outputs.sha" in ci
    assert "HERMES_PINNED_SHA" not in ci


def test_message_budget_tokens_rejects_malformed_rows():
    import pytest
    from hermes_tameru_plugin.hermes_compat import message_budget_tokens
    for bad in (None, "x", [None], [{"role": "user"}, "str-row"]):
        with pytest.raises(TypeError, match="must be"):
            message_budget_tokens(bad)
    assert message_budget_tokens([{"role": "user", "content": "hi"}]) > 0


def test_index_and_display_reject_wrong_types():
    import pytest
    from hermes_tameru_plugin.supersede import build_index
    from hermes_tameru_plugin.activity import display_text
    with pytest.raises(TypeError, match="sequence of dicts"):
        build_index(None)
    with pytest.raises(TypeError, match="sequence of dicts"):
        build_index("x")
    with pytest.raises(TypeError, match="value must be str"):
        display_text(None)
    assert display_text("a\x07b") == "a\\u0007b"


def test_ledger_seam_rejects_wrong_types():
    import pytest
    from hermes_tameru_plugin.ledger import build_ledger, merge_into_summary
    with pytest.raises(TypeError, match="turns must be"):
        build_ledger(None)
    with pytest.raises(TypeError, match="max_chars must be int"):
        build_ledger([], max_chars="x")
    with pytest.raises(TypeError, match="previous must be str"):
        build_ledger([], previous=5)
    with pytest.raises(TypeError, match="summary must be str"):
        merge_into_summary(5, "x")
    with pytest.raises(TypeError, match="ledger must be str"):
        merge_into_summary("x", 5)


def test_build_query_max_chars_rejects_non_int():
    import pytest
    from hermes_tameru_plugin.query import build_query
    m = [{"role": "user", "content": "x"}]
    for bad in (None, "x", 1.5, True, -1):
        with pytest.raises(TypeError, match="max_chars"):
            build_query(m, 0, max_chars=bad)
    assert build_query(m, 0, max_chars=0) == [] or build_query(m, 0)
