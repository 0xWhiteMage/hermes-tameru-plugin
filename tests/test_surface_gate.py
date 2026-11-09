"""Verbs that show raw output or change behaviour run only on Desktop, CLI and TUI, never in a shared chat."""
from __future__ import annotations

import json

import pytest
from engine_support import Chat, demote, make_engine, pytest_failure_with_passes
from test_autotune_engine import _MetaDB

from hermes_tameru_plugin.activity import (
    GATED_VERBS, SURFACE_REFUSAL, is_interactive_surface,
)
from hermes_tameru_plugin.config import TameruSettings


def _engine_and_ref():
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive"))
    engine.bind_session_state(session_db=_MetaDB(), session_id="s1")
    chat = Chat("why did the gateway time out?")
    idx = chat.tool("terminal", {"command": "pytest -q"}, pytest_failure_with_passes())
    changed, out = demote(engine, chat.finish(), idx)
    assert changed
    engine._observe_activity(out)
    return engine, engine.activity_snapshot()["items"][0]["ref"]


def _run(engine, text, **context):
    return json.loads(engine._activity_registry.command("--json " + text, session_id="s1", **context))


@pytest.mark.parametrize("platform,source,expected", [
    ("cli", None, True), ("tui", None, True), ("desktop", None, True), ("acp", None, True), ("CLI", None, True),
    ("webui", None, True), ("oneshot", None, True),              # local clients label their sessions in varied ways
    ("telegram", object(), False), ("discord", None, False), ("slack", object(), False),
    ("", None, True), (None, None, True),                       # a bare call (tests, older hosts) with no gateway source
    ("", object(), False), (None, object(), False),             # a gateway source with no client name: fail closed
    ("local", object(), True),                                  # Desktop: the gateway path, platform LOCAL (seen live)
    ("tui", object(), True),
    ("somenewmessenger", object(), False), ("somenewmessenger", None, True),
])
def test_which_callers_count_as_interactive(platform, source, expected):
    assert is_interactive_surface(platform, source) is expected


@pytest.mark.parametrize("verb", sorted(GATED_VERBS))
def test_a_messaging_platform_cannot_run_a_gated_verb(verb):
    engine, ref = _engine_and_ref()
    arg = f"{verb} {ref}" if verb in ("view", "show") else verb
    out = _run(engine, arg, platform="telegram", source=object())
    assert out["ok"] is False and out["error"] == SURFACE_REFUSAL
    assert "content" not in out


def test_a_messaging_platform_can_still_read_the_summary_and_the_change_list():
    engine, ref = _engine_and_ref()
    assert _run(engine, "", platform="telegram", source=object())["ok"] is True
    assert _run(engine, "changes", platform="telegram", source=object())["ok"] is True


def test_desktop_cli_and_tui_can_view_an_original():
    engine, ref = _engine_and_ref()
    for platform in ("desktop", "cli", "tui"):
        out = _run(engine, f"view {ref}", platform=platform, source=None)
        assert out["ok"] is True and out["content"]


def test_refused_verbs_are_counted_without_content():
    engine, ref = _engine_and_ref()
    registry = engine._activity_registry
    before = getattr(registry, "refused_verbs", 0)
    _run(engine, f"view {ref}", platform="telegram", source=object())
    assert registry.refused_verbs == before + 1
