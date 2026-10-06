"""Hermes' directory loader executes every top-level ``*.py`` of the plugin eagerly, as ``<pkg>.<stem>``,
and silently drops one that fails. These tests import each module that way, in a subprocess, with and
without ``agent`` importable, and check that nothing raises and nothing happens at import time."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import HERMES_REPO_ROOT, REAL_HERMES, STUB_ROOT

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "hermes_tameru_plugin"
STEMS = sorted(p.stem for p in PLUGIN_DIR.glob("*.py") if p.stem != "__init__")

_SCRIPT = textwrap.dedent(
    """
    import importlib, json, os, sys, threading, types

    plugin_dir, agent_root, hermes_home, with_agent, load_init = sys.argv[1:6]
    os.environ["HERMES_HOME"] = hermes_home
    if with_agent == "1":
        sys.path.insert(0, agent_root)
    else:
        for name in ("agent", "hermes_cli", "plugins"):
            sys.modules[name] = None            # `import agent...` raises ImportError
    if with_agent == "1":
        import agent.context_compressor          # Hermes' own import side effects are not the plugin's
    env_before, threads_before = dict(os.environ), threading.active_count()
    home_before = set(os.listdir(hermes_home))
    package = types.ModuleType("zz_throwaway")
    package.__path__ = [plugin_dir]
    sys.modules["zz_throwaway"] = package

    stems = json.loads(sys.argv[6])
    errors = {}
    for stem in stems:                            # alphabetical, as the loader does
        try:
            importlib.import_module(f"zz_throwaway.{stem}")
        except BaseException as exc:
            errors[stem] = repr(exc)
    result = {"errors": errors}
    if load_init == "1":
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "zz_throwaway", os.path.join(plugin_dir, "__init__.py"),
                submodule_search_locations=[plugin_dir],
            )
            init = importlib.util.module_from_spec(spec)
            sys.modules["zz_throwaway"] = init        # siblings stay loaded, as in Hermes' loader
            spec.loader.exec_module(init)
            registered = []
            ctx = types.SimpleNamespace(register_context_engine=registered.append)
            init.register(ctx)
            result["registered"] = [type(e).__name__ for e in registered]
        except BaseException as exc:
            result["errors"]["__init__"] = repr(exc)
    engine = sys.modules.get("zz_throwaway.engine")
    if engine is not None:
        try:
            engine.TameruContextEngine()
            result["instantiated"] = True
        except ImportError:
            result["instantiated"] = False
        except BaseException as exc:
            result["instantiate_error"] = repr(exc)
    result["env_added"] = sorted(set(os.environ) - set(env_before))
    result["threads_started"] = threading.active_count() - threads_before
    result["home_files"] = sorted(set(os.listdir(hermes_home)) - home_before)
    print("RESULT " + json.dumps(result))
    """
)


def _run(tmp_path, *, with_agent: bool, load_init: bool = False, stems=STEMS) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    agent_root = HERMES_REPO_ROOT if REAL_HERMES else STUB_ROOT
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(PLUGIN_DIR), str(agent_root), str(home),
         "1" if with_agent else "0", "1" if load_init else "0", json.dumps(stems)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, f"no result\n{proc.stdout}\n{proc.stderr}"
    return json.loads(lines[-1][len("RESULT "):])


def test_every_module_is_listed():
    assert {"config", "engine", "hermes_compat", "ledger", "payload", "query", "recovery", "render",
            "supersede", "telemetry"} <= set(STEMS)


@pytest.mark.parametrize("stem", STEMS)
def test_each_module_imports_alone_as_a_sibling_without_hermes(tmp_path, stem):
    result = _run(tmp_path, with_agent=False, stems=[stem])
    assert result["errors"] == {}


def test_all_modules_import_eagerly_without_hermes_and_nothing_happens(tmp_path):
    result = _run(tmp_path, with_agent=False)
    assert result["errors"] == {}
    assert result["threads_started"] == 0 and result["home_files"] == [] and result["env_added"] == []
    assert result["instantiated"] is False       # ImportError, lazily, only when the engine is built


def test_all_modules_import_eagerly_with_hermes_and_nothing_happens(tmp_path):
    result = _run(tmp_path, with_agent=True)
    assert result["errors"] == {}
    assert result["threads_started"] == 0 and result["home_files"] == [] and result["env_added"] == []
    assert "instantiate_error" not in result


def test_register_never_raises_without_hermes(tmp_path):
    result = _run(tmp_path, with_agent=False, load_init=True)
    assert result["errors"] == {} and result["registered"] == []


def test_register_registers_one_engine_with_hermes(tmp_path):
    result = _run(tmp_path, with_agent=True, load_init=True)
    assert result["errors"] == {} and result["registered"] == ["TameruContextEngine"]


def test_modules_use_only_relative_imports_of_siblings():
    """No ``import hermes_tameru_plugin`` (the package ``__init__``) and no absolute sibling imports."""
    import re

    pattern = re.compile(r"^\s*(?:from|import)\s+hermes_tameru_plugin\b", re.MULTILINE)
    for stem in STEMS:
        text = (PLUGIN_DIR / f"{stem}.py").read_text(encoding="utf-8")
        assert not pattern.search(text), f"{stem}.py imports the package by name"
