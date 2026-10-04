"""Shared pytest setup for the Hermes Tameru plugin.

Everything here runs once, at conftest import time (before any test module imports ``agent.*``):

* ``HERMES_HOME`` points at a fresh temp dir, so no test ever reads or writes ``~/.hermes``.
* Every ``TAMERU_*`` / ``HERMES_*`` variable of the caller's environment is removed, except
  ``HERMES_REPO_ROOT`` (selects real Hermes), ``HERMES_HOME`` (set here) and
  ``TAMERU_WRITE_BASELINES`` (a harness switch of ``tests/eval``, not engine configuration).
* Real Hermes vs stub: with ``HERMES_REPO_ROOT`` set that checkout goes to ``sys.path[0]`` and
  ``REAL_HERMES`` is True; otherwise ``tests/hermes_stub`` (a minimal stand-in for the ``agent``
  package) is used and ``REAL_HERMES`` is False.

Markers: ``real_hermes`` (skipped unless real Hermes), ``stub_only`` (skipped on real Hermes) and
``perf`` (scaling assertions; CI excludes them with ``-m "not perf"``).
"""
from __future__ import annotations

import atexit
import itertools
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
STUB_ROOT = TESTS_DIR / "hermes_stub"

# ---- environment isolation -----------------------------------------------------------------
_KEEP_ENV = {"HERMES_REPO_ROOT", "HERMES_HOME", "TAMERU_WRITE_BASELINES"}
for _name in list(os.environ):
    if _name.startswith(("TAMERU_", "HERMES_")) and _name not in _KEEP_ENV:
        del os.environ[_name]

SESSION_HERMES_HOME = tempfile.mkdtemp(prefix="tameru-hermes-home-")
os.environ["HERMES_HOME"] = SESSION_HERMES_HOME
atexit.register(shutil.rmtree, SESSION_HERMES_HOME, ignore_errors=True)

# ---- real Hermes or stub -------------------------------------------------------------------
# The plugin repo root is importable without installing the package (``pytest`` vs ``python -m pytest``).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_real_root = os.environ.get("HERMES_REPO_ROOT", "").strip()
REAL_HERMES = bool(_real_root)
if REAL_HERMES:
    _root = Path(_real_root).expanduser().resolve()
    if not (_root / "agent" / "context_compressor.py").is_file():
        raise RuntimeError(f"HERMES_REPO_ROOT={_real_root!r} is not a Hermes Agent checkout")
    HERMES_REPO_ROOT: Path | None = _root
    sys.path.insert(0, str(_root))
else:
    HERMES_REPO_ROOT = None
    sys.path.insert(0, str(STUB_ROOT))


# ---- markers --------------------------------------------------------------------------------
def pytest_configure(config):
    config.addinivalue_line("markers", "real_hermes: needs a real Hermes checkout (HERMES_REPO_ROOT); skipped otherwise")
    config.addinivalue_line("markers", "stub_only: only meaningful against tests/hermes_stub; skipped on real Hermes")
    config.addinivalue_line("markers", "perf: scaling/performance assertion; CI runs the suite with -m 'not perf'")


def pytest_collection_modifyitems(config, items):
    skip_real = pytest.mark.skip(reason="needs real Hermes (set HERMES_REPO_ROOT)")
    skip_stub = pytest.mark.skip(reason="stub-only test; real Hermes is active")
    for item in items:
        if "real_hermes" in item.keywords and not REAL_HERMES:
            item.add_marker(skip_real)
        if "stub_only" in item.keywords and REAL_HERMES:
            item.add_marker(skip_stub)


# ---- fixtures -------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def hermes_mode() -> str:
    """``"real"`` when ``HERMES_REPO_ROOT`` selected a Hermes checkout, else ``"stub"``."""
    return "real" if REAL_HERMES else "stub"


@pytest.fixture
def hermes_home(tmp_path, monkeypatch) -> Path:
    """A fresh ``HERMES_HOME`` for one test (config.yaml, plugins/, ...)."""
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _tool_turn_factory():
    """A ``make_tool_turn`` with its own id counter: generated ``call_NNNN`` ids start at 1 in every
    test, whatever ran before it (a module-level counter made ids depend on test selection/order)."""
    call_ids = itertools.count(1)

    def make_tool_turn(name, args, content, call_id=None):
        """``(assistant_msg_with_tool_calls, tool_msg)`` in OpenAI chat format."""
        call_id = call_id or f"call_{next(call_ids):04d}"
        arguments = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        assistant = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}],
        }
        tool = {"role": "tool", "tool_call_id": call_id, "content": content, "tool_name": name}
        return assistant, tool

    return make_tool_turn


@pytest.fixture
def make_tool_turn():
    """Factory ``make_tool_turn(name, args, content, call_id=None)`` -> ``(assistant, tool)`` rows."""
    return _tool_turn_factory()


def _numbered(text, start=1):
    """The ``<n>|`` gutter Hermes' ``read_file`` puts in front of every line."""
    if text.endswith("\n"):
        text = text[:-1]
    return "\n".join(f"{i}|{line}" for i, line in enumerate(text.split("\n"), start=start))


def read_file_payload(text, *, total_lines=None, offset=1, truncated=False, hint=None):
    """Hermes ``read_file`` result: ``ReadResult.to_dict()`` serialized with ``json.dumps``."""
    numbered = _numbered(text, offset)
    body = {
        "content": numbered,
        "total_lines": total_lines if total_lines is not None else numbered.count("\n") + 1,
        "file_size": len(text.encode("utf-8")),
        "truncated": truncated,
    }
    if hint:
        body["hint"] = hint
    body.update({"is_binary": False, "is_image": False, "not_found": False})
    return json.dumps(body, ensure_ascii=False)


def terminal_payload(output, *, exit_code=0, error=None):
    """Hermes ``terminal`` result envelope: ``output`` / ``exit_code`` / ``error``."""
    return json.dumps({"output": output, "exit_code": exit_code, "error": error}, ensure_ascii=False)


def search_files_payload(matches, *, densify=True, truncated=False):
    """Hermes ``search_files`` result: ``SearchResult.to_dict(densify=True)`` serialized.

    ``matches`` is a list of ``(path, line_number, content)``. With 5 or more matches Hermes
    replaces the ``matches`` array by a path-grouped ``matches_text`` block.
    """
    body = {"total_count": len(matches)}
    if matches:
        if densify and len(matches) >= 5:
            lines, current = [], None
            for path, line_no, content in matches:
                if path != current:
                    lines.append(path)
                    current = path
                lines.append(f"  {line_no}: {content.rstrip()}")
            body["matches_format"] = (
                "path-grouped: each file path on its own line, followed by "
                "indented '<line>: <content>' rows for matches in that file"
            )
            body["matches_text"] = "\n".join(lines)
        else:
            body["matches"] = [{"path": p, "line": n, "content": c} for p, n, c in matches]
    if truncated:
        body["truncated"] = True
        body["total_count_is_lower_bound"] = True
    return json.dumps(body, ensure_ascii=False)


@pytest.fixture
def payloads():
    """Hermes-shaped tool payload builders (``read_file``, ``terminal``, ``search_files``)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        read_file=read_file_payload, terminal=terminal_payload, search_files=search_files_payload,
    )
