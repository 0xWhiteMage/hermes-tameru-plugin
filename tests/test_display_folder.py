"""The plugin works from a folder named like Desktop's label, so Settings can read "Tameru (ためる)".

Desktop labels an engine ``prettyName(context.engine)`` (underscores to spaces, each word capitalised) and Hermes loads
the engine from a plugin folder of that exact name. ``scripts/use_display_name.ps1`` renames both together.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import types
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hermes_tameru_plugin"
KANA = "\u305f\u3081\u308b"
FOLDER = f"tameru_({KANA})"


def pretty_name(value: str) -> str:
    """Desktop's ``prettyName`` (apps/desktop/src/lib/text.ts): underscores become spaces, each word is capitalised."""
    return re.sub(r"\b[A-Za-z0-9_]", lambda m: m.group(0).upper(), value.replace("_", " "))


def test_desktop_labels_the_folder_name_as_the_display_name():
    assert pretty_name(FOLDER) == f"Tameru ({KANA})"
    assert pretty_name("tameru") == "Tameru"


def test_the_plugin_id_does_not_depend_on_the_folder_name():
    manifest = yaml.safe_load((PACKAGE / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "tameru", "plugins.enabled and plugins.entries.tameru are keyed by the manifest name"
    assert manifest["label"].startswith(f"Tameru ({KANA})")


def test_the_package_imports_from_a_folder_with_a_display_name(tmp_path):
    folder = tmp_path / FOLDER
    shutil.copytree(PACKAGE, folder, ignore=shutil.ignore_patterns("__pycache__"))
    module_name = f"_hermes_user_context_engine.{FOLDER}"          # how Hermes names a user engine module
    parent = types.ModuleType("_hermes_user_context_engine")        # Hermes makes this synthetic parent package too
    parent.__path__ = []
    sys.modules["_hermes_user_context_engine"] = parent
    spec = importlib.util.spec_from_file_location(module_name, folder / "__init__.py", submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)                            # relative imports resolve against that odd package name
        assert callable(getattr(module, "register", None))
        engine_module = importlib.import_module(f"{module_name}.engine")
        assert hasattr(engine_module, "TameruContextEngine")
    finally:
        for name in [n for n in sys.modules if n == module_name or n.startswith(module_name + ".")]:
            del sys.modules[name]
        sys.modules.pop("_hermes_user_context_engine", None)


def test_the_scripts_build_the_same_folder_name_and_stay_ascii():
    script = (ROOT / "scripts" / "use_display_name.ps1").read_text(encoding="utf-8")
    assert script.isascii(), "Windows PowerShell 5.1 reads a BOM-less file as ANSI; build non-ASCII text from code points"
    assert "0x305F, 0x3081, 0x308B" in script and KANA == "".join(chr(c) for c in (0x305F, 0x3081, 0x308B))
    rollback = (ROOT / "scripts" / "rollback.ps1").read_text(encoding="utf-8")
    assert rollback.isascii() and "0x305F, 0x3081, 0x308B" in rollback


def test_the_scripts_never_leave_a_backup_where_hermes_or_desktop_would_load_it():
    for name in ("use_display_name.ps1", "rollback.ps1"):
        script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "plugin-backups" in script, name
        assert '".$from.bak' not in script and '".$folder.bak' not in script, "a dot-named backup inside plugins/ is still loaded"
        assert '"$desktop.bak' not in script, "a backup beside the panel inside desktop-plugins/ is still loaded"
    rollback = (ROOT / "scripts" / "rollback.ps1").read_text(encoding="utf-8")
    assert "desktop-plugins\\$folder" in rollback, "the panel follows the renamed folder"
