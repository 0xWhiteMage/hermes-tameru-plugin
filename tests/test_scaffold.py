"""The test scaffold itself: environment isolation and fixture determinism (see ``conftest.py``)."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import TESTS_DIR, _tool_turn_factory


def test_conftest_scrubs_hermes_and_tameru_env(tmp_path):
    """A developer's HERMES_* / TAMERU_* variables must never reach the tests, and HERMES_HOME is a temp dir."""
    mine = tmp_path / "my_hermes_home"
    env = {**os.environ, "HERMES_FOO": "1", "TAMERU_BAR": "1", "HERMES_HOME": str(mine)}
    env.pop("HERMES_REPO_ROOT", None)
    code = (
        "import json, os, conftest; "
        "print(json.dumps({k: v for k, v in os.environ.items() if k.startswith(('HERMES_', 'TAMERU_'))}))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=TESTS_DIR, env=env, capture_output=True, text=True, check=True,
    )
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert set(seen) == {"HERMES_HOME"}, seen
    assert seen["HERMES_HOME"] != str(mine) and "tameru-hermes-home-" in seen["HERMES_HOME"]


def test_make_tool_turn_ids_do_not_depend_on_other_tests():
    first, second = _tool_turn_factory(), _tool_turn_factory()
    assistant, tool = first("terminal", {"command": "ls"}, "out")
    first("terminal", {"command": "pwd"}, "out")
    again, _ = second("terminal", {"command": "ls"}, "out")
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "call_0001"
    assert again["tool_calls"][0]["id"] == "call_0001", "every factory counts from 1"
    pinned, _ = first("terminal", "{}", "out", call_id="mine")
    assert pinned["tool_calls"][0]["id"] == "mine"
    assert pinned["tool_calls"][0]["function"]["arguments"] == "{}", "string arguments are passed through"
