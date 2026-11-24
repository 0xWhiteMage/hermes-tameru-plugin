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

from conftest import terminal_payload
from engine_support import (
    PROTECT_LAST_N,
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


def test_plan_forecast_off_delegates_to_parent():
    """``plan_forecast=False`` reproduces the HP1 arm: proactive checkpoints delegate untouched."""
    engine = TameruContextEngine(
        settings=TameruSettings(policy_mode="plan", plan_forecast=False),
        quiet_mode=True, config_context_length=64_000,
    )
    chat = Chat()
    chat.tool("terminal", {"command": "tail app.log"}, server_log())
    messages = chat.finish()
    out, count = engine.prune_tool_results_only(messages, current_tokens=50_000)
    assert out is messages and count == 0
    assert getattr(engine, "_plan_warm_pending", []) == []


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


# ---- the forecast exception (warm-step acts) -----------------------------------------------------


def _growth(engine, step: int = 200, n: int = 8, end: int = 0) -> None:
    """Feed ``observe_body`` ``n`` steady growth samples of ``step`` tokens ending at ``end``."""
    from hermes_tameru_plugin import plan_pass

    for i in range(1, n + 1):
        plan_pass.observe_body(engine, end - step * (n - i))


def test_warm_step_commits_inside_horizon_with_runway():
    """Near the trigger (≤ H steps out) an act that leaves ≥ H + margin runway commits."""
    from hermes_tameru_plugin.hermes_compat import message_budget_tokens

    engine = plan_engine()
    chat = Chat()
    chat.tool("terminal", {"command": "tail a.log"}, server_log())
    chat.tool("terminal", {"command": "tail b.log"}, server_log(seed=2))
    messages = chat.finish()
    body = message_budget_tokens(messages)
    _growth(engine, step=200, end=body)
    engine.threshold_tokens = body + 600          # steps_to_trigger = 3 ≤ H
    out, count = engine.prune_tool_results_only(messages, current_tokens=body)
    assert out is not messages and count > 0
    assert rungs(out), "a warm act renders plan rungs"
    events = engine._telemetry.snapshot()["events"]
    assert events.get("plan_warm_commit", {}).get("count", 0) == 1


def test_warm_step_outside_horizon_declines_without_items():
    """Beyond ``steps_to_trigger > H`` the pass stands down before any row is shadowed."""
    from hermes_tameru_plugin.hermes_compat import message_budget_tokens

    engine = plan_engine()
    chat = Chat()
    chat.tool("terminal", {"command": "tail a.log"}, server_log())
    messages = chat.finish()
    body = message_budget_tokens(messages)
    _growth(engine, step=200, end=body)
    engine.threshold_tokens = body + 2_000        # steps_to_trigger = 10 > H
    out, count = engine.prune_tool_results_only(messages, current_tokens=body)
    assert out is messages and count == 0


def test_warm_step_without_runway_declines():
    """An act whose projected prompt leaves < H + margin runway stands down with no_runway."""
    from hermes_tameru_plugin.hermes_compat import message_budget_tokens

    engine = plan_engine()
    chat = Chat()
    chat.tool("terminal", {"command": "tail a.log"}, server_log())
    messages = chat.finish()
    body = message_budget_tokens(messages)
    _growth(engine, step=2_000, end=body)
    engine.threshold_tokens = body + 2_000        # inside H; the act saves < 9 steps of growth
    out, count = engine.prune_tool_results_only(messages, current_tokens=body)
    assert out is messages and count == 0


def test_warm_step_respects_hysteresis():
    from hermes_tameru_plugin.hermes_compat import message_budget_tokens

    engine = plan_engine()
    chat = Chat()
    chat.tool("terminal", {"command": "tail a.log"}, server_log())
    messages = chat.finish()
    body = message_budget_tokens(messages)
    _growth(engine, step=200, end=body)
    engine.threshold_tokens = body + 600
    engine._plan_last_act, engine._responses = 0, 1   # acted one step ago: K = 3 blocks
    out, count = engine.prune_tool_results_only(messages, current_tokens=body)
    assert out is messages and count == 0


def test_warm_act_failed_prevention_is_counted():
    """A compaction within H + margin of a warm act marks it as failed prevention."""
    from hermes_tameru_plugin import plan_pass

    engine = plan_engine()
    engine._responses = 10
    engine._plan_warm_pending = [9, 1]           # 1 step ago: fails; 9 steps ago: at the boundary
    plan_pass.note_compaction(engine)
    assert engine._telemetry.snapshot()["events"]["plan_warm_act_failed"]["count"] == 2
    assert engine._plan_warm_pending == []


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
        inspect.getsource(plan_pass.apply_deep_tail),
        inspect.getsource(plan_pass.ev_strength),
        inspect.getsource(plan_pass.observe_body),
        inspect.getsource(plan_pass.step_tokens),
        inspect.getsource(plan_pass.warm_prune),
        inspect.getsource(plan_pass.mean_step_tokens),
        inspect.getsource(plan_pass.note_compaction),
        inspect.getsource(plan_pass.replace_compaction),
    ]
    src = "\n".join(sources)
    for constant in ("0.60", "0.85", "0.15"):
        assert not re.search(rf"(?<![\d.]){re.escape(constant)}(?![\d])", src), constant
    assert plan_pass.PLAN_K == 3 and plan_pass.PLAN_H == 0
    assert plan_pass.PLAN_WARM_H == 3 and plan_pass.PLAN_MARGIN_STEPS == 6


