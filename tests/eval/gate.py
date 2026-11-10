"""Write the gate report for the current commit: what was run, on what, and whether it passed.

``python tests/eval/gate.py [OUTPUT_DIR]`` runs the stub suite, the real-Hermes suite (when ``HERMES_REPO_ROOT`` is set)
and the Desktop panel tests, and writes ``OUTPUT_DIR/gate-<commit>.json`` (default ``docs/gate-reports``). "Done" means
this file exists for HEAD and says ``"clean": true`` and ``"passed": true``; the changelog and the docs may quote
numbers only from it. The stock canary is a hash of the saved stock-Hermes baselines: if it changes, the harness moved,
not the product.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()


def run(name: str, command: list[str], env: dict[str, str] | None = None) -> dict:
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env={**os.environ, **(env or {})})
    tail = [line for line in done.stdout.strip().splitlines() if line.strip()][-1:] or [""]
    return {"name": name, "exit_code": done.returncode, "summary": tail[0], "passed": done.returncode == 0}


def stock_canary() -> str:
    """One hash over every saved stock-Hermes baseline (the engines that contain no Tameru code)."""
    digest = hashlib.sha256()
    for path in sorted((ROOT / "tests" / "eval" / "baselines" / "protocol-2").rglob("stock*.json")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    out_dir = Path(argv[0]) if argv else ROOT / "docs" / "gate-reports"
    commit = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    hermes_root = os.environ.get("HERMES_REPO_ROOT", "").strip()
    suites = [run("stub", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], {"HERMES_REPO_ROOT": ""})]
    if hermes_root:
        suites.append(run("real_hermes", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]))
    deps = os.environ.get("TAMERU_UI_TEST_DEPS", "").strip()
    if deps:
        suites.append(run("desktop_panel", ["node", "--experimental-vm-modules", "--test", "tests/desktop/activity.test.mjs"]))
    report = {
        "commit": commit, "clean": not dirty, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hermes_pin": (ROOT / "HERMES_PIN").read_text(encoding="utf-8").strip(),
        "hermes_checkout": git_head(hermes_root) if hermes_root else None,
        "stock_canary": stock_canary(), "suites": suites,
        "skipped": [n for n, ran in (("real_hermes", bool(hermes_root)), ("desktop_panel", bool(deps))) if not ran],
    }
    report["passed"] = all(s["passed"] for s in suites) and report["clean"] and not report["skipped"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"gate-{commit[:12]}.json"
    path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"{path}: passed={report['passed']} clean={report['clean']} skipped={report['skipped']}")
    return 0 if report["passed"] else 1


def git_head(checkout: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", checkout, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
