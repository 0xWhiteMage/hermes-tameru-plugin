"""``/tameru help``, ``stats`` and ``reset``."""
from __future__ import annotations

import json

import pytest
from engine_support import make_engine
from test_autotune_engine import bind_meta

from hermes_tameru_plugin.activity import SURFACE_REFUSAL
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.info_commands import HELP_LINES, help_text


def _live(**settings):
    engine = make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="adaptive", **settings))
    bind_meta(engine, None, "s1")
    return engine


def _cmd(engine, args, platform="desktop"):
    return engine._activity_registry.command(args, session_id="s1", platform=platform)


def test_help_lists_every_verb_and_is_available_everywhere():
    text = help_text()
    for command, _what in HELP_LINES:
        assert command in text
    for verb in ("keep", "restore", "pin", "bias", "pause", "policy", "stats", "reset", "changes", "view"):
        assert f"/tameru {verb}" in text


def test_the_footer_of_the_default_view_points_at_help_and_stats(monkeypatch):
    engine = _live()
    out = _cmd(engine, "")
    assert "/tameru help" in out and "/tameru stats" in out


def test_stats_prints_numbers_only_and_says_freed_is_not_cost():
    engine = _live()
    out = _cmd(engine, "stats")
    assert out.startswith("Tameru - numbers for this chat")
    assert "Context freed" in out and "not what the chat costs" in out and "Outputs shortened" in out


def test_help_and_stats_also_work_from_a_shared_chat_but_reset_does_not():
    engine = _live()
    assert "commands" in _cmd(engine, "help", platform="telegram")
    assert _cmd(engine, "stats", platform="telegram").startswith("Tameru - numbers")
    assert _cmd(engine, "reset confirm", platform="telegram") == SURFACE_REFUSAL


def test_reset_without_confirm_changes_nothing_and_lists_what_it_would_do():
    engine = _live()
    engine._policy.tuner.set_bias(0.5)
    out = _cmd(engine, "reset")
    assert "A reset would change" in out and "bias +0.50" in out and "Run /tameru reset confirm" in out
    assert engine._policy.tuner.bias == 0.5


def test_reset_confirm_puts_the_chat_back_to_neutral_and_keeps_pins():
    engine = _live()
    _cmd(engine, "pin always run the tests with --no-cov")
    engine._policy.tuner.set_bias(-0.8)
    engine._policy.paused = True
    out = _cmd(engine, "reset confirm")
    assert "Tameru reset for this chat" in out and "Pins and Keeps are unchanged" in out
    assert engine._policy.tuner.bias == 0.0 and engine._policy.paused is False and engine._policy.tuner.pristine()
    assert "always run the tests" in _cmd(engine, "pins")


def test_reset_on_a_neutral_chat_says_so():
    engine = _live()
    assert "already neutral" in _cmd(engine, "reset")
    assert "Nothing needed resetting" in _cmd(engine, "reset confirm")


def test_reset_rejects_unknown_arguments_and_json_confirm():
    engine = _live()
    assert "Use /tameru reset" in _cmd(engine, "reset now")
    assert "Refresh context details" in json.loads(_cmd(engine, "--json reset confirm"))["error"]


def test_a_model_hermes_compacts_on_the_providers_servers_gets_a_note_in_the_default_view(monkeypatch):
    from hermes_tameru_plugin import engine as engine_module
    engine = _live()
    assert "compact this model on its own servers" not in _cmd(engine, "")
    monkeypatch.setattr(engine_module, "native_compaction_active", lambda *a, **k: True)
    out = _cmd(engine, "")
    assert "compact this model on its own servers" in out and "pins" in out


def test_native_compaction_is_false_when_the_hermes_module_is_missing_or_raises(monkeypatch):
    from hermes_tameru_plugin import hermes_compat
    monkeypatch.setattr(hermes_compat, "_hermes", lambda module, attr: None)
    assert hermes_compat.native_compaction_active("gpt-5.6", "openai") is False

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(hermes_compat, "_hermes", lambda module, attr: boom)
    assert hermes_compat.native_compaction_active("gpt-5.6", "openai") is False


@pytest.mark.real_hermes
def test_the_real_hermes_function_says_yes_for_a_supported_route_and_no_for_others():
    from hermes_tameru_plugin.hermes_compat import native_compaction_active
    assert native_compaction_active("gpt-5.6", "openai", "https://api.openai.com/v1") is True
    assert native_compaction_active("claude-haiku-4-5", "anthropic", "https://api.anthropic.com") is False


def test_freed_tokens_count_the_live_rows_after_a_restart_when_no_cleanup_of_this_process_is_recorded():
    from test_edits import _shortened
    engine, _out, _idx, _ref, _original = _shortened()
    engine._policy.saved_tokens = 0                            # a fresh process: its policy has no cleanups yet
    savings = engine.activity_snapshot()["progress"]["savings"]
    assert savings["shortened"] == 1 and savings["tokens"] > 0


def test_freed_tokens_keep_the_larger_of_the_recorded_cleanups_and_the_rows():
    from test_edits import _shortened
    engine, *_ = _shortened()
    engine._policy.saved_tokens = 10_000_000
    assert engine.activity_snapshot()["progress"]["savings"]["tokens"] == 10_000_000
