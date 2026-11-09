"""Pinned instructions: a few lines the user wrote, kept word for word through every Hermes summary.

A Hermes summary is written by a model, so a rule such as "never touch production config" can be softened or dropped.
A pin is the user's own text, added only by an explicit ``/tameru pin``; nothing is ever inferred from what the user or
the model said. ``merge_pins`` puts the pins into the summary under a fixed heading (replacing an earlier section, never
duplicating it), the same way the artifact trail is merged, so they are in every summary whatever the summarizer wrote.

Limits: ``MAX_PINS`` pins of at most ``PIN_CHARS`` characters, ``TOTAL_CHARS`` together. Text that holds a probable
credential is refused. The text never contains ``User asked:`` (Hermes' summary validator reads it as a claim), and
the section goes through Hermes' own compaction redactor.
"""
from __future__ import annotations

import re
from typing import Any

from .hermes_compat import redact_compaction_text
from .session_policy import PolicyError
from .tameru.compress_context import contains_secret

HEADING = "## Pinned by you (Tameru, verbatim)"
MAX_PINS = 5
PIN_CHARS = 300
TOTAL_CHARS = 1500
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ‪-‮⁦-⁩]")
_USER_ASKED = re.compile(r"\bUser\s+asked\s*:", re.IGNORECASE)
_FRAME = re.compile(r"</?\s*tameru", re.IGNORECASE)
_SECTION = re.compile(rf"^{re.escape(HEADING)}[^\n]*(?:\n|\Z).*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def clean_pin(text: Any) -> str:
    """One safe line of the user's text, or a PolicyError saying why it cannot be pinned."""
    if not isinstance(text, str):
        raise PolicyError("Write the instruction after /tameru pin.")
    line = " ".join(_CONTROL.sub(" ", text).split())
    if not line:
        raise PolicyError("Write the instruction after /tameru pin.")
    if len(line) > PIN_CHARS:
        raise PolicyError(f"Too long to pin ({PIN_CHARS} characters at most).")
    if contains_secret(line):
        raise PolicyError("That looks like it holds a secret, so it is not pinned.")
    if _FRAME.search(line):
        raise PolicyError("That cannot be pinned.")
    return _USER_ASKED.sub("User asked -", line)


class PinState:
    """The pins of one chat, oldest first."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, text: Any) -> str:
        line = clean_pin(text)
        if line in self.items:
            raise PolicyError("Already pinned.")
        if len(self.items) >= MAX_PINS:
            raise PolicyError(f"{MAX_PINS} pins at most. Unpin one first.")
        if sum(len(p) for p in self.items) + len(line) > TOTAL_CHARS:
            raise PolicyError("The pins are too long together. Unpin one first.")
        self.items.append(line)
        return line

    def drop(self, number: int) -> str:
        if not 1 <= number <= len(self.items):
            raise PolicyError("There is no pin with that number. /tameru pins lists them.")
        return self.items.pop(number - 1)

    def load(self, record: Any) -> None:
        """Restore saved pins; anything that does not clean up or does not fit is ignored."""
        self.items = []
        if not isinstance(record, list):
            return
        for text in record[:MAX_PINS]:
            try:
                self.add(text)
            except PolicyError:
                continue


def merge_pins(summary: str, state: PinState | None) -> str:
    """``summary`` with the pins as its one pinned section; unchanged when there are no pins."""
    if state is None or not state.items:
        return summary
    body = "\n".join(f"{i}. {line}" for i, line in enumerate(state.items, 1))
    section = redact_compaction_text(f"{HEADING}\n{body}\n")
    rest = _SECTION.sub("", summary).rstrip("\n")    # an earlier section (and any repeat) is replaced, never duplicated
    return (rest + "\n\n" if rest else "") + section
