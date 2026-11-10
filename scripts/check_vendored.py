#!/usr/bin/env python3
"""Check that the vendored engine is the engine repository's source at the commit ``VENDORED_FROM`` records.

    python scripts/check_vendored.py PATH_TO_ENGINE_CLONE        # or set TAMERU_ENGINE_REPO

Compares every module under ``src/tameru`` of the engine repository, as it was at that commit, with the copy in
``hermes_tameru_plugin/tameru`` (line endings ignored). ``__init__.py`` is the plugin's own (it records the commit)
and is skipped. Exit status 0 when the two agree, 1 on any difference, a missing or an extra module, 2 on a usage
error. Reads git objects only; it changes nothing.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "hermes_tameru_plugin" / "tameru"
OWN = {"__init__.py"}


def vendored_from() -> str:
    match = re.search(r'^VENDORED_FROM\s*=\s*"([0-9a-f]{40})"', (PLUGIN / "__init__.py").read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit("hermes_tameru_plugin/tameru/__init__.py has no VENDORED_FROM commit")
    return match.group(1)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def compare(engine_repo: Path, commit: str) -> list[str]:
    """Differences between the engine at ``commit`` and the vendored folder, as readable lines (empty when they agree)."""
    try:
        git(engine_repo, "cat-file", "-e", f"{commit}^{{commit}}")
    except subprocess.CalledProcessError:
        return [f"commit {commit} is not in {engine_repo} (push the engine repository first, or fetch it)"]
    names = [n for n in git(engine_repo, "ls-tree", "-r", "--name-only", commit, "src/tameru").decode().splitlines()
             if n.endswith(".py")]
    engine_files = {Path(n).name: n for n in names}
    problems: list[str] = []
    for name, path in sorted(engine_files.items()):
        if name in OWN:
            continue
        mine = PLUGIN / name
        if not mine.is_file():
            problems.append(f"missing from the plugin: {name}")
            continue
        theirs = git(engine_repo, "show", f"{commit}:{path}").replace(b"\r\n", b"\n")
        if theirs != mine.read_bytes().replace(b"\r\n", b"\n"):
            problems.append(f"differs from the engine at {commit[:12]}: {name}")
    for mine in sorted(PLUGIN.glob("*.py")):
        if mine.name not in engine_files and mine.name not in OWN:
            problems.append(f"not in the engine at {commit[:12]}: {mine.name}")
    return problems


def main(argv: list[str]) -> int:
    where = argv[0] if argv else os.environ.get("TAMERU_ENGINE_REPO", "")
    if not where or not (Path(where) / ".git").exists():
        print("usage: check_vendored.py PATH_TO_ENGINE_CLONE (or set TAMERU_ENGINE_REPO)", file=sys.stderr)
        return 2
    commit = vendored_from()
    problems = compare(Path(where), commit)
    if problems:
        print(f"The vendored engine does not match the engine repository at {commit}:")
        print("\n".join("  " + p for p in problems))
        return 1
    print(f"The vendored engine matches the engine repository at {commit}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
