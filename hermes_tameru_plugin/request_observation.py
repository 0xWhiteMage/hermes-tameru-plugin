"""Scalar-only observations of the host's prepared main-model requests.

The hook runs after preflight. Its cap is a future estimate, never a guarantee
about the next request. Do not retain request text, credentials or route strings.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


_CAP_KEYS = ("max_tokens", "max_completion_tokens", "max_output_tokens")
_TRANSPORT_CONFIGS = ("generation_config", "generationConfig", "inferenceConfig", "config")


@dataclass(frozen=True)
class RequestObservation:
    output_reserve_tokens: int | None
    started_at: float
    source: str


def request_cap(request: Any, *, model: str | None) -> tuple[int | None, str]:
    """Read a visible unambiguous cap; an omitted or truncated cap is not zero."""
    if not isinstance(request, dict):
        return None, "missing_request"
    if request.get("_truncated") or "_truncated_items" in request:
        return None, "truncated_request"
    body = request.get("body")
    if not isinstance(body, dict):
        return None, "missing_request_body"
    if body.get("_truncated") or "_truncated_items" in body:
        return None, "truncated_request"
    if "model" in body and body["model"] != model:
        return None, "request_model_changed"
    if any(key in body for key in _TRANSPORT_CONFIGS):
        return None, "transport_specific_budget"
    extra = body.get("extra_body")
    if extra is not None:
        if not isinstance(extra, dict):
            return None, "transport_specific_budget"
        if any(key in extra for key in (*_CAP_KEYS, *_TRANSPORT_CONFIGS, "_truncated_items")):
            return None, "transport_specific_budget"
    values = [body[key] for key in _CAP_KEYS if key in body and body[key] is not None]
    if not values:
        return None, "missing_output_cap"
    if any(type(value) is not int or value < 0 for value in values):
        return None, "invalid_output_cap"
    if len(set(values)) != 1:
        return None, "ambiguous_output_cap"
    return values[0], "request_body"


def observe_request(
    engine, *, session_id: str, lifecycle_id: str, model=None, provider=None,
    base_url=None, api_mode=None, request=None, started_at=None,
) -> bool:
    """Accept only a current owner/route and an ordered host wall-clock timestamp."""
    if type(started_at) not in (int, float):
        return False
    try:
        timestamp = float(started_at)
    except (ValueError, OverflowError):
        return False
    if not math.isfinite(timestamp):
        return False
    route = {"model": model, "provider": provider, "base_url": base_url, "api_mode": api_mode}
    if any(value is not None and not isinstance(value, str) for value in route.values()):
        return False
    with engine._activity.lifecycle_lock:
        state = engine._policy
        if not engine._settings.enabled or not session_id:
            return False
        if (session_id != getattr(engine, "_session_id", "")
                or lifecycle_id != engine._activity.lifecycle_id):
            return False
        if any((value or "") != (getattr(engine, key, None) or "") for key, value in route.items()):
            return False
        # Native started_at is time.time() at the beginning of an iteration.
        # A rerouted retry carrying an older timestamp stays conservatively
        # unknown until a new iteration; it cannot resurrect a pre-reset cap.
        if timestamp < state.request_observed_after or timestamp > time.time():
            return False
        if state.last_request is not None and timestamp < state.last_request.started_at:
            return False
        reserve, source = request_cap(request, model=model)
        state.last_request = RequestObservation(reserve, timestamp, source)
        return True
