"""Compression progress: one structured snapshot, one text view of it.

``build_snapshot`` is the single source of truth every front end reads: Desktop (through ``/tameru --json``),
the CLI, the Ink TUI and messaging (through ``render_text``). It holds counts, token numbers, short reason
codes and plain sentences, never transcript text, request bodies or credentials.
"""
from __future__ import annotations

import time
from typing import Any

SCHEMA = 3
PANEL_BUILD = 6      # the Desktop panel (desktop/plugin.js) carries the same number; a mismatch means one side is stale
_TIMELINE = 20

# last_check reason -> plain sentence. Anything unknown falls back to "Watching."
_DIAGNOSIS = {
    "ample_room": "Plenty of room. Nothing to clean up yet.",
    "pressure": "The chat is filling up. Tameru is cleaning up when it pays off.",
    "no_commit": "Looked for savings, but nothing was worth the cost to the prompt cache.",
    "paused": "Paused. Tameru is not changing this chat.",
    "activation_disabled": "Cleanup is switched off for this model.",
    "host_compaction": "Hermes is compacting the chat.",
    "inconsistent_input_limit": "The model's window size is unclear, so Tameru is waiting.",
    "no_input_space": "The model's window size is unclear, so Tameru is waiting.",
}


def _number(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _diagnosis(engine, policy, paused: bool) -> dict[str, str]:
    if paused:
        return {"code": "paused", "text": _DIAGNOSIS["paused"]}
    if policy.host_notice:
        return {"code": "host_passive",
                "text": "Hermes summarized the chat before Tameru acted, so Tameru is starting sooner."}
    check = policy.last_check
    if check is None:
        return {"code": "watching", "text": "Watching. No checks yet in this chat."}
    if check.kind == "committed":
        commit = policy.last_commit
        reclaimed = commit.estimated_reclaim_tokens if commit else 0
        return {"code": "committed", "text": f"Shortened old outputs and freed about {fmt_tokens(reclaimed)} tokens."}
    return {"code": check.reason or "watching", "text": _DIAGNOSIS.get(check.reason, "Watching.")}


def _latency(timeline) -> int | None:
    """Average milliseconds of Tameru's recent passes (commits and declined checks), or None before any."""
    values = [e["ms"] for e in timeline if e.get("kind") in ("commit", "decline") and type(e.get("ms")) is int]
    return round(sum(values) / len(values)) if values else None


def build_snapshot(engine) -> dict[str, Any]:
    """The compression-progress view of ``engine``. Call under its lifecycle lock."""
    policy = engine._policy
    tuner = policy.tuner
    check = policy.last_check
    plan = check.plan if check is not None else None
    budget = engine._request_budget()
    reported = getattr(engine, "last_real_prompt_tokens", None)
    used, basis = None, "none"
    if check is not None and _number(check.input_tokens) is not None:
        used, basis = check.input_tokens, "estimate"
    elif _number(reported) is not None and reported > 0:
        used, basis = reported, "reported"
    rows = engine._activity.rows()
    # Tokens freed: what this process's cleanups recorded, but never less than what the shortened rows themselves account for
    # (a chat resumed after a restart has its rows back from history and no cleanup of this process to count).
    rows_freed = sum(max(0, -(-row.before_chars // 4) - -(-row.after_chars // 4)) for row in rows if row.live)
    freed = max(int(policy.saved_tokens or 0), rows_freed)
    adjustments = [e for e in policy.events if e.get("kind") == "adjust"][-5:]
    timeline = list(policy.events)[-_TIMELINE:]
    limit = budget.usable_input_tokens
    return {
        "schema": SCHEMA,
        "state": "paused" if policy.paused else "watching",
        "gauge": {
            "used": used, "basis": basis,
            "fill_pct": round(100 * used / limit) if used is not None and _number(limit) else None,
            "cleanup_at": _number(plan.activation_tokens) if plan is not None else None,
            "host_at": _number(getattr(engine, "threshold_tokens", None)) or None,
            "limit": budget.usable_input_tokens, "window": budget.window_tokens,
        },
        "savings": {
            "tokens": freed, "commits": policy.commit_count, "latency_ms": _latency(timeline),
            "saved_pct": (round(100 * freed / limit) if freed and _number(limit) else None),
            "estimate": True,
            "shortened": sum(1 for row in rows if row.live),
            "originals": sum(1 for row in rows if row.live and engine._activity.original_check(row.ref) != "unavailable"),
        },
        "host": engine.host_snapshot() if callable(getattr(engine, "host_snapshot", None)) else None,
        "usage": engine.usage_snapshot() if callable(getattr(engine, "usage_snapshot", None)) else None,
        "diagnosis": _diagnosis(engine, policy, policy.paused),
        "tuner": {
            "enabled": bool(engine._settings.autotune), "posture": tuner.posture() if engine._settings.autotune else "fixed",
            "bias": round(tuner.bias, 2), "paused": policy.paused, "learned": bool(tuner.seeded),
            "adjustments": [{"signal": e.get("signal"), "changed": e.get("changed", []),
                             "reason": e.get("reason", ""), "t": e.get("t")} for e in adjustments],
        },
        "timeline": [dict(event, actor="hermes" if event.get("kind") == "host" else "tameru") for event in timeline],
    }


# ---- text ----------------------------------------------------------------------------------------
def fmt_tokens(value: Any) -> str:
    n = _number(value)
    if n is None:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n / 1000:.0f}K"
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(n)


def gauge_bar(gauge: dict, width: int = 24, *, ascii_only: bool = False) -> str:
    """A bar scaled to Hermes's own compaction point (the first hard limit), with Tameru's start marked."""
    fill, empty, mark = ("#", "-", "|") if ascii_only else ("█", "░", "▏")
    used, host, cleanup = gauge.get("used"), gauge.get("host_at"), gauge.get("cleanup_at")
    if used is None:
        return empty * width
    reference = next((v for v in (host, gauge.get("limit"), gauge.get("window")) if isinstance(v, int) and v > 0), 0)
    scale = max(1, reference, used)
    filled = min(width, round(used / scale * width))
    cells = [fill] * filled + [empty] * (width - filled)
    if isinstance(cleanup, int) and 0 < cleanup < scale:
        position = min(width - 1, round(cleanup / scale * width))
        cells[position] = mark
    return "".join(cells)


def _clock(t: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(t)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "--:--"


def _event_line(event: dict) -> str:
    kind = event.get("kind")
    when = _clock(event.get("t"))
    if kind == "commit":
        return f"{when}  shortened {fmt_tokens(event.get('before'))} -> {fmt_tokens(event.get('after'))} ({event.get('stage', '')})"
    if kind == "host":
        tail = "  (Tameru had not acted)" if event.get("passive") else ""
        return f"{when}  Hermes summarized {fmt_tokens(event.get('before'))} -> {fmt_tokens(event.get('after'))}{tail}"
    if kind == "adjust":
        return f"{when}  tuned: {event.get('reason', '')}"
    if kind == "decline":
        return f"{when}  checked at {fmt_tokens(event.get('tokens'))}: {_DIAGNOSIS.get(event.get('reason'), 'nothing to do')}"
    if kind == "posture":
        change = event.get("change")
        if change == "pause":
            return f"{when}  you paused Tameru"
        if change == "resume":
            return f"{when}  you resumed Tameru"
        bias = event.get("bias")
        label = ("saving space" if isinstance(bias, (int, float)) and bias < -0.33
                 else "keeping detail" if isinstance(bias, (int, float)) and bias > 0.33 else "balanced")
        return f"{when}  you set the preference to {label}"
    return f"{when}  {kind}"


def render_text(snapshot: dict, *, ascii_only: bool = False, timeline: int = 5, width: int = 24) -> str:
    """The compression-progress view as plain text, for the CLI, the TUI and messaging chats."""
    gauge, savings, tuner = snapshot["gauge"], snapshot["savings"], snapshot["tuner"]
    cleanup, host = gauge.get("cleanup_at"), gauge.get("host_at")
    marks = []
    if cleanup:
        marks.append(f"Tameru starts at {fmt_tokens(cleanup)}")
    if host:
        marks.append(f"Hermes compacts at {fmt_tokens(host)}")
    state = "paused" if snapshot["state"] == "paused" else tuner["posture"]
    lines = [f"Tameru - compression progress ({state})",
             f"Context  [{gauge_bar(gauge, width, ascii_only=ascii_only)}] {fmt_tokens(gauge.get('used'))}"
             + (f" of {fmt_tokens(gauge.get('limit') or gauge.get('window'))}" if (gauge.get('limit') or gauge.get('window')) else "")]
    if marks:
        lines.append("         " + " - ".join(marks))
    saved = (f"{fmt_tokens(savings['tokens'])} tokens of context freed - {savings['shortened']} outputs shortened - "
             f"{savings['originals']} originals available" if savings["commits"] or savings["shortened"]
             else "Nothing shortened yet")
    lines.append(f"Freed    {saved}")
    lines.append(f"Status   {snapshot['diagnosis']['text']}")
    if tuner["adjustments"]:
        lines.append(f"Tuning   {tuner['adjustments'][-1]['reason']}")
    recent = snapshot["timeline"][-timeline:] if timeline else []
    if recent:
        lines.extend(["", "Recent"] + [f"  {_event_line(event)}" for event in recent])
    return "\n".join(lines)
