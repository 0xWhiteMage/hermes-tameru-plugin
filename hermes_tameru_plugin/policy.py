"""Scalar-only planning for Tameru's adaptive cleanup.

Limits, activation and retention use separate inputs. A host compaction trigger
is a useful fallback reference, but does not establish usable request capacity.
The runtime must measure candidates and obtain a host commit before publishing
any decision as a completed change.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from .autotune import Tuning


PREFERENCES = ("save_space", "balanced", "preserve_detail")
ACTIVATIONS = ("auto", "earlier", "standard", "later")
RECENT_PROTECTIONS = ("auto", "short", "standard", "long")
POLICY_MODES = ("adaptive", "fixed", "legacy")
STAGES = ("light", "standard", "strong")


@dataclass(frozen=True)
class _Preference:
    activation: float
    detail: float
    recent: float
    minimum_reclaim_share: float
    regrowth: float


# Initial engineering policy, frozen before adaptive development comparisons.
# These are choices to evaluate, not empirically established optima.
_PREFERENCES = {
    "save_space": _Preference(0.70, 0.75, 0.80, 0.20, 0.10),
    "balanced": _Preference(0.60, 0.67, 1.00, 0.35, 0.15),
    "preserve_detail": _Preference(1.00, 2.00, 1.50, 0.50, 0.20),
}
_ACTIVATIONS = {"earlier": 0.70, "standard": 0.85, "later": 1.00}
_RECENT = {"short": 0.75, "standard": 1.00, "long": 1.50}
_DETAIL = {"light": 2.50, "standard": 1.50, "strong": 1.00}


def _row(controls: "Controls", tuning: Tuning | None) -> _Preference:
    """The numbers a plan uses: the live tuning when there is one, else the fixed row of the preference."""
    if tuning is None:
        return _PREFERENCES[controls.preference]
    return _Preference(tuning.activation, tuning.detail, tuning.recent, tuning.minimum_reclaim_share, tuning.regrowth)


def _count(value: int | None, field: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")


@dataclass(frozen=True)
class Controls:
    mode: str = "adaptive"
    preference: str = "balanced"
    activation: str = "auto"
    recent: str = "auto"

    def __post_init__(self) -> None:
        for name, choices in (
            ("mode", POLICY_MODES), ("preference", PREFERENCES),
            ("activation", ACTIVATIONS), ("recent", RECENT_PROTECTIONS),
        ):
            if getattr(self, name) not in choices:
                raise ValueError(f"{name} must be one of {', '.join(choices)}")


@dataclass(frozen=True)
class RequestBudget:
    window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    input_limit_tokens: int | None = None
    source: str = "unknown"
    estimated: bool = False

    def __post_init__(self) -> None:
        for name in ("window_tokens", "output_reserve_tokens", "input_limit_tokens"):
            _count(getattr(self, name), name, optional=True)

    @property
    def invalid_reason(self) -> str | None:
        window, net, reserve = self.window_tokens, self.input_limit_tokens, self.output_reserve_tokens
        if window is not None and net is not None:
            if net > window or (reserve is not None and net + reserve > window):
                return "inconsistent_input_limit"
        if self.usable_input_tokens == 0:
            return "no_input_space"
        return None

    @property
    def usable_input_tokens(self) -> int | None:
        if self.input_limit_tokens is not None:
            return self.input_limit_tokens
        if self.window_tokens is None or self.output_reserve_tokens is None:
            return None
        return max(0, self.window_tokens - self.output_reserve_tokens)


@dataclass(frozen=True)
class Retention:
    extract_chars: int
    brief_chars: int
    retained_chars: int
    error_chars: int
    error_row_chars: int
    recent_tokens: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _count(getattr(self, name), name)
        if self.extract_chars == 0:
            raise ValueError("extract_chars must be positive")


@dataclass(frozen=True)
class PlanningInput:
    budget: RequestBudget
    current_tokens: int
    input_complete: bool
    host_threshold_tokens: int
    retention: Retention
    minimum_reclaim_tokens: int
    minimum_result_chars: int
    recent_growth_tokens: int = 0
    proactive_trigger_override: int | None = None
    full: bool = False
    force: bool = False

    def __post_init__(self) -> None:
        for name in (
            "current_tokens", "host_threshold_tokens", "minimum_reclaim_tokens",
            "minimum_result_chars", "recent_growth_tokens",
        ):
            _count(getattr(self, name), name)
        _count(self.proactive_trigger_override, "proactive_trigger_override", optional=True)


@dataclass(frozen=True)
class Plan:
    controls: Controls
    stage: str
    consider: bool
    reason: str
    input_tokens: int
    input_complete: bool
    usable_input_tokens: int | None
    budget_source: str
    budget_estimated: bool
    pressure: float | None
    projected_pressure: float | None
    pressure_basis: str
    activation_tokens: int
    target_tokens: int
    growth_headroom_tokens: int
    urgent: bool
    retention: Retention
    minimum_result_chars: int
    minimum_reclaim_tokens: int
    minimum_reclaim_share: float
    regrowth_tokens: int

    def required_reclaim(self, invalidated_suffix_tokens: int) -> int:
        """Minimum useful reduction; suffix size is measured by the runtime."""
        _count(invalidated_suffix_tokens, "invalidated_suffix_tokens")
        return max(1, self.minimum_reclaim_tokens,
                   ceil(invalidated_suffix_tokens * self.minimum_reclaim_share))


def _pressure(current: PlanningInput) -> tuple[int | None, str | None, int, float | None, float | None]:
    limit = current.budget.usable_input_tokens
    invalid = current.budget.invalid_reason
    if invalid == "inconsistent_input_limit":
        limit = None
    reference = 0 if invalid else (limit if limit is not None else current.host_threshold_tokens)
    growth = min(current.recent_growth_tokens, int(reference * 0.10)) if reference > 0 else 0
    pressure = current.current_tokens / reference if reference > 0 else None
    projected = (current.current_tokens + growth) / reference if reference > 0 else None
    return limit, invalid, growth, pressure, projected


def _activation(controls: Controls, current: PlanningInput, host_trigger: int, limit: int | None,
                tuning: Tuning | None = None) -> int:
    ratio = _row(controls, tuning).activation if controls.activation == "auto" else _ACTIVATIONS[controls.activation]
    activation = int(host_trigger * ratio)
    if current.proactive_trigger_override is not None:
        activation = current.proactive_trigger_override
    if limit is not None and activation > 0:
        activation = min(activation, int(limit * 0.85))
    return activation


def _stages(controls: Controls, current: PlanningInput, limit: int | None, projected: float | None) -> tuple[str, ...]:
    if controls.mode == "fixed":
        return ("standard",)
    if current.force:
        return ("strong",)
    # The development matrix showed the standalone light stage never repays its
    # cache break: a shallower commit means more commits and more compactions in
    # every preference, with earlier retention-budget exhaustion on top.
    usable = STAGES[1:]
    if current.full:
        return usable
    light_ceiling, strong_floor = (0.65, 0.85) if limit is not None else (0.75, 1.00)
    if projected is None or projected < light_ceiling:
        return usable[:1]
    return usable[:2] if projected < strong_floor else usable


def _retention(controls: Controls, base: Retention, stage: str, limit: int | None,
               tuning: Tuning | None = None) -> Retention:
    preference = _row(controls, tuning)
    factor = preference.detail * _DETAIL[stage]
    recent_factor = (
        preference.recent * (1.25 if stage == "light" else 1.0)
        if controls.recent == "auto" else _RECENT[controls.recent]
    )
    recent_tokens = round(base.recent_tokens * recent_factor)
    if limit is not None:
        recent_tokens = min(recent_tokens, int(limit * 0.25))
    return Retention(
        extract_chars=max(1, round(base.extract_chars * factor)),
        brief_chars=round(base.brief_chars * factor),
        retained_chars=round(base.retained_chars * factor),
        error_chars=round(base.error_chars * factor),
        error_row_chars=round(base.error_row_chars * factor),
        recent_tokens=recent_tokens,
    )


def _reason(invalid: str | None, full: bool, activation: int, consider: bool) -> str:
    if invalid:
        return invalid
    if full:
        return "host_compaction"
    if activation == 0:
        return "activation_disabled"
    return "pressure" if consider else "ample_room"


def plans(controls: Controls, current: PlanningInput, tuning: Tuning | None = None) -> tuple[Plan, ...]:
    """Return permitted candidates, least destructive first.

    Fixed mode keeps the standard retention budget at every pressure. It shares
    activation, cache batching and request accounting with adaptive mode, so it
    can isolate the value of occupancy-dependent retention in comparisons.
    Legacy mode is implemented by the existing host/plugin policy, not here.
    ``tuning`` (the self-tuner's live numbers) replaces the preference's fixed row when given.
    """
    if controls.mode == "legacy":
        return ()
    preference = _row(controls, tuning)
    limit, invalid, growth, pressure, projected = _pressure(current)
    host_trigger = current.host_threshold_tokens or (int(limit * 0.85) if limit else 0)
    activation = _activation(controls, current, host_trigger, limit, tuning)
    consider = not invalid and (
        current.full or (activation > 0 and current.current_tokens + growth >= activation)
    )
    target = int(host_trigger * 0.85)
    if limit is not None:
        target = min(target, int(limit * 0.70)) if target else int(limit * 0.70)
    target = max(0, target - growth)
    urgent = current.full or (projected is not None and projected >= (0.95 if limit is not None else 1.00))
    reason = _reason(invalid, current.full, activation, bool(consider))
    result = []
    for stage in _stages(controls, current, limit, projected):
        result.append(Plan(
            controls=controls, stage=stage, consider=bool(consider), reason=reason,
            input_tokens=current.current_tokens, input_complete=current.input_complete,
            usable_input_tokens=limit, budget_source=current.budget.source,
            budget_estimated=current.budget.estimated, pressure=pressure,
            projected_pressure=projected,
            pressure_basis=("host_trigger" if limit is None else
                            ("estimated_input_limit" if current.budget.estimated else "input_limit")),
            activation_tokens=activation, target_tokens=target,
            growth_headroom_tokens=growth, urgent=urgent,
            retention=_retention(controls, current.retention, stage, limit, tuning),
            minimum_result_chars=current.minimum_result_chars * (2 if stage == "light" else 1),
            minimum_reclaim_tokens=current.minimum_reclaim_tokens,
            minimum_reclaim_share=0.0 if urgent else preference.minimum_reclaim_share,
            regrowth_tokens=max(1, current.minimum_reclaim_tokens, ceil(host_trigger * preference.regrowth)),
        ))
    return tuple(result)
