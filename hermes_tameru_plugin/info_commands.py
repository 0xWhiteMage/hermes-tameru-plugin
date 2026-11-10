"""``/tameru help``, ``/tameru stats`` and ``/tameru reset``: orientation, a numbers-only summary, and a way back to neutral.

* ``help`` lists every verb in one line each.
* ``stats`` prints counts for this chat: how much context Tameru freed (removed text, not what the chat costs), how many
  outputs it shortened, what Hermes' last reply reported about the cache. Numbers only, never text.
* ``reset`` puts this chat's self-tuning, bias, pause and Hermes-setting offsets back to neutral. It lists what it will
  clear and changes nothing until it is run again with ``confirm``. Pins and Keeps are yours and stay; ``/tameru unpin N``
  and ``/tameru unkeep REF`` remove them.
"""
from __future__ import annotations

from .progress import fmt_tokens
from .session_policy import PolicyError

HELP_LINES = (
    ("/tameru", "what Tameru did in this chat, and the context gauge"),
    ("/tameru changes", "the outputs it shortened, and where each original is"),
    ("/tameru view REF [OFFSET]", "read an original (this does not change what the model sees)"),
    ("/tameru ask REF", "text to paste into your next message so the agent re-reads an output"),
    ("/tameru keep REF | unkeep REF", "show an output in full on the next requests, or stop"),
    ("/tameru restore REF | cancel", "show an output in full for a few replies, or cancel"),
    ("/tameru pin TEXT | unpin N | pins", "keep a line of your own, word for word, through every summary"),
    ("/tameru bias save|balanced|detail", "save space or keep detail (or a number from -1 to 1)"),
    ("/tameru pause | resume", "stop or restart changes to this chat"),
    ("/tameru policy [key=value ...]", "show or set the compaction policy; `policy reset` for the defaults"),
    ("/tameru stats", "numbers for this chat: context freed, outputs shortened, cache use"),
    ("/tameru reset [confirm]", "put this chat's self-tuning back to neutral (lists what it clears first)"),
    ("/tameru reset stats [confirm]", "delete the daily stats ledger (only exists with stats_ledger: on)"),
)
HELP_FOOTER = ("Most verbs need the Desktop app, CLI or TUI; in a shared chat only `/tameru`, `changes`, `help` and `stats` work. "
               "Add --json for machine-readable output. Turn Tameru off with `enabled: false` in its settings.")
RESET_HELP = "Use /tameru reset to see what it clears, then /tameru reset confirm."


def help_text() -> str:
    width = max(len(command) for command, _ in HELP_LINES)
    return "\n".join(["Tameru - commands", "", *(f"  {command.ljust(width)}  {what}" for command, what in HELP_LINES), "", HELP_FOOTER])


def help_command(engine, parts, **_context) -> dict:
    return {"ok": True, "kind": "help", "text": help_text()}


def _pct(value) -> str:
    return f"{value}%" if isinstance(value, (int, float)) and not isinstance(value, bool) else "not reported"


def stats_text(snapshot: dict, usage: dict) -> str:
    progress = snapshot.get("progress") or {}
    savings, gauge = progress.get("savings") or {}, progress.get("gauge") or {}
    doctor = progress.get("doctor") or {}
    lines = ["Tameru - numbers for this chat", ""]
    lines.append(f"  Context freed        {fmt_tokens(savings.get('tokens'))} tokens (text removed, not what the chat costs)")
    lines.append(f"  Outputs shortened    {savings.get('shortened', 0)}   originals available {savings.get('originals', 0)}")
    lines.append(f"  Cleanups             {savings.get('commits', 0)}")
    if gauge.get("used") is not None:
        lines.append(f"  Context now          {fmt_tokens(gauge.get('used'))} of {fmt_tokens(gauge.get('limit') or gauge.get('window'))}")
    if usage.get("measured"):
        lines.append(f"  Last reply           {fmt_tokens(usage.get('prompt'))} prompt tokens, cache hit {_pct(usage.get('cache_hit_rate'))}"
                     + (f", {usage['api_ms']} ms" if usage.get("api_ms") is not None else ""))
    else:
        lines.append("  Last reply           nothing reported yet")
    net = snapshot.get("net_estimate")
    if net is not None:
        lines.append("  Net estimate         " + net)
    week = snapshot.get("ledger_week")
    if week:
        lines.append(f"  Last 7 days          {week['cleanups']} cleanups, {fmt_tokens(week['tokens'])} tokens removed, on {week['active_days']} days (all chats)")
    lazy = doctor.get("lazy_tool")
    if lazy:
        lines.append("  Recovery tool        " + ", ".join(f"{state} {count}" for state, count in lazy.items()) + " (requests)")
    if doctor:
        lines.append(f"  Tameru {doctor.get('version', '?')}        (engine {doctor.get('engine_version', '?')}) self-tuning {'on' if doctor.get('autotune') else 'off'}, "
                     f"cleanup {'active' if doctor.get('active') else 'inactive'}")
    lines.append("")
    lines.append("Context freed is not a cost figure: rewriting the cached prompt can cost more than it saves. See the README, 'When Tameru costs more'.")
    return "\n".join(lines)


