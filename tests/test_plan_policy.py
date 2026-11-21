"""The opt-in ``plan`` policy: the engine's ``plan()`` (compact mode) at Hermes' cache-broken moments.

The incumbent policies stay untouched — the default is still ``adaptive``, ``legacy`` is unchanged,
and under ``plan`` no plugin-supplied proactive prune trigger exists (only Hermes' own compaction /
prune moments plan). The retained budget, the trigger and the EV inputs are host numbers the adapter
computes; the engine owns ranking and vetoes.
"""
from __future__ import annotations

import inspect
import re

import pytest

from conftest import read_file_payload, terminal_payload
from engine_support import (
    MIN_PRUNE_CHARS,
    Chat,
    make_engine,
    prune_pass,
    pytest_failure,
    server_log,
    source_file,
)
from hermes_tameru_plugin import engine as engine_module
from hermes_tameru_plugin.config import TameruSettings, _SETTING_PARSERS
from hermes_tameru_plugin.engine import TameruContextEngine
from hermes_tameru_plugin.render import parse_header


def plan_engine(**kwargs) -> TameruContextEngine:
    return make_engine(TameruSettings(small_window_threshold_tokens=0, policy_mode="plan"), **kwargs)


def rungs(result: list[dict]) -> dict[int, str]:
    out = {}
    for i, msg in enumerate(result):
        if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
            header = parse_header(msg["content"])
            if header is not None:
                out[i] = header["rung"]
    return out


def fact_file(key_value: str, filler: int = 30, prefix: str = "f") -> str:
    """A ``cat`` result carrying one protected fact line (rare, <10% of the non-blank lines)."""
    lines = [f"def {prefix}unction_{i}():\n    return {i}" for i in range(filler)]
    return terminal_payload("\n".join(lines) + f"\n{key_value}\n")


# ---- settings --------------------------------------------------------------------------------


def test_plan_is_opt_in_and_incumbent_stays_default():
    assert TameruSettings().policy_mode == "adaptive"
    assert TameruSettings(policy_mode="plan").policy_mode == "plan"
    assert TameruSettings().price_profile == "anthropic-sonnet-5.5-5m"


def test_price_profile_parsing():
    parse = _SETTING_PARSERS["price_profile"]
    assert parse("anthropic-sonnet-5.5-5m") == "anthropic-sonnet-5.5-5m"
    assert parse("deepseek-v4-pro") == "deepseek-v4-pro"
    with pytest.raises(ValueError):
        parse("not-a-real-profile")


def test_policy_label_covers_plan():
    from hermes_tameru_plugin.policy_commands import _LABELS

    assert _LABELS["mode"]["plan"] == "Plan"


# ---- when it acts -----------------------------------------------------------------------------


def test_no_plugin_prune_trigger_under_plan():
    """The plugin-supplied prune trigger is adaptive machinery; under ``plan`` it is not set."""
    bare = TameruContextEngine(
        settings=TameruSettings(small_window_threshold_tokens=0, policy_mode="plan"),
        quiet_mode=True, config_context_length=64_000,
    )
    assert getattr(bare, "proactive_prune_tokens", 0) in (0, None)


def test_warm_prune_call_without_trigger_does_not_plan():
    """With no trigger armed (Hermes' proactive prune off) a call returns the input and plans nothing."""
    engine = plan_engine(proactive_prune_tokens=0)
    chat = Chat()
    chat.tool("terminal", {"command": "tail app.log"}, server_log())
    messages = chat.finish()
    out, count = engine.prune_tool_results_only(messages, current_tokens=50_000)
    assert out is messages and count == 0
    scope = engine_module._SCOPE.get()
    assert scope is None or scope.plan_input is None


# ---- the PlanInput ---------------------------------------------------------------------------


def _planned_scope(engine, messages, **fields):
    """Run a prune pass and return the scope ``_plan_rows`` planned (None when it never ran)."""
    holder = {}
    real = engine._plan_rows

    def capture(*a, **kw):
        real(*a, **kw)
        holder["scope"] = a[-1]

    engine._plan_rows = capture
    try:
        prune_pass(engine, messages, **fields)
    finally:
        engine._plan_rows = real
    return holder.get("scope")


def test_plan_input_shape():
    engine = plan_engine()
    chat = Chat()
    chat.tool("terminal", {"command": "tail app.log"}, server_log())
    chat.tool("read_file", {"path": "svc.py"}, source_file())
    messages = chat.finish()
    scope = _planned_scope(engine, messages)
    assert scope is not None
    pi = scope.plan_input
    assert pi is not None
    assert pi.event == "trigger" and pi.host_mode == "compact"
    assert (pi.K, pi.H) == (3, 0)
    assert pi.retained_budget_chars == engine._retained_budget()
    assert pi.policy.ev is not None
    assert pi.policy.ev.price_profile == "anthropic-sonnet-5.5-5m"
    assert pi.policy.ev.loss_profile == "replay-2026-11-21"
    assert pi.policy.ev.strength == "normal"
    assert pi.policy.require_fingerprints
    demoted = [i for i in pi.items if i.stock_action == "demote"]
    assert demoted, "the tool rows Hermes would demote are the plan items"
    assert all(i.stratum == "hermes" for i in demoted)
    assert all(isinstance(i.fact_lines, int) and i.fact_lines >= 0 for i in demoted)
    keep = [i for i in pi.items if i.stock_action == "keep"]
    assert keep and all(i.protected for i in keep)


