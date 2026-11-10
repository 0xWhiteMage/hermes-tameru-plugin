"""The holdout register: one look per product hash, a second needs a reason, and the hash ignores line endings."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("holdout_register", REPO / "tests" / "eval" / "holdout_register.py")
reg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reg)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "plugin"
    (root / "hermes_tameru_plugin").mkdir(parents=True)
    (root / "hermes_tameru_plugin" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "hermes_tameru_plugin" / "b.py").write_text("y = 2\n", encoding="utf-8")
    return root


def test_the_hash_ignores_line_endings_and_pycache_but_not_content(tree):
    first = reg.product_hash(tree)
    (tree / "hermes_tameru_plugin" / "a.py").write_bytes(b"x = 1\r\n")
    (tree / "hermes_tameru_plugin" / "__pycache__").mkdir()
    (tree / "hermes_tameru_plugin" / "__pycache__" / "junk.py").write_text("z", encoding="utf-8")
    assert reg.product_hash(tree) == first
    (tree / "hermes_tameru_plugin" / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert reg.product_hash(tree) != first


def test_a_first_look_is_recorded_and_a_repeat_look_needs_a_reason(tree, tmp_path):
    register = tmp_path / "looks.json"
    first = reg.record_look("3", ["bursty", "dense"], note="first", register=register, root=tree)
    assert first["looks_at_this_hash"] == 1 and first["again"] == ""
    with pytest.raises(ValueError, match="already looked at"):
        reg.record_look("3", ["bursty"], register=register, root=tree)
    second = reg.record_look("3", ["bursty"], again="a holdout cell failed to load", register=register, root=tree)
    assert second["looks_at_this_hash"] == 2 and second["again"] == "a holdout cell failed to load"
    assert len(json.loads(register.read_text(encoding="utf-8"))) == 2


def test_a_changed_product_is_a_new_look_by_construction(tree, tmp_path):
    register = tmp_path / "looks.json"
    reg.record_look("3", ["bursty"], register=register, root=tree)
    (tree / "hermes_tameru_plugin" / "b.py").write_text("y = 3\n", encoding="utf-8")
    assert reg.record_look("3", ["bursty"], register=register, root=tree)["looks_at_this_hash"] == 1


@pytest.mark.parametrize("phase,scenarios", [("", ["bursty"]), ("3", []), ("3", ["oncall"]), ("3", ["bursty", "nope"])])
def test_bad_input_is_refused(tree, tmp_path, phase, scenarios):
    with pytest.raises(ValueError):
        reg.record_look(phase, scenarios, register=tmp_path / "looks.json", root=tree)


def test_the_committed_register_is_valid_and_every_entry_has_its_fields():
    entries = reg.looks()
    for entry in entries:
        assert {"product", "date", "phase", "scenarios", "looks_at_this_hash"} <= set(entry)
        assert len(entry["product"]) == 64 and set(entry["scenarios"]) <= set(reg.HOLDOUTS)
        if entry["looks_at_this_hash"] > 1:
            assert entry["again"], "a repeat look must carry its reason"
