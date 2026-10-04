"""pytest setup for ``tests/eval`` (owned by the session-replay harness).

Self-contained on purpose: it does not depend on ``tests/conftest.py``.

* When ``HERMES_REPO_ROOT`` is set, that checkout is put on ``sys.path`` (first) so ``agent.*`` resolves
  to real Hermes. ``HERMES_HOME`` falls back to a throwaway directory so nothing touches ``~/.hermes``.
* Tests carrying the ``real_hermes`` marker are skipped when ``HERMES_REPO_ROOT`` is not set (stub mode). When
  it IS set, a checkout that cannot be imported, or that lacks the module's own ``call_llm`` seam, FAILS those
  tests: a required real-Hermes CI job must never go green by skipping everything.
* ``tests/`` is put on ``sys.path`` so ``from fixtures import ...`` works whatever the rootdir.
"""
from __future__ import annotations

import atexit
import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
_TESTS_DIR = _EVAL_DIR.parent
_REPO_ROOT = _TESTS_DIR.parent

for _p in (_EVAL_DIR, _TESTS_DIR, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_hermes_root = os.environ.get("HERMES_REPO_ROOT", "").strip()
if _hermes_root:
    _root = str(Path(_hermes_root).expanduser().resolve())
    if _root not in sys.path:
        sys.path.insert(0, _root)

if not os.environ.get("HERMES_HOME"):
    _home = tempfile.mkdtemp(prefix="tameru-eval-home-")
    os.environ["HERMES_HOME"] = _home
    atexit.register(shutil.rmtree, _home, ignore_errors=True)


def _real_hermes_problem() -> str | None:
    """Why the real-Hermes tests cannot run here, or None when they can."""
    if not _hermes_root:
        return "HERMES_REPO_ROOT is not set (the session replay needs real Hermes)"
    try:
        mod = importlib.import_module("agent.context_compressor")
    except Exception as exc:  # noqa: BLE001 - ImportError, or any Hermes import-time failure
        return f"agent.context_compressor cannot be imported: {type(exc).__name__}: {exc}"
    if not hasattr(mod, "call_llm"):
        return "agent.context_compressor has no call_llm seam (stub or incompatible Hermes)"
    return None


@pytest.fixture(autouse=True)
def _skip_without_real_hermes(request):
    if request.node.get_closest_marker("real_hermes") is None:
        return
    problem = _real_hermes_problem()
    if problem:
        if _hermes_root:
            pytest.fail(problem, pytrace=False)  # the caller asked for real Hermes and did not get it
        pytest.skip(problem)
