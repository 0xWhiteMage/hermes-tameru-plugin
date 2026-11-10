"""``enabled: false`` must leave Hermes exactly stock: no hook, no system-prompt note, no tool, no change to requests.

The engine already behaves as Hermes' own compressor when it is disabled; these tests cover what registration adds
around it, and (on real Hermes) that a whole replayed session is the one stock Hermes produces.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hermes_tameru_plugin import RECOVERY_NOTE, register
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.engine import TameruContextEngine

HOOKS = {"pre_api_request", "post_api_request", "api_request_error", "post_compaction", "pre_llm_call"}


class RecordingCtx:
    """A general-plugin context (it has a ``plugin_id``) that records what ``register`` adds."""

    plugin_id = "tameru"

    def __init__(self, **settings):
        self.settings = settings
        self.engines, self.commands, self.hooks, self.sections = [], [], [], {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_context_engine(self, engine):
        self.engines.append(engine)

    def register_command(self, name, handler, **_):
        self.commands.append(name)

    def register_hook(self, name, callback):
        self.hooks.append(name)

    def register_system_prompt_section(self, id, content, **_):
        self.sections[id] = content


def test_enabled_registers_the_hooks_and_the_recovery_note():
    ctx = RecordingCtx()
    register(ctx)
    assert len(ctx.engines) == 1 and set(ctx.hooks) == HOOKS and ctx.sections == {"tameru.recovery": RECOVERY_NOTE}


def test_disabled_registers_no_hook_and_no_system_prompt_note():
    ctx = RecordingCtx(enabled=False)
    register(ctx)
    (engine,) = ctx.engines                       # the engine is still there, so ``context.engine: tameru`` keeps resolving
    assert ctx.hooks == [] and ctx.sections == {}
    assert engine.get_tool_schemas() == []
    assert ctx.commands == ["tameru"]             # the user can still ask what Tameru is doing (nothing)


def test_the_recovery_note_is_left_out_when_the_tool_is_off():
    """The note tells the model to call ``tameru_read_original``; without the tool that would be a wrong instruction."""
    ctx = RecordingCtx(expand_tool=False)
    register(ctx)
    assert set(ctx.hooks) == HOOKS and ctx.sections == {}
    assert ctx.engines[0].get_tool_schemas() == []


@pytest.mark.real_hermes
@pytest.mark.parametrize("scenario", ["default", "oncall", "dense"])
@pytest.mark.parametrize("window", [32_000, 64_000, 200_000])
def test_a_disabled_engine_replays_exactly_like_stock_hermes(scenario, window):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "eval"))
    import replay

    from agent.context_compressor import ContextCompressor

    def run(factory):
        return replay.deterministic_part(replay.run_session(factory, scenario=scenario, context_length=window))

    off = run(lambda: TameruContextEngine(settings=TameruSettings(enabled=False), quiet_mode=True))
    stock = run(lambda: ContextCompressor(model="test-model", quiet_mode=True))
    assert off == stock, "a disabled Tameru changed the session; compare the two metrics dictionaries"
