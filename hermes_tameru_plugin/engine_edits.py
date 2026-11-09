"""Keep and Restore: the per-turn request overlay, the edit commands and pinned instructions.

Methods of ``TameruContextEngine`` kept in their own module so the engine class stays readable; they use the
engine's ``self`` state and are mixed in verbatim.
"""
from __future__ import annotations

from .quiet import swallowed
from typing import Any

from .activity import (
    LABELS as ACTIVITY_LABELS,
    ActivityState,
    Change,
    SessionRegistry,
    digest as activity_digest,
    line_key,
    message_signatures,
    context_state,
    same_occurrence,
    proposed_change,
)
from .render import (
    HEADER_MAX_LEN,
    TAMERU_TAG,
    RenderStats,
    build_header,
    classify_render,
    header_only,
    header_parent,
    make_ref,
    make_ref_from_digest,
    omitted_categories,
    parse_header,
    render,
    retarget_rung,
    true_line_count,
    for_summary,
)
from .session_policy import PolicyError
from .recovery import (
    EXPAND_SCHEMA,
    EXPAND_TOOL_NAME,
    READ_ORIGINAL_SCHEMA,
    READ_ORIGINAL_TOOL_NAME,
    SECRET_SOURCE,
    OriginalStore,
    expand,
    lookup_original,
    screen_original,
    session_lookup_factory,
)
from .edits import MAX_ENTRIES as _EDIT_MAX, UNCONFIRMED, EditState, held_budget_chars
from .hermes_compat import (
    LEAN_STUB_RE,
    SKILL_PRUNED_MARKER_PREFIX,
    SeamReport,
    compression_threshold,
    drop_stale_api_content,
    elision_marker,
    has_untrusted_wrapper,
    is_persisted_block,
    is_untrusted_tool,
    marker_artifact_re,
    message_budget_tokens,
    probe_seams,
    refused_summary,
    session_db_usable,
    summarize_tool_result,
    tool_calls_by_id,
)


