"""``/tameru bias|pause|resume``: the user's two choices over the self-tuning engine."""
from __future__ import annotations

from .session_policy import PolicyError
from .session_tuner import set_posture

BIAS_WORDS = {"save": -1.0, "save_space": -1.0, "balanced": 0.0, "detail": 1.0, "preserve_detail": 1.0}
POSTURE_HELP = ("Use /tameru bias save|balanced|detail (or a number from -1 to 1), /tameru pause, "
                "or /tameru resume.")


def _split_lifecycle(parts):
    life, rest, index = None, [], 0
    while index < len(parts):
        if parts[index] == "--lifecycle":
            if life is not None or index + 1 >= len(parts) or not parts[index + 1]:
                raise PolicyError(POSTURE_HELP)
            life = parts[index + 1]
            index += 2
            continue
        rest.append(parts[index])
        index += 1
    return life, rest


def _bias(word: str) -> float:
    if word in BIAS_WORDS:
        return BIAS_WORDS[word]
    try:
        value = float(word)
    except ValueError:
        raise PolicyError(POSTURE_HELP) from None
    if value != value or not -1.0 <= value <= 1.0:
        raise PolicyError(POSTURE_HELP)
    return value


def posture_command(verb: str, engine, parts, *, session_id, lifecycle_id, json_mode):
    requested_life, args = _split_lifecycle(parts)
    if requested_life is not None and requested_life != lifecycle_id:
        raise PolicyError("The active session changed. Open context details again.")
    if json_mode and requested_life is None:
        raise PolicyError("Refresh context details before changing preferences.")
    if verb == "bias":
        if len(args) != 1:
            raise PolicyError(POSTURE_HELP)
        change = {"bias": _bias(args[0])}
    elif verb in ("pause", "resume"):
        if args:
            raise PolicyError(POSTURE_HELP)
        change = {"paused": verb == "pause"}
    else:
        raise PolicyError(POSTURE_HELP)
    set_posture(engine, session_id=session_id, lifecycle_id=requested_life or lifecycle_id, **change)
    return {"ok": True, "kind": "posture", "verb": verb, **engine.activity_snapshot()}
