"""Packaging invariants: metadata agrees, the entry point exists, no module falls out of the wheel."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_PKG = REPO_ROOT / "hermes_tameru_plugin"
PLUGIN_YAML = PLUGIN_PKG / "plugin.yaml"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _yaml_scalar(key: str) -> str | None:
    """Top-level ``key: value`` of plugin.yaml (flat file; avoids a PyYAML dependency)."""
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.*?)\s*$", PLUGIN_YAML.read_text(encoding="utf-8"), re.M)
    return match.group(1).strip("'\"") if match else None


def test_pyproject_and_plugin_yaml_versions_agree(pyproject):
    version = pyproject["project"]["version"]
    # X.Y.Z with an optional PEP 440 pre/post/dev suffix, so a release candidate (1.4.0rc1) can be tagged.
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?", version), version
    assert _yaml_scalar("version") == version


def test_plugin_yaml_has_no_kind_key():
    # ``kind: context-engine`` is not a valid Hermes plugin kind (G12).
    assert _yaml_scalar("kind") is None
    assert not re.search(r"^\s*kind\s*:", PLUGIN_YAML.read_text(encoding="utf-8"), re.M)
    assert _yaml_scalar("name") == "tameru"


def test_entry_point_declared(pyproject):
    entry_points = pyproject["project"]["entry-points"]["hermes_agent.plugins"]
    assert entry_points == {"tameru": "hermes_tameru_plugin"}
    assert (PLUGIN_PKG / "__init__.py").is_file()


def test_python_floor_matches_hermes(pyproject):
    # Hermes itself supports 3.11-3.14.
    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert "dev" in pyproject["project"]["optional-dependencies"]


def _package_dirs() -> dict[str, list[Path]]:
    """dotted package name -> .py modules, for every directory under hermes_tameru_plugin/."""
    found: dict[str, list[Path]] = {}
    for directory in [PLUGIN_PKG, *(p for p in PLUGIN_PKG.rglob("*") if p.is_dir())]:
        if "__pycache__" in directory.parts:
            continue
        modules = sorted(directory.glob("*.py"))
        if modules:
            dotted = ".".join(directory.relative_to(REPO_ROOT).parts)
            found[dotted] = modules
    return found


def test_every_module_is_covered_by_the_packages_list(pyproject):
    packages = set(pyproject["tool"]["setuptools"]["packages"])
    assert {"hermes_tameru_plugin", "hermes_tameru_plugin.tameru"} <= packages
    for dotted, modules in _package_dirs().items():
        assert dotted in packages, f"{[m.name for m in modules]} live in {dotted}, which is not packaged"
        assert (REPO_ROOT.joinpath(*dotted.split(".")) / "__init__.py").is_file(), f"{dotted} lacks __init__.py"
    assert not [p for p in packages if p.split(".")[0] in {"tests", "docs"}], "tests/ must not be packaged"
    package_data = pyproject["tool"]["setuptools"]["package-data"]["hermes_tameru_plugin"]
    assert "plugin.yaml" in package_data


def test_build_py_ships_every_module_and_plugin_yaml(pyproject, tmp_path, monkeypatch):
    """Offline wheel simulation: run setuptools' ``build_py`` with the declared packages."""
    setuptools = pytest.importorskip("setuptools")
    monkeypatch.chdir(REPO_ROOT)
    tool = pyproject["tool"]["setuptools"]
    dist = setuptools.Distribution({
        "name": pyproject["project"]["name"],
        "packages": tool["packages"],
        "package_data": tool["package-data"],
    })
    dist.script_name = "setup.py"  # build_py resolves it; there is no setup.py (PEP 517 build)
    build_py = dist.get_command_obj("build_py")
    build_py.build_lib = str(tmp_path / "lib")
    build_py.compile = False
    build_py.ensure_finalized()
    build_py.run()
    shipped = {p.relative_to(tmp_path / "lib").as_posix() for p in (tmp_path / "lib").rglob("*") if p.is_file()}
    expected = {
        "/".join((*dotted.split("."), module.name)) for dotted, modules in _package_dirs().items() for module in modules
    }
    assert expected <= shipped, sorted(expected - shipped)
    assert "hermes_tameru_plugin/plugin.yaml" in shipped
    assert not [p for p in shipped if p.startswith(("tests/", "docs/"))]


def test_sdist_excludes_tests():
    """distutils adds tests/test*.py to an sdist by default; without conftest/stub they cannot run."""
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert re.search(r"^prune tests\s*$", manifest, re.M), "MANIFEST.in must prune tests"


def test_plugin_registers_a_context_engine():
    """The package imports (stub or real Hermes) and ``register`` hands Hermes one engine."""
    import hermes_tameru_plugin

    class Ctx:
        engines: list = []

        def register_context_engine(self, engine):
            self.engines.append(engine)

    ctx = Ctx()
    hermes_tameru_plugin.register(ctx)
    (engine,) = ctx.engines
    assert engine.name == "tameru" and isinstance(engine, hermes_tameru_plugin.ExtractiveContextEngine)


def test_vendored_engine_is_pinned_to_a_commit_and_matches_the_package_version(pyproject):
    from hermes_tameru_plugin.tameru import VENDORED_FROM
    from hermes_tameru_plugin.tameru.compress_context import ENGINE_VERSION

    assert re.fullmatch(r"[0-9a-f]{40}", VENDORED_FROM), VENDORED_FROM
    assert ENGINE_VERSION == pyproject["project"]["version"]