def test_preference_maps_to_ev_strength():
    engine = plan_engine()
    engine._settings = TameruSettings(
        small_window_threshold_tokens=0, policy_mode="plan", preference="preserve_detail")
    chat = Chat()
    chat.tool("terminal", {"command": "tail app.log"}, server_log())
    scope = _planned_scope(engine, chat.finish())
    assert scope is not None
    assert scope.plan_input.policy.ev.strength == "more"


def test_supersession_blocked_without_fact_coverage():
    """An earlier row's facts absent from its successor block the ``superseded`` rung."""
    engine = plan_engine()
    chat = Chat()
    earlier = fact_file("region = \"eu-west-1\"")
    later = fact_file("mode: release", prefix="g")   # re-read lacks the earlier row's fact
    chat.tool("terminal", {"command": "cat a.py"}, earlier)
    chat.tool("terminal", {"command": "cat a.py"}, later)
    scope = _planned_scope(engine, chat.finish())
    assert scope is not None
    pi = scope.plan_input
    assert scope.plan_blocked >= 1
    blocked = [i for i in pi.items if i.superseded_by and not i.facts_covered_by_successor]
    assert blocked, "the earlier read's supersession lost its coverage"
    for action in scope.plan_obj.actions:
        if action.id in {b.id for b in blocked}:
            assert action.action != "superseded"


def test_supersession_allowed_with_fact_coverage():
    engine = plan_engine()
    chat = Chat()
    earlier = fact_file("region = \"eu-west-1\"", prefix="f")
    later = fact_file("region = \"eu-west-1\"", prefix="g")   # same fact, different file
    chat.tool("terminal", {"command": "cat a.py"}, earlier)
    chat.tool("terminal", {"command": "cat a.py"}, later)
    scope = _planned_scope(engine, chat.finish())
    assert scope is not None
    covered = [i for i in scope.plan_input.items if i.superseded_by and i.facts_covered_by_successor]
    assert covered, "identical re-reads carry each other's facts"


# ---- applying the plan ------------------------------------------------------------------------


def test_ladder_renders_plan_rungs_with_refs():
    engine = plan_engine()
    chat = Chat()
    chat.tool("terminal", {"command": "tail app.log"}, server_log())
    chat.tool("terminal", {"command": "pytest"}, pytest_failure())
    out, count = prune_pass(engine, chat.finish())
    assert count > 0
    found = rungs(out)
    assert found, "the plan pass rewrote rows"
    for i, rung in found.items():
        assert rung in {"extract", "brief", "header", "superseded", "errors"}
        assert "tameru:" in out[i]["content"]      # refs/tag preserved


def test_facts_and_error_exemplars_flow_through_plan():
    engine = plan_engine()
    chat = Chat()
    chat.tool("terminal", {"command": "pytest"}, pytest_failure())
    out, count = prune_pass(engine, chat.finish())
    kept = rungs(out)
    assert kept, "error-bearing rows keep a rendered rung under plan"


def test_plan_failure_keeps_parent_lines():
    """If the engine's ``plan()`` raises, every row falls back to Hermes' own line."""
    from hermes_tameru_plugin import plan_pass

    engine = plan_engine()
    chat = Chat()
    chat.tool("terminal", {"command": "tail app.log"}, server_log())
    messages = chat.finish()
    original = plan_pass.engine_plan
    plan_pass.engine_plan = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        scope = _planned_scope(engine, messages)
        assert scope.plan_obj is None and scope.plan_entries == {}
    finally:
        plan_pass.engine_plan = original


def test_incumbent_path_never_plans():
    engine = make_engine()     # legacy policy
    chat = Chat()
    chat.tool("terminal", {"command": "tail app.log"}, server_log())
    assert _planned_scope(engine, chat.finish()) is None


# ---- the adaptive constants do not leak into the plan path -------------------------------------


def test_plan_code_has_no_adaptive_constants():
    """The plan policy must not reach for the adaptive 0.60 / 0.85 / 0.15 constants."""
    from hermes_tameru_plugin import plan_pass

    sources = [
        inspect.getsource(plan_pass.plan_rows),
        inspect.getsource(plan_pass.apply_plan_row),
        inspect.getsource(plan_pass.ev_strength),
        inspect.getsource(plan_pass.observe_body),
        inspect.getsource(plan_pass.step_tokens),
    ]
    src = "\n".join(sources)
    for constant in ("0.60", "0.85", "0.15"):
        assert not re.search(rf"(?<![\d.]){re.escape(constant)}(?![\d])", src), constant
    assert plan_pass.PLAN_K == 3 and plan_pass.PLAN_H == 0