class EditsMixin:
    def edits_enabled(self) -> bool:
        """Edits are available: switched on, not tripped by repeated errors, and this Hermes has the request seam."""
        return (bool(self._settings.edits) and not self._edits.tripped
                and callable(getattr(super(), "select_context", None)))

    def begin_turn_edits(self) -> None:
        """Freeze what the user queued: it applies from this turn on (called when a turn starts)."""
        try:
            with self._activity.lifecycle_lock:
                if self._edits_staged:                               # a turn ended without the provider answering
                    UNCONFIRMED["count"] += sum(len(refs) for refs in self._edits_staged.values())
                    self._edits_staged.clear()
                self._edits.begin_turn()
        except Exception:
            swallowed(self, "begin_turn_edits")

    def stage_edits(self, api_request_id: str = "", request_messages=None) -> None:
        """A provider request just started. Check that what the overlay put in is really in the request Hermes built,
        and hold those edits (not applied yet) until the provider answers: Hermes may still retry or fail the call."""
        try:
            with self._activity.lifecycle_lock:
                inflight, self._edits_inflight = self._edits_inflight, []
                if not inflight:
                    return
                if self._edits_staged:                           # an earlier attempt never got an answer
                    UNCONFIRMED["count"] += sum(len(refs) for refs in self._edits_staged.values())
                    self._edits_staged.clear()
                present = None
                if isinstance(request_messages, list):
                    present = {activity_digest(m["content"]) for m in request_messages
                               if isinstance(m, dict) and m.get("role") == "tool" and isinstance(m.get("content"), str)}
                    if not present:
                        present = None                           # another request format (Anthropic blocks, Responses input): cannot check
                staged = []
                for ref in inflight:
                    want = self._edits_digests.get(ref)
                    if present is not None and want is not None and want not in present:
                        self._edits.fail(ref, "Not in the request")
                        UNCONFIRMED["count"] += 1
                    else:
                        staged.append(ref)
                if staged:
                    self._edits_staged[str(api_request_id or "")] = staged
        except Exception:
            swallowed(self, "stage_edits")

    def confirm_edits_applied(self, api_request_id: str | None = None) -> None:
        """The provider accepted a request: the edits it carried are applied. Without an id, every held attempt counts."""
        try:
            with self._activity.lifecycle_lock:
                keys = list(self._edits_staged) if api_request_id is None else [str(api_request_id)]
                for key in keys:
                    refs = self._edits_staged.pop(key, None)
                    if refs:
                        self._edits.sent(refs)
        except Exception:
            swallowed(self, "confirm_edits_applied")

    def edits_not_confirmed(self, api_request_id: str = "", *, retryable: bool = True) -> None:
        """A request failed. Its edits stay pending when Hermes will retry, and read Failed when it will not."""
        try:
            with self._activity.lifecycle_lock:
                refs = self._edits_staged.pop(str(api_request_id or ""), None) or []
                UNCONFIRMED["count"] += len(refs)
                if not retryable:
                    for ref in refs:
                        self._edits.fail(ref, "Not sent")
        except Exception:
            swallowed(self, "edits_not_confirmed")

    def _edit_original(self, change: Change) -> str:
        """The screened original of a shortened output, or a PolicyError saying why there is none."""
        entry, source = lookup_original(self._store, change.ref, self._session_original_lookup(), cache=False)
        if source == SECRET_SOURCE:
            raise PolicyError("Held a secret, so Tameru does not keep or show it.")
        text = screen_original(entry.text) if entry is not None else None
        if text is None or activity_digest(text) != change.original_digest:
            raise PolicyError("No original kept. Use Re-Read instead.")
        return text

    def _edit_would_crowd(self, extra_tokens: int, current_tokens: int | None = None) -> bool:
        """True when adding ``extra_tokens`` to what the model already gets would pass Hermes' compaction point.

        Hermes decides to summarise from the size of the request after this overlay, and a summary cannot be undone,
        so a Restore must never be what tips a chat over. ``current_tokens`` is the request being built, or None to
        use the provider's last real prompt size. Unknown sizes never block (nothing to compare)."""
        try:
            threshold = int(getattr(self, "threshold_tokens", 0) or 0)
            used = current_tokens if current_tokens is not None else getattr(self, "last_real_prompt_tokens", None)
            if threshold <= 0 or type(used) is not int or used <= 0:
                return False
            return used + max(0, int(extra_tokens)) > threshold
        except Exception:
            return False

    def pin_change(self, verb: str, value) -> dict:
        """pin TEXT / unpin NUMBER. Returns {"message": ...} or raises PolicyError with one short reason."""
        if not (self._settings.enabled and self._settings.pins):
            raise PolicyError("Pinned instructions are off for this chat.")
        with self._activity.lifecycle_lock:
            if verb == "pin":
                line = self._pins.add(value)
                message = "Pinned. It stays word for word in every Hermes summary of this chat."
            else:
                line = self._pins.drop(int(value))
                message = "Unpinned."
            from .session_pins import save
            save(self)
            return {"message": message, "text": line}

    def edit_change(self, verb: str, ref: str = "") -> dict:
        """keep / unkeep / restore / cancel. Returns {"message": ...} or raises PolicyError with one short reason."""
        if not self.edits_enabled():
            raise PolicyError("Keep and Restore are off for this chat.")
        with self._activity.lifecycle_lock:
            if verb == "cancel":
                gone = self._edits.cancel()
                return {"message": f"Cancelled {gone} pending change{'' if gone == 1 else 's'}."}
            matches = [c for c in self._activity.rows() if len(ref) >= 8 and c.ref.startswith(ref)]
            if len(matches) > 1:
                raise PolicyError("That reference matches more than one output. Use the full reference.")
            change = matches[0] if matches else None
            if change is None:
                raise PolicyError("Tameru has no shortened output with that reference in this chat.")
            if not change.live:
                raise PolicyError("That output is no longer shortened in this chat.")
            if verb == "unkeep":
                dropped = self._edits.drop(change.ref)
                from .session_edits import save
                save(self)
                return {"message": "Unpinned." if dropped else "That output was not kept."}
            if verb not in ("keep", "restore"):
                raise PolicyError("Use keep, unkeep, restore or cancel.")
            if change.external:
                raise PolicyError("Web output: use Re-Read instead.")
            delta = max(0, len(self._edit_original(change)) - change.after_chars)
            entry = self._edits.entries.get(change.ref)
            held = self._edits.held_chars() - (entry["delta"] if entry else 0)
            if held + delta > held_budget_chars(getattr(self, "threshold_tokens", 0)):
                raise PolicyError("Keep limit reached. Unkeep something first." if verb == "keep"
                                  else "Too big to restore right now.")
            if self._edit_would_crowd(delta // 4 - (entry["delta"] // 4 if entry else 0)):
                raise PolicyError("Memory nearly full. Restoring would make Hermes summarise this chat.")
            if change.ref not in self._edits.entries and len(self._edits.entries) >= _EDIT_MAX:
                raise PolicyError("Too many edits. Cancel or unkeep some first.")
            self._edits.request(change.ref, verb, delta)
            self._host.note_reach(2.0)
            if verb == "keep":
                from .session_edits import save
                save(self)
            window = max(1, getattr(self, "threshold_tokens", 0) or 1)
            extra = max(1, round(100 * (delta / 4) / window)) if delta else 0
            return {"message": ("Kept. " if verb == "keep" else "Restoring. ")
                    + "It reaches the AI with your next message."
                    + (f" About +{extra}% of memory each reply." if extra else ""), "delta_chars": delta}

    def _edits_overlay(self, messages):
        """The request with Kept and Restored outputs shown in full, or None when nothing changes.

        Request-only: the saved history and ``messages`` itself are never touched. A row counts only if it is
        provably the one Tameru shortened (same message identity and the same bytes), is not web content, and its
        original passes the secret screen. The total it adds is held under a budget; Keeps go first, then the newest.
        Any error leaves the request exactly as Hermes built it and, after three, switches edits off for the chat.
        """
        try:
            if not isinstance(messages, list) or not self.edits_enabled():
                return None
            with self._activity.lifecycle_lock:
                active = self._edits.active()
                if not active:
                    return None
                found = []                                           # (keep first, newest first, ref, index, original)
                seen = set()
                for index, row in enumerate(messages):
                    content = row.get("content") if isinstance(row, dict) else None
                    if (not isinstance(content, str) or row.get("role") != "tool"
                            or TAMERU_TAG not in content[:HEADER_MAX_LEN + 200]):
                        continue
                    header = parse_header(content)
                    ref = header["ref"] if header else None
                    if ref not in active:
                        continue
                    change = self._activity.get(ref)
                    if (change is None or change.external or not same_occurrence(change.identity, row)
                            or change.current_digest != activity_digest(content)):
                        continue
                    seen.add(ref)
                    try:
                        original = self._edit_original(change)
                    except PolicyError as refusal:
                        self._edits.fail(ref, str(refusal))
                        continue
                    found.append((0 if active[ref] == "keep" else 1, -index, ref, index, original))
                for ref in active:
                    if ref not in seen:
                        self._edits.fail(ref, "Not in this chat's history")
                budget = held_budget_chars(getattr(self, "threshold_tokens", 0))
                out, used, applied = list(messages), 0, []
                base_tokens = message_budget_tokens(messages)
                for _priority, _newest, ref, index, original in sorted(found):
                    row = messages[index]
                    delta = max(0, len(original) - len(row["content"]))
                    if used + delta > budget:
                        self._edits.fail(ref, "Too big")
                        continue
                    if self._edit_would_crowd((used + delta) // 4, base_tokens):
                        self._edits.fail(ref, "Memory nearly full")
                        continue
                    replacement = dict(row, content=original)
                    self._edits_digests[ref] = activity_digest(original)
                    drop_stale_api_content(replacement)
                    out[index] = replacement
                    used += delta
                    applied.append(ref)
                if not applied:
                    return None
                self._edits_inflight = applied
                return out
        except Exception:
            try:
                with self._activity.lifecycle_lock:
                    self._edits.overlay_failed()
            except Exception:
                swallowed(self, "_edits_overlay")
            return None
