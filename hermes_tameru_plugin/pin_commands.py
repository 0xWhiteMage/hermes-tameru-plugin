"""``/tameru pin TEXT``, ``/tameru unpin N``, ``/tameru pins``: instructions kept word for word through summaries.

Same shape as the other verbs: a ``--lifecycle`` guard (Desktop always sends it), one short refusal reason, and the
fresh snapshot back. They run only on the user's own clients (see ``activity.GATED_VERBS``).
"""
from __future__ import annotations

from .posture_commands import _split_lifecycle
from .session_policy import PolicyError

PIN_HELP = "Use /tameru pin TEXT, /tameru unpin NUMBER or /tameru pins."


def pin_command(verb: str, engine, parts, *, session_id, lifecycle_id, json_mode):
    requested_life, args = _split_lifecycle(parts)
    if requested_life is not None and requested_life != lifecycle_id:
        raise PolicyError("The active session changed. Open context details again.")
    if json_mode and requested_life is None and verb != "pins":
        raise PolicyError("Refresh context details before changing anything.")
    if verb == "pins":
        if args:
            raise PolicyError(PIN_HELP)
        outcome = {"message": f"{len(engine._pins.items)} pinned." if engine._pins.items else "Nothing is pinned."}
    elif verb == "pin":
        if not args:
            raise PolicyError(PIN_HELP)
        outcome = engine.pin_change("pin", " ".join(args))
    else:
        if len(args) != 1 or not args[0].isdigit():
            raise PolicyError(PIN_HELP)
        outcome = engine.pin_change("unpin", int(args[0]))
    return {"ok": True, "kind": "pins", "verb": verb, "pin": {"verb": verb, "items": list(engine._pins.items), **outcome},
            **engine.activity_snapshot()}
