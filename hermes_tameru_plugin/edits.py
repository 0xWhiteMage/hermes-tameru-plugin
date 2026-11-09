"""Keep and Restore: the user's two ways to put a shortened output back in front of the model.

Both only ever act on an output Tameru already shortened, and both work on the request that goes to the model
(``select_context``), never on the saved history. **Restore** shows the full original for the next few replies, then
lets it go. **Keep** does the same until the user unpins it, and survives a restart. A change waits for the next turn
(it is frozen at ``begin_turn``) so a batch costs the prompt cache one rebuild, not one per click.

A state is only "applied" once the request that carried it was started; until then it is "pending", and an edit that
could not be applied says why ("failed"). This module holds state only; the engine decides what is allowed.
"""
from __future__ import annotations

from typing import Any

RESTORE_TURNS = 5            # replies a Restore lasts
MAX_ENTRIES = 64             # Keeps and Restores together
HELD_SHARE = 0.15            # of Hermes' compaction point
HELD_MAX_TOKENS = 40_000
TRIP_AFTER = 3               # overlay errors before edits switch themselves off for the session
KINDS = ("keep", "restore")


def held_budget_chars(threshold_tokens: Any) -> int:
    """Characters the restored text may add to a request: 15% of Hermes' compaction point, at most 40k tokens."""
    try:
        threshold = int(threshold_tokens)
    except (TypeError, ValueError):
        threshold = 0
    tokens = min(int(threshold * HELD_SHARE), HELD_MAX_TOKENS) if threshold > 0 else HELD_MAX_TOKENS // 4
    return max(0, tokens) * 4


UNCONFIRMED = {"count": 0}   # edits whose request never got an answer (integer only, shown in the Health line)

class EditState:
    """Keeps and Restores of one chat. Copied fresh for each agent."""

    def __init__(self) -> None:
        self.turn = 0
        self.entries: dict[str, dict[str, Any]] = {}     # ref -> {kind, state, reason, delta, since, begun}
        self.errors = 0
        self.tripped = False

    # ---- changing -------------------------------------------------------------------------------
    def request(self, ref: str, kind: str, delta_chars: int) -> None:
        """Queue ``kind`` for ``ref``; it applies from the next turn. A Keep outranks a Restore of the same ref."""
        if kind not in KINDS:
            raise ValueError("kind")
        current = self.entries.get(ref)
        if current and current["kind"] == "keep" and kind == "restore":
            return
        self.entries[ref] = {"kind": kind, "state": "pending", "reason": "", "delta": max(0, int(delta_chars)),
                             "since": self.turn, "begun": None}

    def drop(self, ref: str) -> bool:
        return self.entries.pop(ref, None) is not None

    def cancel(self) -> int:
        """Drop everything that has not started yet."""
        gone = [ref for ref, entry in self.entries.items() if entry["begun"] is None]
        for ref in gone:
            del self.entries[ref]
        return len(gone)

    def load_pins(self, refs: Any) -> None:
        """Pins saved with the chat: queued again, to be re-checked when they apply."""
        if not isinstance(refs, list):
            return
        for ref in refs[:MAX_ENTRIES]:
            if isinstance(ref, str) and ref and ref not in self.entries:
                self.entries[ref] = {"kind": "keep", "state": "pending", "reason": "", "delta": 0,
                                     "since": self.turn, "begun": None}

    def pins(self) -> list[str]:
        return sorted(ref for ref, entry in self.entries.items() if entry["kind"] == "keep")[:MAX_ENTRIES]

    # ---- turns ----------------------------------------------------------------------------------
    def begin_turn(self) -> None:
        """Freeze the edits for this turn: what was queued starts now, and old Restores let go."""
        self.turn += 1
        for ref in list(self.entries):
            entry = self.entries[ref]
            if entry["begun"] is None and entry["state"] == "pending":
                entry["begun"] = self.turn
            elif entry["kind"] == "restore" and entry["begun"] is not None and self.turn - entry["begun"] >= RESTORE_TURNS:
                entry["state"], entry["reason"] = "expired", "Restore ended"
                entry["begun"] = None

    def active(self) -> dict[str, str]:
        """ref -> kind for the edits that apply to requests of this turn."""
        return {ref: entry["kind"] for ref, entry in self.entries.items()
                if entry["begun"] is not None and entry["state"] in ("pending", "applied")}

    def held_chars(self) -> int:
        return sum(entry["delta"] for entry in self.entries.values() if entry["state"] in ("pending", "applied"))

    # ---- outcome --------------------------------------------------------------------------------
    def fail(self, ref: str, reason: str) -> None:
        entry = self.entries.get(ref)
        if entry is not None:
            entry["state"], entry["reason"] = "failed", reason[:60]

    def sent(self, refs: Any) -> None:
        """The provider accepted the request that carried these edits: they are applied."""
        for ref in refs:
            entry = self.entries.get(ref)
            if entry is not None and entry["state"] == "pending":
                entry["state"] = "applied"

    def overlay_failed(self) -> None:
        self.errors += 1
        if self.errors >= TRIP_AFTER:
            self.tripped = True

    def snapshot(self) -> dict[str, Any]:
        return {ref: {"kind": entry["kind"], "state": entry["state"], "reason": entry["reason"]}
                for ref, entry in sorted(self.entries.items())}
