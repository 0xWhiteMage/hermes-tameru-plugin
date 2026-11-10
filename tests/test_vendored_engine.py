"""The vendored engine is the engine repository's source at ``VENDORED_FROM`` (``scripts/check_vendored.py``).

The real comparison runs when ``TAMERU_ENGINE_REPO`` names a clone of the engine repository (CI sets it). The negative cases
use a throwaway git repository, so the check is proven able to fail without touching either real repository.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_vendored", REPO / "scripts" / "check_vendored.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _fake_engine(tmp_path: Path, mutate=None) -> tuple[Path, str]:
    """A git repository holding the plugin's vendored modules as ``src/tameru`` (optionally altered)."""
    repo = tmp_path / "engine"
    (repo / "src" / "tameru").mkdir(parents=True)
    for source in check.PLUGIN.glob("*.py"):
        shutil.copy(source, repo / "src" / "tameru" / source.name)
    if mutate:
        mutate(repo / "src" / "tameru")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q"], ["add", "-A"], ["-c", "core.autocrlf=false", "commit", "-q", "-m", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return repo, commit


def test_the_real_engine_repository_matches_the_recorded_commit():
    where = os.environ.get("TAMERU_ENGINE_REPO", "").strip()
    if not where:
        pytest.skip("set TAMERU_ENGINE_REPO to a clone of the engine repository")
    assert check.compare(Path(where), check.vendored_from()) == []


def test_identical_trees_agree_even_with_different_line_endings(tmp_path):
    def crlf(folder: Path):
        for path in folder.glob("*.py"):
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    repo, commit = _fake_engine(tmp_path, crlf)
    assert check.compare(repo, commit) == []


def test_a_changed_module_is_reported(tmp_path):
    def edit(folder: Path):
        (folder / "regex_cache.py").write_text((folder / "regex_cache.py").read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    repo, commit = _fake_engine(tmp_path, edit)
    assert check.compare(repo, commit) == ["differs from the engine at " + commit[:12] + ": regex_cache.py"]


def test_a_missing_and_an_extra_module_are_reported(tmp_path):
    def reshape(folder: Path):
        (folder / "regex_cache.py").unlink()
        (folder / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
    repo, commit = _fake_engine(tmp_path, reshape)
    problems = check.compare(repo, commit)
    assert any("missing from the plugin: brand_new.py" in p for p in problems)
    assert any("not in the engine" in p and "regex_cache.py" in p for p in problems)


def test_an_unknown_commit_is_a_readable_error(tmp_path):
    repo, _commit = _fake_engine(tmp_path)
    assert "is not in" in check.compare(repo, "0" * 40)[0]
