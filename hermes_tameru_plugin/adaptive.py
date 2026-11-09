"""Adaptive proactive cleanup runtime: policy plans, staged candidates and commit gates.

The mixin owns the adaptive/fixed proactive driver: one immutable plan tuple per public
attempt, one staged candidate per permitted ``Plan`` inside the host's own prune seam, one
native public commit attempt. Rejected candidates publish nothing. Legacy mode keeps the
existing host/plugin policy and reuses only the scope-sensitive native gates here.
"""
from __future__ import annotations

import inspect
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

from .autotune import Tuner, Tuning
from .config import TameruSettings, scaled_error_budget, scaled_retained_budget
from .hermes_compat import message_budget_tokens
from .request_observation import RequestObservation, observe_request
from .policy import Controls, Plan, PlanningInput, RequestBudget, Retention, plans


_GROWTH_SAMPLES = 4
_EVENTS = 40
_QUIET_REASONS = {"ample_room", "not_considered", "activation_disabled"}
_WIRE_KEYS = ("role", "content", "tool_call_id", "tool_calls", "name")


def _positive_native(value: Any) -> int:
    """Preserve a larger native reservation, including its accepted numeric strings."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def native_budget_kwargs(settings: TameruSettings, values: dict, supported: set[str]) -> dict:
    result = dict(values)
    if not settings.enabled:
        return result
    reserve = settings.output_reserve_tokens
    if reserve is not None and "max_tokens" in supported:
        result["max_tokens"] = max(reserve, _positive_native(result.get("max_tokens")))
    limit = settings.input_budget_tokens
    if limit is not None and "threshold_tokens_cap" in supported:
        ceiling = max(1, int(limit * 0.85))
        prior = _positive_native(result.get("threshold_tokens_cap"))
        result["threshold_tokens_cap"] = min(ceiling, prior) if prior else ceiling
    return result


@dataclass
class _Check:
    kind: str                       # "attempt" | "decline" | "committed"
    input_tokens: int
    body_tokens: int
    reason: str = ""
    plan: Plan | None = None


@dataclass
class _Commit:
    stage: str
    estimated_reclaim_tokens: int
    plan: Plan
    method: str = "proactive"


class PolicyRuntime:
    """Observed policy state: controls plus a small bounded growth history.

    Contains scalars only — never transcript text, request bodies, credentials or routes.
    A copy is a fresh runtime over the current controls, so a clone inherits the choices
    as new defaults but none of the observations or commit history.
    """

    def __init__(self, controls: Controls) -> None:
        self.defaults = controls
        self.controls = controls
        self.has_override = False
        self.controls_source = 'default'
        self.controls_issue: str | None = None
        self.growth: deque[int] = deque(maxlen=_GROWTH_SAMPLES)
        self.last_body_tokens: int | None = None
        self.last_check: _Check | None = None
        self.last_commit: _Commit | None = None
        self.last_request: RequestObservation | None = None
        self.request_observed_after = time.time()
        self.tuner = Tuner()
        self.paused = False
        self.decline_streak = 0             # consecutive "considered, nothing to reclaim" checks
        self.commits_since_host = 0         # Tameru commits since Hermes last compacted
        self.checks_since_commit = 99       # policy checks since Tameru's last commit
        self.pass_started: float | None = None   # perf_counter() of the current proactive pass
        self.events: deque[dict] = deque(maxlen=_EVENTS)    # scalars + short reason codes, never text
        self.saved_tokens = 0               # cumulative reclaim of this session's Tameru commits
        self.commit_count = 0
        self.host_notice = False            # Hermes compacted ahead of Tameru; cleared by the next check

    def _fresh(self) -> "PolicyRuntime":
        """A clone inherits the choices (controls, bias, pause) as new defaults, none of the observations."""
        fresh = type(self)(self.controls)
        fresh.tuner.set_bias(self.tuner.bias)
        fresh.paused = self.paused
        return fresh

    def __copy__(self) -> "PolicyRuntime":
        return self._fresh()

    def __deepcopy__(self, memo: dict) -> "PolicyRuntime":
        return self._fresh()


def _owned_scope(engine: Any) -> Any:
    """The current pass scope when it belongs to ``engine``, else None."""
    from .engine import _SCOPE                          # late: facade/sibling, avoids the cycle

    scope = _SCOPE.get()
    if (scope is None or scope.owner is not engine
            or not getattr(engine._transactions, "active", False)):
        return None
    return scope if scope.serial == engine._transactions.serial else None


def _scoped_native(name: str, default: int):
    """A property that serves a per-pass override while an owned scope carries ``native_options``."""

    raw = "_tameru_native_" + name

    def fget(self):
        scope = _owned_scope(self)
        if scope is not None and name in scope.native_options:
            return scope.native_options[name]
        return self.__dict__.get(raw, default)

    def fset(self, value):
        self.__dict__[raw] = value

    return property(fget, fset)


class AdaptiveMixin:
    """Scope-aware native gates and the adaptive/fixed proactive driver."""

    proactive_prune_tokens = _scoped_native("proactive_prune_tokens", 0)
    proactive_prune_min_reclaim_tokens = _scoped_native("proactive_prune_min_reclaim_tokens", 0)
    proactive_prune_min_result_chars = _scoped_native("proactive_prune_min_result_chars", 8000)

    def _raw_native(self, name: str, default: int = 0) -> Any:
        return self.__dict__.get("_tameru_native_" + name, default)

    @property
    def tail_token_budget(self):
        scope = _owned_scope(self)
        if (scope is not None and scope.plan is not None
                and self._uses_token_tail(scope.plan.controls, full=scope.full)):
            return scope.plan.retention.recent_tokens
        return self._raw_tail_budget()

    def observe_api_request(self, **kwargs) -> bool:
        return observe_request(self, **kwargs)

    def set_policy_controls(self, updates, *, session_id, lifecycle_id, reset=False):
        from .session_policy import set_controls
        return set_controls(self, updates, session_id=session_id,
                            lifecycle_id=lifecycle_id, reset=reset)

    def _uses_token_tail(self, controls: Controls, *, full: bool = False) -> bool:
        if full:
            return True
        if not (self._settings.enabled and self._seams.demote and self._token_tail):
            return False
        return (self._settings.prune_tail == 'tokens'
                or (controls.mode != 'legacy' and controls.recent != 'auto'))

    def _raw_tail_budget(self):
        base_prop = self._native_tail_property()
        if isinstance(base_prop, property) and base_prop.fget is not None:
            return base_prop.fget(self)
        return self.__dict__.get("_tameru_native_tail_token_budget")

    @tail_token_budget.setter
    def tail_token_budget(self, value):
        base_prop = self._native_tail_property()
        if isinstance(base_prop, property) and base_prop.fset is not None:
            base_prop.fset(self, value)
        else:
            self.__dict__["_tameru_native_tail_token_budget"] = value

    # ---- controls --------------------------------------------------------------
    def _init_policy(self, settings: TameruSettings) -> None:
        self._policy = PolicyRuntime(Controls(
            mode=settings.policy_mode, preference=settings.preference,
            activation=settings.activation, recent=settings.recent_protection,
        ))

    def _discard_policy_views(self, *, preserve_controls: bool = True) -> None:
        """Fresh runtime: reset leaks no observations; an unrelated bind also drops choices."""
        old = self._policy
        fresh = PolicyRuntime(old.defaults)
        if preserve_controls:
            fresh.tuner.set_bias(old.tuner.bias)
            fresh.paused = old.paused
            fresh.controls = old.controls
            fresh.has_override = old.has_override
            fresh.controls_source = old.controls_source
            fresh.controls_issue = old.controls_issue
        self._policy = fresh

    def _event(self, kind: str, **fields) -> None:
        """Append one scalar-only timeline entry. Call under the lifecycle lock."""
        self._policy.events.append({"t": round(time.time(), 1), "kind": kind, **fields})

    def _signal(self, name: str, count: int = 1) -> None:
        """Feed the self-tuner one signal and log any adjustment it made. Call under the lifecycle lock."""
        if not self._settings.autotune or self._policy.paused:
            return
        adjustment = self._policy.tuner.signal(name, count)
        if adjustment is not None:
            self._event("adjust", signal=name, changed=list(adjustment.changed), reason=adjustment.reason)
            from . import learned
            from .session_tuner import save                 # late: sibling imports this module's package
            save(self)
            learned.record(self._policy.tuner.offsets())

    def observe_host_compaction(self, *, session_id: str = "", kind: str = "", reason: str = "",
                                before_tokens=None, after_tokens=None, **_ignored) -> bool:
        """``post_compaction``: Hermes compacted. Returns True when Tameru had done nothing since the last one.

        A compaction Tameru did not precede means cleanup was too late or too weak, so the tuner is told.
        """
        def _count(value):
            return value if type(value) is int and value >= 0 else None
        with self._activity.lifecycle_lock:
            passive = self._policy.commits_since_host == 0
            self._policy.commits_since_host = 0
            self._manifest.reset_last()      # a Hermes summary may have dropped the earlier notes
            self._host.note_host_summary()   # the threshold was not early enough: do not push it later for a while
            self._policy.decline_streak = 0
            self._event("host", before=_count(before_tokens), after=_count(after_tokens),
                        compaction=str(kind)[:24], passive=passive)
            self._policy.host_notice = passive
            if passive:
                self._signal("host_summarized")
        return passive

    def _tuning(self) -> Tuning | None:
        """The self-tuner's live numbers, or None when autotune is off (the preference's fixed row applies)."""
        return self._policy.tuner.tuning() if self._settings.autotune else None

    def _clear_policy_observations(self) -> None:
        """Model/window change: drop estimates, never an in-flight plan's validity."""
        with self._activity.lifecycle_lock:
            self._policy.growth.clear()
            self._policy.last_body_tokens = None
            self._policy.last_request = None
            self._policy.request_observed_after = time.time()
            self._policy.last_check = None

    def _model_update_budget(self, bound: inspect.BoundArguments) -> None:
        """Apply the standing reserve without replacing an unspecified host reservation."""
        reserve = self._settings.output_reserve_tokens
        if not self._settings.enabled or reserve is None or "max_tokens" not in bound.signature.parameters:
            return
        requested = bound.arguments.get("max_tokens")
        if requested is None:
            requested = getattr(self, "max_tokens", None)
        bound.arguments["max_tokens"] = max(reserve, _positive_native(requested))

    # ---- budget ----------------------------------------------------------------
    def _request_budget(self) -> RequestBudget:
        settings = self._settings
        input_limit = settings.input_budget_tokens if settings.enabled else None
        configured_reserve = settings.output_reserve_tokens if settings.enabled else None
        native_reserve = getattr(self, "max_tokens", None)
        if type(native_reserve) is not int or native_reserve <= 0:
            declared = self.__dict__.get("_tameru_native_max_tokens")
            native_reserve = 0 if type(declared) is int and declared == 0 else None
        if configured_reserve is not None and (
            native_reserve is None or configured_reserve >= native_reserve
        ):
            reserve, source = configured_reserve, "configured_reserve"
        else:
            reserve = native_reserve
            source = "host" if native_reserve is not None else "unknown"
        estimated = False
        if settings.enabled and input_limit is None and reserve is None:
            observation = self._policy.last_request
            if observation is not None and observation.output_reserve_tokens is not None:
                reserve, source, estimated = observation.output_reserve_tokens, 'previous_request', True
        return RequestBudget(
            window_tokens=self._window_tokens(), input_limit_tokens=input_limit,
            output_reserve_tokens=reserve,
            source="configured_input" if input_limit is not None else source,
            estimated=estimated)

    def _window_tokens(self) -> int | None:
        if getattr(self, "_placeholder_context", False):
            return None
        length = getattr(self, "context_length", None)
        return length if type(length) is int and length > 0 else None

    # ---- planning input ----------------------------------------------------------
    def _planning_input(self, messages: list[dict], current_tokens: Any,
                        *, body_tokens: int | None = None,
                        full: bool = False, force: bool = False) -> PlanningInput:
        settings = self._settings
        body = message_budget_tokens(messages) if body_tokens is None else body_tokens
        complete = type(current_tokens) is int and current_tokens >= 0
        measured = current_tokens if complete else body
        base = Retention(
            extract_chars=settings.max_extract_chars,
            brief_chars=settings.brief_chars,
            retained_chars=scaled_retained_budget(settings, getattr(self, "threshold_tokens", 0)),
            error_chars=scaled_error_budget(settings, getattr(self, "threshold_tokens", 0)),
            error_row_chars=settings.errors_row_chars,
            recent_tokens=self._raw_tail_budget() or 0,
        )
        with self._activity.lifecycle_lock:
            growth = max(self._policy.growth, default=0)
        override = self._raw_native("proactive_prune_tokens") if not getattr(
            self, "_scaled_prune", False) else None
        return PlanningInput(
            budget=self._request_budget(), current_tokens=measured,
            input_complete=complete,
            host_threshold_tokens=int(getattr(self, "threshold_tokens", 0) or 0),
            retention=base,
            minimum_reclaim_tokens=self._raw_native("proactive_prune_min_reclaim_tokens"),
            minimum_result_chars=self._raw_native("proactive_prune_min_result_chars", 8000),
            recent_growth_tokens=growth,
            proactive_trigger_override=override, full=full, force=force,
        )

    # ---- the proactive driver -----------------------------------------------------
    def _adaptive_prune(self, messages, current_tokens=None, *args, **kwargs):
        """One plan tuple, one staged candidate per permitted plan, one native commit attempt."""
        from .engine import _SCOPE                          # late: facade/sibling

        parent = super().prune_tool_results_only
        scope = self._new_scope(messages)
        token = _SCOPE.set(scope)
        started = time.perf_counter()
        self._policy.pass_started = started
        try:
            try:
                body = message_budget_tokens(messages)
                with self._activity.lifecycle_lock:
                    if not self._pass_is_current(scope):
                        return messages, 0
                    self._observe_policy_body(body)
                    planning = self._planning_input(messages, current_tokens, body_tokens=body)
                    scope.policy_plans = () if self._policy.paused else plans(
                        self._policy.controls, planning, self._tuning())
                    if self._policy.tuner.tick() and self._settings.autotune:
                        from . import learned                     # calm time eases what earlier chats taught
                        learned.relax()
                    self._policy.checks_since_commit += 1
            except Exception:
                self._telemetry.count("error", where="policy_plan")
                return messages, 0
            first = scope.policy_plans[0] if scope.policy_plans else None
            if first is None or not first.consider or not self._pass_is_current(scope):
                self._record_check("decline", planning, body,
                                   "paused" if self._policy.paused else (first.reason if first else "not_considered"),
                                   scope, first)
                return messages, 0
            self._record_check("attempt", planning, body, first.reason, scope, first)
            # Activation uses the full request above; native persistence must price
            # its next runway with the smaller, independently chosen regrowth floor.
            scope.native_options["proactive_prune_tokens"] = first.regrowth_tokens
            with self._telemetry.hold() as held:
                res = parent(messages, planning.current_tokens, *args, **kwargs)
            final_scope = _SCOPE.get()
            if res[0] is messages or res[1] <= 0:
                self._record_check("decline", planning, body, "no_commit", scope, first)
                return messages, 0
            count = 0
            if final_scope is not None:
                with self._activity.lifecycle_lock:
                    if self._publish_pass_state(final_scope):
                        held.apply()
                        count = self._activity.confirm(final_scope.changes.values(), res[0])
                        self._activity.refresh(res[0])
                self._activity_registry.notify(self, count)
            committed = getattr(final_scope, "plan", None) or first
            self._arm_regrowth(res[0], getattr(committed, "regrowth_tokens", 0))
            return res
        finally:
            self._close_pass(scope)
            _SCOPE.reset(token)
            self._telemetry.time(time.perf_counter() - started)

    def _observe_policy_body(self, body: int) -> None:
        """Called once under the lifecycle lock, before choosing this attempt's plans."""
        last = self._policy.last_body_tokens
        if last is not None and body > last:
            self._policy.growth.append(body - last)
            commit = self._policy.last_commit
            if (commit is not None and self._policy.checks_since_commit <= 2
                    and body - last >= commit.plan.regrowth_tokens):
                self._signal("fast_regrowth")
        elif last is not None and body < last:
            self._policy.growth.clear()
        self._policy.last_body_tokens = body

    def _pass_ms(self) -> int | None:
        """Milliseconds since the current proactive pass began, or None outside one."""
        started = self._policy.pass_started
        return None if started is None else max(0, round((time.perf_counter() - started) * 1000))

    def _record_check(self, kind: str, planning: PlanningInput, body: int, reason: str,
                      anchor, plan: Plan | None) -> None:
        with self._activity.lifecycle_lock:
            if not self._pass_is_current(anchor):
                return
            self._policy.last_check = _Check(kind, planning.current_tokens, body, reason, plan)
            self._policy.host_notice = False
            if kind == "decline" and reason == "no_commit":
                self._policy.decline_streak += 1
                if self._policy.decline_streak >= 3:
                    self._policy.decline_streak = 0
                    self._signal("reclaim_declined")
            elif kind == "attempt":
                pass                                  # the outcome decides the streak
            else:
                self._policy.decline_streak = 0
            if kind == "decline" and reason not in _QUIET_REASONS:
                self._event("decline", tokens=planning.current_tokens, reason=reason,
                            mode=self._policy.tuner.mode(), ms=self._pass_ms())

    # ---- candidate stages ------------------------------------------------------------
    def _adaptive_candidates(self, native_pruner, messages, protect_tail_count,
                           protect_tail_tokens=None, *args, **kwargs):
        """Run the native private prune once per permitted plan; select the staged winner."""
        from .engine import _SCOPE                          # late: facade/sibling

        base_scope = _SCOPE.get()
        permitted = base_scope.policy_plans if base_scope is not None else ()
        original_body = message_budget_tokens(messages)
        sig = inspect.signature(native_pruner)

        def bound(plan):
            call = sig.bind(messages, protect_tail_count, protect_tail_tokens, *args, **kwargs)
            tail = (plan.retention.recent_tokens
                    if self._uses_token_tail(plan.controls, full=base_scope.full) else None)
            for key, value in (("protect_tail_tokens", tail),
                               ("min_prune_chars", plan.minimum_result_chars)):
                if key in sig.parameters:
                    call.arguments[key] = value
            return call.args, call.kwargs

        urgent = permitted[0].urgent if permitted else False
        last_qualifying = None
        for index, plan in enumerate(permitted):
            if not self._pass_is_current(base_scope):
                return messages, 0                       # lifecycle changed mid-plan: discard
            stage_scope = self._candidate_scope(base_scope, plan=plan)
            if stage_scope is None:
                return messages, 0
            _SCOPE.set(stage_scope)
            stage_scope.native_options["proactive_prune_min_reclaim_tokens"] = plan.minimum_reclaim_tokens
            stage_scope.native_options["proactive_prune_min_result_chars"] = plan.minimum_result_chars
            stage_scope.native_options["proactive_prune_tokens"] = plan.regrowth_tokens
            stage_scope.policy_before_tokens = original_body
            stage_scope.policy_stage_index = index
            call_args, call_kwargs = bound(plan)
            with self._telemetry.hold() as held:
                candidate, n = native_pruner(*call_args, **call_kwargs)
            with self._activity.lifecycle_lock:
                if not self._pass_is_current(base_scope):
                    _SCOPE.set(base_scope)
                    return messages, 0
            if n <= 0 or candidate is messages:
                continue
            after = message_budget_tokens(candidate)
            gain = original_body - after
            first_changed = next(
                (i for i, (old, new) in enumerate(zip(messages, candidate))
                 if old is not new and any(old.get(k) != new.get(k) for k in _WIRE_KEYS)),
                len(candidate))
            suffix = message_budget_tokens(messages[first_changed:])
            required = plan.required_reclaim(suffix)
            stage_scope.native_options["proactive_prune_min_reclaim_tokens"] = required
            stage_scope.policy_after_tokens = after
            if gain < required:
                continue
            entry = (stage_scope, candidate, n, held)
            if not urgent:
                return self._select_policy_stage(base_scope, entry)
            estimated = max(after, plan.input_tokens - gain)
            if estimated <= plan.target_tokens:
                return self._select_policy_stage(base_scope, entry)
            last_qualifying = entry
        if last_qualifying is None:
            _SCOPE.set(base_scope)
            return messages, 0
        return self._select_policy_stage(base_scope, last_qualifying)

    def _select_policy_stage(self, anchor, entry):
        from .engine import _SCOPE

        with self._activity.lifecycle_lock:
            if not self._pass_is_current(anchor):
                _SCOPE.set(anchor)
                return anchor.messages, 0
            scope, candidate, count, held = entry
            _SCOPE.set(scope)
            held.apply()
            return candidate, count

    def _record_commit(self, scope) -> None:
        """Record committed policy metadata and rebase body growth at the published body count."""
        plan = scope.plan
        after = scope.policy_after_tokens if scope.policy_after_tokens is not None else scope.policy_before_tokens
        reclaim = scope.policy_before_tokens - after if scope.policy_after_tokens is not None else 0
        method = 'local_full' if scope.full_local else ('full' if scope.full else 'proactive')
        with self._activity.lifecycle_lock:
            self._policy.last_commit = _Commit(plan.stage, reclaim, plan, method)
            self._policy.last_check = _Check(
                "committed", plan.input_tokens, scope.policy_before_tokens, plan.reason, plan)
            self._policy.growth.clear()          # the committed body is the new baseline
            self._policy.last_body_tokens = after
            self._policy.decline_streak = 0
            self._policy.commits_since_host += 1
            self._policy.checks_since_commit = 0
            self._policy.host_notice = False
            self._policy.saved_tokens += max(0, reclaim)
            self._policy.commit_count += 1
            changes = list(getattr(scope, "changes", {}).values())[:8]
            refs = [c.ref for c in changes if isinstance(getattr(c, "ref", None), str)]
            tools = []
            for c in changes:
                name = getattr(c, "tool", None)
                if isinstance(name, str) and name and name[:32] not in tools and len(tools) < 4:
                    tools.append(name[:32])
            self._event("commit", before=scope.policy_before_tokens, after=after, stage=plan.stage,
                        method=method, mode=self._policy.tuner.mode(), refs=refs, tools=tools,
                        ms=self._pass_ms(), parent_lines=int(getattr(scope, "parent_lines", 0) or 0))
            if scope.policy_stage_index > 0:
                self._telemetry.count("escalation")

    # ---- status ----------------------------------------------------------------------
    def get_policy_status(self) -> dict:
        with self._activity.lifecycle_lock:
            check = self._policy.last_check
            commit = self._policy.last_commit
            token_tail = self._uses_token_tail(self._policy.controls)
            budget = self._request_budget()
            last_reported = getattr(self, "last_real_prompt_tokens", None)
            return {
                "controls": {
                    "mode": self._policy.controls.mode,
                    "preference": self._policy.controls.preference,
                    "activation": self._policy.controls.activation,
                    "recent": self._policy.controls.recent,
                },
                "budget": ({**asdict(budget), "usable_input_tokens": budget.usable_input_tokens,
                            "invalid_reason": budget.invalid_reason}
                           if hasattr(self, "_settings") else {}),
                "last_check": asdict(check) if check else None,
                "last_commit": ({"stage": commit.stage,
                                 "estimated_reclaim_tokens": commit.estimated_reclaim_tokens,
                                 "method": commit.method,
                                 "recent_basis": ("tokens" if self._uses_token_tail(
                                     commit.plan.controls, full=commit.method != "proactive")
                                     else "messages"),
                                 "plan": asdict(commit.plan)} if commit else None),
                "rearm_body_tokens": getattr(self, "_proactive_prune_rearm_tokens", None),
                "proactive_recent_basis": "tokens" if token_tail else "messages",
                "proactive_recent_messages": None if token_tail else self.protect_last_n,
                "last_request": (asdict(self._policy.last_request)
                                 if self._policy.last_request is not None else None),
                "controls_overridden": self._policy.has_override,
                "controls_source": self._policy.controls_source,
                "controls_issue": self._policy.controls_issue,
                "controls_available": bool(self._settings.enabled and self._seams.demote),
                "last_reported_input_tokens": (last_reported if type(last_reported) is int
                                               and last_reported > 0 else None),
            }
