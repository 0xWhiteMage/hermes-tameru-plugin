"""The rollback scripts restore a tag or any commit into a Hermes folder, plan first, backups outside the scanned folders.

Every run here uses a temporary fake Hermes folder; the scripts are never pointed at a real install.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not (REPO / ".git").exists() or shutil.which("git") is None, reason="needs the git checkout")


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / "hermes"
    (home / "plugins" / "tameru").mkdir(parents=True)
    (home / "plugins" / "tameru" / "marker.txt").write_text("installed before the rollback", encoding="utf-8")
    (home / "desktop-plugins" / "tameru").mkdir(parents=True)
    (home / "desktop-plugins" / "tameru" / "plugin.js").write_text("// old panel", encoding="utf-8")
    return home


def _run(kind: str, home: Path, *args: str) -> subprocess.CompletedProcess:
    if kind == "bash":
        command = [BASH, str(REPO / "scripts" / "rollback.sh"), "--hermes-home", str(home)]
        command += [{"-Ref": "--ref", "-Tag": "--tag", "-Apply": "--apply"}.get(a, a) for a in args]
    else:
        command = [PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                   str(REPO / "scripts" / "rollback.ps1"), "-HermesHome", str(home), *args]
    return subprocess.run(command, capture_output=True, text=True, timeout=180)


KINDS = [pytest.param("bash", marks=pytest.mark.skipif(BASH is None, reason="no bash")),
         pytest.param("powershell", marks=pytest.mark.skipif(PWSH is None, reason="no PowerShell"))]


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("kind", KINDS)
def test_without_apply_it_prints_the_plan_and_changes_nothing(kind, fake_home):
    before = _tree(fake_home)
    head = _git("rev-parse", "HEAD")
    done = _run(kind, fake_home, "-Ref", head)
    assert done.returncode == 0, done.stderr
    assert head[:12] in done.stdout and "Nothing was changed" in done.stdout
    assert "plugin-backups" in done.stdout                      # both backups go outside the folders Hermes scans
    assert _tree(fake_home) == before


@pytest.mark.parametrize("kind", KINDS)
def test_a_ref_that_is_not_a_commit_is_refused_before_anything_changes(kind, fake_home):
    before = _tree(fake_home)
    done = _run(kind, fake_home, "-Ref", "no-such-commit-anywhere", "-Apply")
    assert done.returncode != 0 and _tree(fake_home) == before


@pytest.mark.parametrize("kind", KINDS)
def test_apply_restores_the_commit_and_keeps_both_backups_outside_the_scanned_folders(kind, fake_home, tmp_path):
    head = _git("rev-parse", "HEAD")
    done = _run(kind, fake_home, "-Ref", head, "-Apply")
    assert done.returncode == 0, done.stderr + done.stdout

    expected = tmp_path / "expected"
    expected.mkdir()
    archive = tmp_path / "head.tar"
    subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", f"--output={archive}", head, "hermes_tameru_plugin"], check=True)
    shutil.unpack_archive(str(archive), str(expected))
    assert _tree(fake_home / "plugins" / "tameru") == _tree(expected / "hermes_tameru_plugin")

    backups = sorted(p.name for p in (fake_home / "plugin-backups").iterdir())
    assert any(n.startswith("plugins.tameru.bak-") for n in backups)
    assert any(n.startswith("desktop-plugins.tameru.plugin.js.bak-") for n in backups)
    assert sorted(p.name for p in (fake_home / "desktop-plugins" / "tameru").iterdir()) == ["plugin.js"]   # no backup left where Desktop scans
    assert (fake_home / "desktop-plugins" / "tameru" / "plugin.js").read_bytes() == (expected / "hermes_tameru_plugin" / "desktop" / "plugin.js").read_bytes()


@pytest.mark.parametrize("kind", KINDS)
def test_a_release_tag_still_works(kind, fake_home):
    done = _run(kind, fake_home, "-Tag", "v1.6.0")
    assert done.returncode == 0 and "v1.6.0" in done.stdout and "Nothing was changed" in done.stdout
