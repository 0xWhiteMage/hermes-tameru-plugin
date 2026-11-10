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