def stats_command(engine, parts, **_context) -> dict:
    if parts:
        raise PolicyError("Use /tameru stats.")
    snapshot = dict(engine.activity_snapshot())
    if getattr(engine._settings, "net_estimate", False):
        from .net_estimate import describe, estimate

        with engine._activity.lifecycle_lock:
            events = list(engine._policy.cost_events)
        snapshot["net_estimate"] = describe(estimate(events, getattr(engine, "_responses", 0)))
    if getattr(engine._settings, "stats_ledger", False):
        from .stats_ledger import summary

        snapshot["ledger_week"] = summary(7)
    return {"ok": True, "kind": "stats", "text": stats_text(snapshot, engine.usage_snapshot())}


def reset_plan(engine) -> list[str]:
    """What a reset would change in this chat, as readable lines (numbers only)."""
    tuner = engine._policy.tuner
    lines = []
    offsets = tuner.offsets()
    if any(abs(value - 1.0) > 1e-9 for value in offsets):
        lines.append("self-tuning: " + ", ".join(f"{value:.2f}" for value in offsets) + " -> neutral (1.00 each)")
    if abs(tuner.bias) > 1e-9:
        lines.append(f"preference: bias {tuner.bias:+.2f} -> balanced (0)")
    if engine._policy.paused:
        lines.append("pause: paused -> running")
    host = getattr(engine, "_host", None)
    if host is not None and (host.offset or host.scale != 1.0):
        lines.append("Hermes settings: summary threshold and protected tail -> the values you configured")
    return lines


def reset_command(engine, parts, *, session_id, lifecycle_id, json_mode, **_context) -> dict:
    from .session_tuner import reset_chat

    if parts[:1] == ["stats"]:
        from .stats_ledger import purge, summary

        if parts[1:] == ["confirm"]:
            return {"ok": True, "kind": "reset", "done": True, "cleared": ["stats ledger"],
                    "text": "Stats ledger deleted." if purge() else "There was no stats ledger to delete."}
        if parts[1:]:
            raise PolicyError("Use /tameru reset stats, then /tameru reset stats confirm.")
        week = summary(90)
        return {"ok": True, "kind": "reset", "done": False, "cleared": [],
                "text": ("The stats ledger holds daily totals only (%d cleanups, %s tokens over %d days). Run /tameru reset stats confirm to delete it."
                         % (week["cleanups"], fmt_tokens(week["tokens"]), week["active_days"])) if week else "The stats ledger is empty."}
    if len(parts) > 1 or (parts and parts[0] not in ("confirm", "preview")):
        raise PolicyError(RESET_HELP)
    plan = reset_plan(engine)
    if parts[:1] == ["confirm"]:
        if json_mode:
            raise PolicyError("Refresh context details before changing preferences.")
        reset_chat(engine, session_id=session_id, lifecycle_id=lifecycle_id)
        return {"ok": True, "kind": "reset", "done": True, "cleared": plan,
                "text": "Tameru reset for this chat." + ("\n" + "\n".join(f"  {line}" for line in plan) if plan else " Nothing needed resetting.")
                + "\nPins and Keeps are unchanged (/tameru unpin N, /tameru unkeep REF)."}
    body = ("Nothing to reset: this chat is already neutral." if not plan
            else "A reset would change:\n" + "\n".join(f"  {line}" for line in plan) + "\nRun /tameru reset confirm to do it.")
    return {"ok": True, "kind": "reset", "done": False, "cleared": plan,
            "text": body + "\nPins and Keeps are not touched (/tameru unpin N, /tameru unkeep REF)."}
