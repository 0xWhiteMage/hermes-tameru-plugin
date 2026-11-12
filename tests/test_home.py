"""``home.hermes_home``: Hermes' own answer when Hermes is importable, the environment otherwise."""
from __future__ import annotations

import sys
import types
from pathlib import Path

from hermes_tameru_plugin.home import hermes_home


def test_hermes_own_helper_wins_when_it_is_importable(monkeypatch, tmp_path):
    fake = types.ModuleType("hermes_constants")
    fake.get_hermes_home = lambda: tmp_path / "profile-home"
    monkeypatch.setitem(sys.modules, "hermes_constants", fake)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "ignored"))
    assert hermes_home() == tmp_path / "profile-home"


def test_without_hermes_the_environment_then_the_default(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)           # makes the import fail
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "from-env"))
    assert hermes_home() == tmp_path / "from-env"
    monkeypatch.setenv("HERMES_HOME", "")
    assert hermes_home() == Path("~/.hermes").expanduser()


def test_a_broken_hermes_helper_falls_back_instead_of_raising(monkeypatch, tmp_path):
    fake = types.ModuleType("hermes_constants")

    def boom():
        raise RuntimeError("PRIVATE_SENTINEL")

    fake.get_hermes_home = boom
    monkeypatch.setitem(sys.modules, "hermes_constants", fake)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "from-env"))
    assert hermes_home() == tmp_path / "from-env"