# ---- plan_deep_tail (HP4) ----------------------------------------------------------------------


def _deep_tail_fixture(engine):
    """Middle row + PROTECT_LAST_N tail rows; the trailing round is the pending (most recent) step.

    Layout (PROTECT_LAST_N = 4): [sys, user, A, T0, A, T1, A, T2, A, T3, A, T4] — boundary = 8,
    the tail is A/T3 and A/T4, and T4's round is the unread pending round Hermes itself spares.
    """
    chat = Chat()
    chat.tool("read_file", {"path": "svc.py"}, source_file())
    for i in range(PROTECT_LAST_N + 2):
        chat.tool("terminal", {"command": f"tail app{i}.log"}, server_log(seed=10 + i))
    messages = chat.messages
    boundary = len(messages) - PROTECT_LAST_N
    pending_tool_idx = len(messages) - 1
    return messages, boundary, pending_tool_idx


def test_plan_deep_tail_parsing():
    assert TameruSettings().plan_deep_tail is False
    parse = _SETTING_PARSERS["plan_deep_tail"]
    assert parse("on") is True and parse("off") is False


def test_deep_tail_items_cover_tail_except_pending():
    """Under ``plan_deep_tail`` the plan's demotable set extends past the prune boundary into the
    protected tail, stopping before the most recent step's pending round."""
    engine = make_engine(TameruSettings(
        small_window_threshold_tokens=0, policy_mode="plan", plan_deep_tail=True))
    messages, boundary, pending_tool_idx = _deep_tail_fixture(engine)
    scope = _planned_scope(engine, messages, full=True)
    assert scope is not None and scope.plan_input is not None
    demoted = {int(i.id) for i in scope.plan_input.items if i.stock_action == "demote"}
    tail_tool = [i for i in range(boundary, pending_tool_idx)
                 if messages[i].get("role") == "tool"]
    assert tail_tool, "fixture has a tail tool row before the pending round"
    for i in tail_tool:
        assert i in demoted, f"tail row {i} should be a demote item"
    assert pending_tool_idx not in demoted, "the pending round stays protected"
    assert scope.deep_tail_end == pending_tool_idx - 1   # spared.start = owning assistant row


def test_deep_tail_commits_tail_rungs():
    """The tail rows' plan rungs are committed after pass 2 (the seam never visits them)."""
    engine = make_engine(TameruSettings(
        small_window_threshold_tokens=0, policy_mode="plan", plan_deep_tail=True))
    messages, boundary, pending_tool_idx = _deep_tail_fixture(engine)
    original_pending = messages[pending_tool_idx]["content"]
    out, count = prune_pass(engine, messages, full=True)
    found = rungs(out)
    tail_rungs = {i: r for i, r in found.items() if i >= boundary}
    assert tail_rungs, "tail rows carry plan rungs inside the compaction pass"
    assert pending_tool_idx not in found
    assert out[pending_tool_idx]["content"] == original_pending


def test_deep_tail_off_keeps_tail_verbatim():
    """Without ``plan_deep_tail`` a full pass still leaves the protected tail untouched."""
    engine = plan_engine()
    messages, boundary, pending_tool_idx = _deep_tail_fixture(engine)
    tail_contents = [messages[i].get("content") for i in range(boundary, len(messages))]
    out, count = prune_pass(engine, messages, full=True)
    assert [m.get("content") for m in out[boundary:]] == tail_contents


# ---- plan_native_warm_prunes (HP5) ---------------------------------------------------------------


def test_plan_native_warm_prunes_parsing():
    assert TameruSettings().plan_native_warm_prunes is True
    assert _SETTING_PARSERS["plan_native_warm_prunes"]("off") is False


def test_native_warm_prunes_off_declines_the_per_request_prune(monkeypatch):
    """``plan_native_warm_prunes`` off: the per-request proactive prune returns Hermes' no-op
    contract ``(messages, 0)``, and the flow's next step — ``compress`` over threshold — still
    compacts."""
    from engine_support import fake_summary_llm

    engine = make_engine(TameruSettings(
        small_window_threshold_tokens=0, policy_mode="plan", plan_forecast=False,
        plan_deep_tail=True, plan_native_warm_prunes=False))
    chat = Chat()
    for i in range(4):
        chat.tool("terminal", {"command": f"tail -n {100 + i} app.log"}, server_log(i))
    messages = chat.finish("what failed?")
    fake_summary_llm(monkeypatch)
    out, count = engine.prune_tool_results_only(messages, current_tokens=200_000)
    assert count == 0 and out is messages, "the warm prune is declined with Hermes' no-op shape"
    candidate = engine.compress(messages, 200_000, force=True)
    assert isinstance(candidate, list) and candidate is not messages, "the trigger still compacts"


def test_native_warm_prunes_default_on_delegates_to_parent():
    """Default ``plan_native_warm_prunes=True`` is today's behaviour: the per-request checkpoint
    delegates to Hermes' native prune (the HP1-HP4 arms)."""
    engine = TameruContextEngine(
        settings=TameruSettings(policy_mode="plan", plan_forecast=False),
        quiet_mode=True, config_context_length=64_000,
    )
    chat = Chat()
    chat.tool("terminal", {"command": "tail app.log"}, server_log())
    messages = chat.finish()
    out, count = engine.prune_tool_results_only(messages, current_tokens=50_000)
    assert out is messages and count == 0   # native gate decides; not a blanket decline
