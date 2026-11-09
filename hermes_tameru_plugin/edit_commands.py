"""``/tameru keep|unkeep|restore|cancel``: put a shortened output back in front of the model, or let it go.

Same shape as the posture verbs: a ``--lifecycle`` guard (Desktop always sends it), one short refusal reason, and the
fresh snapshot back. They run only on the user's own clients (see ``activity.GATED_VERBS``).
"""
from __future__ import annotations

import re

from .posture_commands import _split_lifecycle
from .session_policy import PolicyError

EDIT_HELP = "Use /tameru keep REF, /tameru unkeep REF, /tameru restore REF or /tameru cancel."
_REF = re.compile(r"(?:[0-9a-f]{16}|[0-9a-f]{8})")


def edit_command(verb: str, engine, parts, *, session_id, lifecycle_id, json_mode):
    requested_life, args = _split_lifecycle(parts)
    if requested_life is not None and requested_life != lifecycle_id:
        raise PolicyError("The active session changed. Open context details again.")
    if json_mode and requested_life is None:
        raise PolicyError("Refresh context details before changing anything.")
    if verb == "cancel":
        if args:
            raise PolicyError(EDIT_HELP)
        ref = ""
    else:
        if len(args) != 1 or not _REF.fullmatch(args[0]):
            raise PolicyError(EDIT_HELP)
        ref = args[0]
    outcome = engine.edit_change(verb, ref)
    return {"ok": True, "kind": "edit", "verb": verb, "edit": {"verb": verb, "ref": ref, **outcome},
            **engine.activity_snapshot()}
