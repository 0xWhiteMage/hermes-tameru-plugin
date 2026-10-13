"""The Tameru context engine: Hermes' ``ContextCompressor`` with a query-aware tool-result ladder.

Hermes already decides *when* to prune, *which* rows are old enough and *whether* the result is worth
committing (proactive-prune gates, tail boundary, pending-round spare, ``archive_and_compact``). The
engine hooks only *what a demoted tool result becomes*:

* seam A, ``_demote_tool_result_at`` (pass 2 with 5 args, pass 4 with 4): instead of Hermes' 1-line
  summary a row goes down a ladder: superseded header, query-aware extract, structural brief,
  header-only, and as the last rung Hermes' own line (``_Ladder`` below);
* seam B, ``_demote_stale_tail_tools`` (lean tail mode): stubs of Tameru rows keep their ``ref``;
* ``_prune_old_tool_results`` / ``_prune_boundary``: with ``prune_tail="tokens"`` the proactive prune sizes its
  protected tail like a full compaction does (and runs pass 4), instead of by message count alone;
* ``_augment_summary_lean``: the artifact trail and the errors of ``ledger``;
* ``tameru_expand``: the tool that hands a pruned original back.

Public overrides only set the per-pass scope (a ``ContextVar``) and return ``super()``'s object
untouched, so Hermes' no-op contract (``result is messages``), ``bypass_cooldown`` and every other
gate behave as without the plugin. Per-agent state lives in instance attributes; a clone starts with a
fresh store and memo.

Only this module imports ``agent.context_compressor``, and it does so guarded: without Hermes the
module still imports and the class raises ``ImportError`` when instantiated.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import inspect
import json
import re
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from .config import (
    TameruSettings, compression_kwargs, load_settings, prune_default_applies, prune_regrowth_tokens,
    prune_trigger_follows_window, reprune_gate_applies, read_hermes_config, scaled_error_budget, scaled_prune_tokens,
    scaled_retained_budget,
)
from .hermes_compat import (
    LEAN_STUB_RE,
    SKILL_PRUNED_MARKER_PREFIX,
    SeamReport,
    compression_threshold,
    drop_stale_api_content,
    elision_marker,
    is_persisted_block,
    marker_artifact_re,
    message_budget_tokens,
    probe_seams,
    refused_summary,
    session_db_usable,
    summarize_tool_result,
    tool_calls_by_id,
)
from .ledger import build_ledger, merge_into_summary
from .payload import HermesPayload, content_hint, meta_line, parse_payload
from .query import build_query, usable_query
from .recovery import EXPAND_SCHEMA, EXPAND_TOOL_NAME, OriginalStore, expand, session_lookup_factory
from .render import (
    TAMERU_TAG,
    RenderStats,
    build_header,
    classify_render,
    header_only,
    header_parent,
    make_ref,
    omitted_categories,
    parse_header,
    render,
    retarget_rung,
    true_line_count,
    for_summary,
)
from .supersede import SupersessionIndex, build_index, superseded_by
from .tameru.compress_context import (
    InliningGapMarker,
    ENGINE_VERSION,
    brief_context,
    compress_context,
    error_fingerprints,
    extract_evidence_lines,
)
from .telemetry import Telemetry

# A dropped span shorter than Hermes's elision marker is kept verbatim (extract and brief alike).
_INLINING_ELISION = InliningGapMarker(elision_marker)

try:
    from agent.context_compressor import ContextCompressor as _Base
except Exception:  # Hermes absent: the module stays importable, the engine cannot be built
    _Base = None

DISPLAY_NAME = "Tameru (貯める)"

# Tools whose results the agent re-reads as instructions or state: Hermes' own summary line applies.
_EXEMPT_TOOLS = frozenset({
    "skill_view", "skills_list", "skill_manage", "clarify", "todo_list", "todo", "memory",
    "session_search", "tameru_expand", "delegate_task", "cronjob_manage", "process_manage",
})
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
_BODYLESS = frozenset({"header", "superseded"})   # rungs that keep no text of the original
_ERROR_LINE_CHARS = 300                            # an errors row keeps at most this much of one exemplar line
_FIXED_RETRIES = 2                                 # fixed-budget extract attempts after an oversized one
_MEMO_ENTRIES = 2048
_MEMO_MAX_TEXT = 50_000       # a bigger extract is recomputed rather than held
_MEMO_MAX_CHARS = 4_000_000   # ...and the memo as a whole holds at most this many chars of extracts
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_SPILL_PATH_RE = re.compile(r"^Full output saved to: (\S[^\n]*)$", re.MULTILINE)   # Hermes' <persisted-output> block
_VERSION_RE = re.compile(r"^version:\s*(\S+)", re.MULTILINE)


def _base() -> type:
    return _Base if _Base is not None else object


class _Extract(NamedTuple):
    """What ``compress_context`` returned that the ladder looks at."""

    text: str
    fail_open: bool
    risk: str | None


class _Memo:
    """Bounded LRU of extracts. A copy is a fresh empty one, so a cloned agent shares nothing."""

    def __init__(self, max_entries: int = _MEMO_ENTRIES, max_chars: int = _MEMO_MAX_CHARS) -> None:
        self._max = max_entries
        self._max_chars = max_chars
        self._chars = 0
        self._items: OrderedDict[tuple, _Extract] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> _Extract | None:
        with self._lock:
            found = self._items.get(key)
            if found is not None:
                self._items.move_to_end(key)
            return found

    def put(self, key: tuple, value: _Extract) -> None:
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._chars -= len(old.text)
            self._items[key] = value
            self._chars += len(value.text)
            while len(self._items) > self._max or self._chars > self._max_chars:
                self._chars -= len(self._items.popitem(last=False)[1].text)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._chars = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __copy__(self) -> _Memo:
        return _Memo(self._max, self._max_chars)

    def __deepcopy__(self, memo: dict) -> _Memo:
        return _Memo(self._max, self._max_chars)


@dataclass
class _Pass:
    """State of one prune or compress call (one per ``ContextVar`` set)."""

    budget: int                                   # inner chars Tameru may still process
    focus: str | None = None                      # ``/compress <topic>``
    degrade: bool = False                         # escalation re-run: header-only rungs only
    pressure: bool = False                        # inside Hermes' pass 4
    full: bool = False                            # inside compress(): a full compaction, not the proactive prune
    renders: int = 0                              # rows rewritten into a Tameru row
    messages: list[dict] | None = None            # the list Hermes was handed (indices match ``result``)
    query_cache: dict = field(default_factory=dict)
    supersede: SupersessionIndex | None = None
    age_demote: set[int] | None = None            # rendered rows beyond the retained budget
    room: int = 0                                 # retained-extract chars left for new rows
    err_room: int = 0                             # retained "errors"-row chars left for new rows
    boundary: int | None = None                   # Hermes' prune boundary of this pass (pass 2 runs below it)
    pending: list[int] | None = None              # pass-2 rows the ladder may still render, oldest first
    reserve_tail: list[int] | None = None         # reserve_tail[k]: estimated brief chars of pending[k:]
    gone: list[str] = field(default_factory=list)  # refs of rows this pass cut down to a body-less header


_SCOPE: ContextVar[_Pass | None] = ContextVar("tameru_pass", default=None)


def _view(scope: _Pass, result: list[dict]) -> list[dict]:
    """The list whose indices and rows the query and the supersession index read: the unpruned input
    Hermes was handed when it lines up with ``result``, else ``result`` itself."""
    messages = scope.messages
    return messages if messages and len(messages) == len(result) else result


def _json_args(raw: Any) -> dict:
    """Tool-call arguments as a dict ({} when they are not a JSON object)."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _spill_note(content: str) -> str:
    """``spilled to <path>`` for a Hermes ``<persisted-output>`` block: the pointer is all that is worth keeping."""
    found = _SPILL_PATH_RE.search(content) if is_persisted_block(content) else None
    return f"spilled to {found.group(1).strip()}" if found else ""


def _line_count(text: str) -> int:
    """Lines of ``text`` the way ``tameru_expand`` numbers them."""
    return len(_LINE_BREAK_RE.split(text))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _plugin_version() -> str:
    """The version in ``plugin.yaml`` (shipped with both install paths), else ``unknown``."""
    try:
        found = _VERSION_RE.search(Path(__file__).with_name("plugin.yaml").read_text("utf-8"))
        return found.group(1) if found else "unknown"
    except (OSError, ValueError):   # unreadable, or not UTF-8
        return "unknown"


class TameruContextEngine(_base()):  # type: ignore[misc]
    """Hermes' compressor with the Tameru tool-result ladder, recovery tool and artifact ledger."""

    name = "tameru"
    DISPLAY_NAME = DISPLAY_NAME
    display_name = DISPLAY_NAME
    _seam_report: SeamReport | None = None     # probed once per class

    def __init__(
        self, model: str = "pending", *, settings: TameruSettings | None = None, **kwargs: Any,
    ) -> None:
        if _Base is None:
            raise ImportError("hermes-tameru-plugin needs Hermes Agent (agent.context_compressor)")
        warnings: list[str] = []
        if settings is None:
            settings, warnings = load_settings()
        scaled = False
        try:
            merged, raw = read_hermes_config()
            params = set(inspect.signature(_Base.__init__).parameters) - {"self"}
            configured, more = compression_kwargs(merged, raw, settings, params)
            warnings += more
            # The plugin's own prune trigger (not the user's, not an explicit argument) follows the window.
            scaled = (
                "proactive_prune_tokens" not in kwargs and "proactive_prune_tokens" in configured
                and prune_default_applies(raw, settings)
            )
        except Exception as exc:
            configured = {}
            warnings.append(f"compression.* could not be read ({exc!r})")
        super().__init__(model=model, **{**configured, **kwargs})
        self._settings = settings
        self._scaled_prune = scaled
        self._scale_prune_trigger()
        self._warnings = warnings
        self._store = OriginalStore(settings.store_max_entries, settings.store_max_chars)
        self._telemetry = Telemetry(settings.telemetry_log)
        self._memo = _Memo()
        self._expand_injected = False
        self._exempt = _EXEMPT_TOOLS | frozenset(settings.exempt_tools)
        try:
            self._settings_sha = _digest(repr(settings))[:12]
            self._seams = self._probe()
            self._token_tail = (
                "protect_tail_tokens" in inspect.signature(_Base._prune_old_tool_results).parameters
                and callable(getattr(_Base, "_prune_boundary", None))
            )
        except Exception as exc:  # degrade to stock behaviour
            self._settings_sha = ""
            self._token_tail = False
            self._seams = SeamReport(reasons=(f"seam probe failed: {exc!r}",))

    @classmethod
    def _probe(cls) -> SeamReport:
        if cls._seam_report is None:
            cls._seam_report = probe_seams(_base())
        return cls._seam_report

    def get_automatic_compaction_status_message(
        self, *, phase: str, default_message: str, **context: Any,
    ) -> str | None:
        message = super().get_automatic_compaction_status_message(
            phase=phase, default_message=default_message, **context,
        )
        if message is None or not self._settings.enabled:   # suppressed by the host flag; or stock
            return message
        return f"🗜️ {self.DISPLAY_NAME} compaction — {message}"

    # ---- lifecycle ----------------------------------------------------------------------------
    def update_model(self, model: str, context_length: int, *args: Any, **kwargs: Any) -> None:
        """Seed Hermes' threshold with its Codex autoraise (host-side only for the built-in engine).

        Also with the plugin off: ``enabled: false`` is the built-in compressor, which has the autoraise."""
        if hasattr(self, "_config_threshold_percent"):
            try:
                provider = kwargs.get("provider", args[2] if len(args) > 2 else "")
                section = read_hermes_config()[0].get("compression")
                raised = compression_threshold(model, provider, section if isinstance(section, dict) else {})
                if raised is not None:
                    self._config_threshold_percent = raised
            except Exception:
                pass
        super().update_model(model, context_length, *args, **kwargs)
        self._scale_prune_trigger()

    def _scale_prune_trigger(self) -> None:
        """Re-derive the plugin-supplied proactive-prune trigger from Hermes' current compaction threshold."""
        # Reading threshold_tokens resolves the context window; for the constructor's placeholder model that is a
        # catalogue lookup over the network. Hermes sizes the engine with update_model right after building it, and
        # that call lands here again with the window known. A host without lazy window resolution (no
        # ``_resolved_context_length`` attribute) has the window already and is scaled straight away.
        if getattr(self, "_scaled_prune", False) and getattr(self, "_resolved_context_length", 0) is not None:
            try:
                self.proactive_prune_tokens = scaled_prune_tokens(
                    self._settings, getattr(self, "threshold_tokens", 0),
                )
            except Exception:
                pass

    def _retained_budget(self) -> int:
        """The retained-extract budget now: the configured floor, or the window-scaled value when larger.

        Computed at use time because Hermes' compaction threshold changes with ``update_model``.
        """
        try:
            return scaled_retained_budget(self._settings, getattr(self, "threshold_tokens", 0))
        except Exception:
            return self._settings.retained_extract_budget_chars

    def _retained_error_budget(self) -> int:
        """The retained ``errors``-row budget now (``_retained_budget``'s counterpart)."""
        try:
            return scaled_error_budget(self._settings, getattr(self, "threshold_tokens", 0))
        except Exception:
            return self._settings.retained_error_budget_chars

    def clone_for_agent(self) -> TameruContextEngine:
        """A per-agent copy; the store, memo and telemetry copy as fresh empty objects."""
        return copy.deepcopy(self)

    def on_session_reset(self) -> None:
        super().on_session_reset()
        self._store.clear()
        self._memo.clear()

    def on_session_end(self, *args: Any, **kwargs: Any) -> None:
        super().on_session_end(*args, **kwargs)
        self._store.clear()
        self._memo.clear()

    # ---- public overrides: scope only ---------------------------------------------------------
    def _new_scope(self, messages: list[dict] | None, **fields: Any) -> _Pass:
        return _Pass(budget=self._settings.pass_char_budget, messages=messages, **fields)

    def prune_tool_results_only(self, messages, current_tokens=None, *args, **kwargs):
        """Hermes' gated prune with the ladder active; the result object is Hermes' own.

        When Hermes declines to commit (its min-reclaim gate) although rows were rendered, the pass
        runs once more with header-only rungs, which reclaim more.
        """
        parent = super().prune_tool_results_only
        if not (self._settings.enabled and self._seams.demote):
            return parent(messages, current_tokens, *args, **kwargs)
        scope = self._new_scope(messages)
        token = _SCOPE.set(scope)
        started = time.perf_counter()
        trigger, min_reclaim = self.proactive_prune_tokens, self.proactive_prune_min_reclaim_tokens
        self.proactive_prune_tokens = self._regrowth_trigger(trigger)
        self.proactive_prune_min_reclaim_tokens = self._reprune_min_reclaim(messages, min_reclaim)
        try:
            # Counters of a pass Hermes declines to commit (it returns the input object) are dropped:
            # nothing was rewritten, so no row was rendered and no character saved.
            with self._telemetry.hold() as held:
                res = parent(messages, current_tokens, *args, **kwargs)
                held.keep = res[0] is not messages
            if res[0] is messages and scope.renders and not scope.degrade:
                self._telemetry.count("escalation")
                _SCOPE.set(self._new_scope(messages, degrade=True))
                with self._telemetry.hold() as held:
                    res = parent(messages, current_tokens, *args, **kwargs)
                    held.keep = res[0] is not messages
            if res[0] is not messages:
                self._arm_regrowth(res[0])
            return res
        finally:
            self.proactive_prune_tokens, self.proactive_prune_min_reclaim_tokens = trigger, min_reclaim
            _SCOPE.reset(token)
            self._telemetry.time(time.perf_counter() - started)

    def _regrowth_active(self) -> bool:
        """The regrowth re-arm applies to the plugin's own, window-scaled trigger; a user's or caller's value is Hermes'."""
        return (
            bool(getattr(self, "_scaled_prune", False)) and self._settings.prune_regrowth_ratio > 0
            and prune_trigger_follows_window(self._settings, getattr(self, "threshold_tokens", 0))
        )

    def _regrowth_trigger(self, trigger: int) -> int:
        """The trigger gate while a prune is armed: Hermes' rearm gate (message tokens) then decides alone."""
        if not self._regrowth_active():
            return trigger
        rearm = getattr(self, "_proactive_prune_rearm_tokens", 0)
        return min(trigger, rearm) if isinstance(rearm, int) and not isinstance(rearm, bool) and rearm > 0 else trigger

    def _reprune_min_reclaim(self, messages: list[dict], min_reclaim: int) -> int:
        """Hermes' minimum reclaim, raised on a re-prune so that it leaves at most ``reprune_target_ratio`` of the threshold.

        A prune that leaves much more than a compaction would (about 0.2 of the threshold) gains little over waiting
        for the compaction, which resets the prompt for good, while its commit breaks the cache just the same.
        """
        ratio = self._settings.reprune_target_ratio
        rearm = getattr(self, "_proactive_prune_rearm_tokens", 0)
        if (
            not self._regrowth_active() or ratio <= 0 or not isinstance(rearm, int) or rearm <= 0
            or not reprune_gate_applies(self._settings, getattr(self, "threshold_tokens", 0))
        ):
            return min_reclaim
        try:
            target = round(ratio * int(getattr(self, "threshold_tokens", 0) or 0))
            return max(min_reclaim, message_budget_tokens(messages) - target) if target > 0 else min_reclaim
        except Exception:
            return min_reclaim

    def _arm_regrowth(self, pruned: list[dict]) -> None:
        """After a commit, shorten Hermes' rearm runway to ``prune_regrowth_ratio`` of the compaction threshold."""
        if not self._regrowth_active():
            return
        try:
            gap = prune_regrowth_tokens(self._settings, getattr(self, "threshold_tokens", 0))
            rearm = getattr(self, "_proactive_prune_rearm_tokens", 0)
            if gap <= 0 or not isinstance(rearm, int) or isinstance(rearm, bool):
                return
            lowered = message_budget_tokens(pruned) + max(gap, int(getattr(self, "proactive_prune_min_reclaim_tokens", 0)))
            if lowered < rearm:
                self._proactive_prune_rearm_tokens = lowered
        except Exception:
            self._telemetry.count("error", where="regrowth")

    def compress(
        self, messages, current_tokens=None, focus_topic=None, force=False, memory_context="",
        **kwargs,
    ):
        """Hermes' compression with the ladder active; ``kwargs`` (``bypass_cooldown``) pass through."""
        parent = super().compress
        if not (self._settings.enabled and self._seams.demote):
            return parent(
                messages, current_tokens=current_tokens, focus_topic=focus_topic, force=force,
                memory_context=memory_context, **kwargs,
            )
        token = _SCOPE.set(self._new_scope(messages, focus=focus_topic, full=True))
        started = time.perf_counter()
        try:
            return parent(
                messages, current_tokens=current_tokens, focus_topic=focus_topic, force=force,
                memory_context=memory_context, **kwargs,
            )
        finally:
            _SCOPE.reset(token)
            self._telemetry.time(time.perf_counter() - started)

    def _token_tail_scope(self) -> _Pass | None:
        """The scope of a proactive prune whose tail is token-sized (``prune_tail``), else None."""
        scope = _SCOPE.get()
        if (
            scope is None or scope.full or not self._token_tail
            or not (self._settings.enabled and self._seams.demote and self._settings.prune_tail == "tokens")
        ):
            return None
        return scope

    def _prune_old_tool_results(self, messages, protect_tail_count, protect_tail_tokens=None, *args, **kwargs):
        """Hermes' prune passes; the proactive prune also sizes its tail in tokens (``prune_tail``).

        Hermes protects the newest ``protect_last_n`` messages of a proactive prune whatever they weigh,
        so a 50 KB result read a few turns ago stays verbatim for the rest of the session while the
        cache-breaking commit leaves it behind. With ``prune_tail="tokens"`` the prune gets what a full
        compaction gets: the tail is capped at ``tail_token_budget`` (the shorter of that and
        ``protect_last_n`` wins, never fewer than Hermes' message floor) and pass 4 demotes the tail's
        oldest bodies while it still exceeds its soft budget. The ladder keeps the query-relevant
        lines and a ``ref`` of every row it cuts.
        """
        if protect_tail_tokens is None and self._token_tail_scope() is not None:
            budget = getattr(self, "tail_token_budget", None)
            if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
                protect_tail_tokens = budget
        return super()._prune_old_tool_results(messages, protect_tail_count, protect_tail_tokens, *args, **kwargs)

    def _prune_boundary(self, result, protect_tail_count, protect_tail_tokens):
        """First index of the protected tail; in a token-tail prune never earlier than Hermes' count tail."""
        boundary = super()._prune_boundary(result, protect_tail_count, protect_tail_tokens)
        if protect_tail_tokens and self._token_tail_scope() is not None:
            boundary = max(boundary, len(result) - protect_tail_count)
        scope = _SCOPE.get()
        if scope is not None and isinstance(boundary, int):
            scope.boundary, scope.pending = boundary, None
        return boundary

    # ---- seam A: the ladder -------------------------------------------------------------------
    def _demote_tool_result_at(self, result, idx, call_id_to_tool, min_prune_chars, *rest, **kw):
        """Replace the tool result at ``idx`` (Hermes' pass 2 and pass 4 call this, via ``self``).

        Stays a drop-in for the parent staticmethod: same arguments, True when the row changed.
        """
        parent = super()._demote_tool_result_at
        if not (self._settings.enabled and self._seams.demote):
            return parent(result, idx, call_id_to_tool, min_prune_chars, *rest, **kw)
        try:
            return self._ladder(parent, result, idx, call_id_to_tool, min_prune_chars, rest, kw)
        except Exception:
            self._telemetry.count("error", where="demote")
            return parent(result, idx, call_id_to_tool, min_prune_chars, *rest, **kw)

    def _plan_retention(self, scope: _Pass, result: list[dict]) -> None:
        """Walk newest to oldest; rendered rows beyond ``retained_extract_budget_chars`` are demoted.

        Only rows inside a pass Hermes is already running change, and Hermes' min-reclaim gate still
        decides whether that pass commits.
        """
        room = self._retained_budget()
        err_room = self._retained_error_budget()
        demote: set[int] = set()
        for i in range(len(result) - 1, -1, -1):
            msg = result[i]
            content = msg.get("content") if msg.get("role") == "tool" else None
            if isinstance(content, str) and "\n" in content and classify_render(content):
                header = parse_header(content)
                if header is not None and header["rung"] == "errors":   # their own, separate budget
                    if len(content) <= err_room:
                        err_room -= len(content)
                    else:
                        demote.add(i)
                elif len(content) <= room:
                    room -= len(content)
                else:
                    demote.add(i)
        scope.age_demote, scope.room, scope.err_room = demote, room, err_room

    def _ladder(self, parent, result, idx, call_id_to_tool, min_prune_chars, rest, kw) -> bool:
        settings = self._settings
        msg = result[idx]
        content = msg.get("content")
        if msg.get("role") != "tool" or not isinstance(content, str):
            return parent(result, idx, call_id_to_tool, min_prune_chars, *rest, **kw)
        scope = _SCOPE.get() or self._new_scope(None)
        if scope.age_demote is None:
            self._plan_retention(scope, result)
        tool, raw_args = call_id_to_tool.get(msg.get("tool_call_id") or "", ("unknown", ""))
        if not rest and not scope.full and tool in self._exempt:
            # Pass 4 of a proactive prune (``prune_tail``): an instruction or state row, such as a skill the
            # model believes is loaded, is not worth a ghost; only a full compaction may override that guard.
            return False

        kind = classify_render(content)
        if kind:
            # Committed bytes never change, except in the pressure pass or for an aged-out row.
            if not (scope.pressure or idx in scope.age_demote):
                return False
            return self._demote_rendered(result, idx, content, kind, tool, raw_args)

        shadow = [dict(msg)]
        if not parent(shadow, 0, call_id_to_tool, min_prune_chars, *rest, **kw):
            return False
        line = shadow[0].get("content")
        args = _json_args(raw_args)
        if (
            not isinstance(line, str) or tool in self._exempt
            or refused_summary(tool, args, content) is not None
            or SKILL_PRUNED_MARKER_PREFIX in content
            or len(content) < settings.min_tool_chars
        ):
            return self._keep_parent(result, idx, shadow[0], tool)
        spill = _spill_note(content)
        if not spill and is_persisted_block(content):
            return self._keep_parent(result, idx, shadow[0], tool)
        if scope.budget < len(content):
            self._telemetry.count("budget_skip", tool=tool)
            return self._keep_parent(result, idx, shadow[0], tool)
        scope.budget -= len(content)

        pl = parse_payload(content)
        line = self._terminal_line(line, pl)
        ref = make_ref(msg, content)
        if spill:   # Hermes already bounded this row; what the agent needs later is where the rest is
            return self._commit(
                result, idx, content, tool, ref, pl, scope, "header",
                self._header_row(line, pl, ref, "header", spill),
            )
        view = _view(scope, result)
        if settings.supersession:
            if scope.supersede is None:
                scope.supersede = build_index(view, call_id_to_tool)
            hit = superseded_by(scope.supersede, idx)
            if hit is not None:
                note = f"superseded by msg {hit[0]} ({hit[1]})"
                return self._commit(
                    result, idx, content, tool, ref, pl, scope, "superseded",
                    self._header_row(line, pl, ref, "superseded", note),
                )
        if scope.degrade or scope.pressure:
            return self._commit_header_or_errors(result, idx, content, tool, ref, pl, scope, line)

        # The engine's task query names the tool itself ("terminal", "read_file"): in an agent transcript every
        # row shares that word and it selects incidental lines, so the plugin leaves it out, unless the user's
        # ``/compress <topic>`` is that very word (the query de-duplicates the two into one part).
        focus = " ".join(scope.focus.split()) if isinstance(scope.focus, str) else ""
        query = [
            part for part in usable_query(
                build_query(view, idx, focus=scope.focus, cache=scope.query_cache), pl.inner,
            )
            if part != tool or part == focus
        ]
        capped = min(settings.max_extract_chars, int((1 - settings.min_savings) * len(content)))
        limit = min(capped, scope.room)
        # Hermes walks pass 2 oldest first, while the retained budget favours the newest rows: an old row's
        # extract or brief may not take the room the rows still to come in this pass need for their briefs.
        free = scope.room - self._reserved_after(scope, result, idx, call_id_to_tool, min_prune_chars)
        own_limit = min(limit, free)
        hint = content_hint(tool, args, pl.inner)
        for rung, build in (
            ("extract", lambda: self._extract_row(line, pl, ref, query, hint, own_limit)),
            ("brief", lambda: self._brief_row(line, pl, ref, query, own_limit) if own_limit > 0 else None),
        ):
            rendered = build()   # lazily: the brief is only computed when the extract was refused
            if rendered is not None:
                scope.room -= len(rendered)
                return self._commit(result, idx, content, tool, ref, pl, scope, rung, rendered)
        # The retained budget (and its reservation), not the savings rule, ruled the body out; or the original has
        # error templates, which Hermes' one-line summary would drop (the errors rung keeps them).
        if free < capped or error_fingerprints(pl.inner):
            return self._commit_header_or_errors(result, idx, content, tool, ref, pl, scope, line)
        return self._keep_parent(result, idx, shadow[0], tool)

    @staticmethod
    def _terminal_line(line: str, pl: HermesPayload) -> str:
        """Hermes' terminal one-liner with the payload's real line count (it counts the JSON-encoded result's
        one line); only for a row this plugin renders and only when the payload was unwrapped."""
        return true_line_count(line, _line_count(pl.inner)) if pl.wrapped else line

    def _reserved_after(
        self, scope: _Pass, result: list[dict], idx: int, call_id_to_tool: dict, min_prune_chars: int,
    ) -> int:
        """Retained-budget chars to leave for the rows pass 2 has still to visit after ``idx``.

        The estimated brief of each row the ladder could render (unrendered tool rows over both size floors,
        not exempt, not a spill) between ``idx`` and the prune boundary; 0 outside pass 2. A brief starts at
        ``max(brief_chars, brief_share * len)`` and grows by half while an error template is missing, so a row with
        an error fingerprint is estimated one step up; never above the row's own extract cap.
        """
        boundary = scope.boundary
        if boundary is None or idx >= boundary or scope.pressure:
            return 0
        if scope.pending is None:
            floor = max(self._settings.min_tool_chars, min_prune_chars + 1)
            pending = []
            for j in range(min(boundary, len(result))):
                msg = result[j]
                content = msg.get("content")
                if (
                    msg.get("role") == "tool" and isinstance(content, str) and len(content) >= floor
                    and not classify_render(content) and not is_persisted_block(content)
                    and call_id_to_tool.get(msg.get("tool_call_id") or "", ("unknown", ""))[0] not in self._exempt
                ):
                    pending.append(j)
            tail = [0] * (len(pending) + 1)
            for k in range(len(pending) - 1, -1, -1):
                tail[k] = tail[k + 1] + self._brief_estimate(result[pending[k]]["content"])
            scope.pending, scope.reserve_tail = pending, tail
        return scope.reserve_tail[bisect.bisect_right(scope.pending, idx)]

    def _brief_estimate(self, content: str) -> int:
        """Deterministic estimate of the brief row ``content`` would get (see ``_reserved_after``)."""
        settings = self._settings
        inner = parse_payload(content).inner
        size = max(settings.brief_chars, int(settings.brief_share * len(inner)))
        if error_fingerprints(inner):
            size = size * 3 // 2
        return min(size, settings.max_extract_chars, int((1 - settings.min_savings) * len(content)))

    # ---- ladder rungs -------------------------------------------------------------------------
    def _errors_body(self, inner: str) -> str:
        """One exemplar line per error template of ``inner``, in order, within ``errors_row_chars``."""
        cap = self._settings.errors_row_chars
        if cap <= 0:
            return ""
        lines: list[str] = []
        used = 0
        for line in error_fingerprints(inner).values():
            line = line if len(line) <= _ERROR_LINE_CHARS else line[: _ERROR_LINE_CHARS - 1] + "…"
            if used + len(line) + (1 if lines else 0) > cap:
                if not lines:
                    lines.append(line[: max(1, cap - 1)] + "…")
                break
            lines.append(line)
            used += len(line) + (1 if len(lines) > 1 else 0)
        return "\n".join(lines)

    def _errors_row(self, parent_line: str, pl: HermesPayload, ref: str, room: int) -> str | None:
        """The ``errors`` row for ``pl`` (header, meta line, one line per error template) if it fits ``room``."""
        body = self._errors_body(pl.inner)
        if not body:
            return None
        rendered = self._body_row(" ".join(parent_line.splitlines()), pl, ref, "errors", body)
        return rendered if len(rendered) <= room else None

    def _commit_header_or_errors(
        self, result: list[dict], idx: int, content: str, tool: str, ref: str, pl: HermesPayload,
        scope: _Pass, line: str,
    ) -> bool:
        """A body-less row, except that a result with error templates keeps one exemplar line of each.

        Rows past the retained-extract budget (or in the pressure pass) used to drop to a bare header and
        took their error lines with them; on a large window, where no compaction summary carries them
        either, a session then kept almost none of its errors. The exemplar lines have their own budget
        (``retained_error_budget_chars``); past it, a bare header as before.
        """
        rendered = self._errors_row(line, pl, ref, scope.err_room)
        if rendered is not None:
            scope.err_room -= len(rendered)
            return self._commit(result, idx, content, tool, ref, pl, scope, "errors", rendered)
        return self._commit(
            result, idx, content, tool, ref, pl, scope, "header", self._header_row(line, pl, ref, "header"),
        )

    @staticmethod
    def _header_row(
        parent_line: str, pl: HermesPayload, ref: str, rung: str, note: str = "",
    ) -> str:
        """A body-less row for ``pl``: Hermes' line plus the Tameru tag."""
        # Hermes' line can span lines (a heredoc command); ``header_only`` would keep only the first,
        # dropping the ``-> exit N`` / ``FAILED`` tail that every other rung keeps.
        parent_line = " ".join(parent_line.splitlines())
        stats = RenderStats(
            0, _line_count(pl.inner), 0, len(pl.inner), omitted_categories(pl.inner, ""), rung, note,
        )
        return header_only(parent_line, stats, ref)

    def _compress(
        self, inner: str, query: list[str], hint: str | None, ratio: float | None,
    ) -> _Extract | None:
        """Memoized ``compress_context`` of ``inner`` (None when the engine raised)."""
        key = (
            _digest(inner), _digest(json.dumps(query, ensure_ascii=False)), ENGINE_VERSION,
            self._settings_sha, hint, ratio,
        )
        found = self._memo.get(key)
        if found is not None:
            return found
        settings = self._settings
        fixed = {} if ratio is None else {"mode": "fixed", "budget_ratio": ratio}
        try:
            res = compress_context(
                inner, query, ccr=False, citations=False, content_hint=hint,
                pin_patterns=list(settings.protect_patterns) or None, gap_marker=elision_marker,
                inline_short_gaps=True,
                recursion_markers=(TAMERU_TAG.strip(),), min_savings_ratio=settings.min_savings,
                **fixed,
            )
        except Exception:
            return None
        out = _Extract(res.compressed_text, bool(res.fail_open), res.compression_risk)
        if len(out.text) <= _MEMO_MAX_TEXT:
            self._memo.put(key, out)
        return out

    def _acceptable(self, out: _Extract | None, fingerprints: dict[str, str]) -> bool:
        """Not fail-open, within ``max_risk`` and an exemplar of every error of the original kept."""
        if out is None or out.fail_open:
            return False
        ceiling = _RISK_ORDER.get(self._settings.max_risk, 1)
        if _RISK_ORDER.get(out.risk or "", 2) > ceiling:
            return False
        return fingerprints.keys() <= error_fingerprints(out.text).keys()

    @staticmethod
    def _body_row(
        parent_line: str, pl: HermesPayload, ref: str, rung: str, body: str,
    ) -> str:
        marker = marker_artifact_re()
        kept = sum(1 for text in _LINE_BREAK_RE.split(body) if not marker.search(text))
        stats = RenderStats(
            kept, _line_count(pl.inner), len(body), len(pl.inner),
            omitted_categories(pl.inner, body), rung,
        )
        return render(parent_line, stats, ref, meta_line=meta_line(pl.meta), body=body)

    def _extract_row(
        self, parent_line: str, pl: HermesPayload, ref: str, query: list[str], hint: str | None,
        limit: int,
    ) -> str | None:
        """The query-aware extract row, within ``limit`` chars; fixed-budget retries if too big."""
        if limit <= 0:
            return None
        fingerprints = error_fingerprints(pl.inner)
        out = self._compress(pl.inner, query, hint, None)
        if not self._acceptable(out, fingerprints):
            return None
        rendered = self._body_row(parent_line, pl, ref, "extract", out.text)
        if len(rendered) <= limit:
            return rendered
        room = limit - (len(rendered) - len(out.text))
        if room <= 0:
            return None
        ratio = max(0.05, min(0.9, room / len(pl.inner)))
        # A forced budget can cut what the query asked about: the cut must keep one of the engine's
        # top query-evidence lines that the unforced extract kept (when there are any).
        evidence = [
            line for line in extract_evidence_lines(pl.inner, " ".join(query), limit=len(fingerprints) + 3)
            if not error_fingerprints(line) and line in out.text
        ][:3]
        # A fixed budget lands near, not under, its target (whole lines, gap markers), so one
        # tighter retry follows an overshoot.
        for _ in range(_FIXED_RETRIES):
            out = self._compress(pl.inner, query, hint, round(ratio, 4))
            if not self._acceptable(out, fingerprints):
                return None
            if evidence and not any(line in out.text for line in evidence):
                return None
            rendered = self._body_row(parent_line, pl, ref, "extract", out.text)
            if len(rendered) <= limit:
                return rendered
            ratio = max(0.05, ratio * min(0.9, limit / len(rendered)) * 0.9)
        return None

    def _brief_row(
        self, parent_line: str, pl: HermesPayload, ref: str, query: list[str], limit: int,
    ) -> str | None:
        """The structural brief row (errors, warnings, query lines, head, tail), within ``limit`` chars.

        The brief is reached when the extract was refused, mostly because it was too big. It starts at
        ``brief_chars`` (or ``brief_share`` of the result, when larger) and grows in steps up to
        ``limit`` while an error template of the original is missing from it. A brief that still misses a
        template at ``limit`` is refused (None): the ladder's ``errors`` rung keeps every template instead.
        """
        settings = self._settings
        stats = RenderStats(0, 0, 0, 0, (), "brief")
        reserve = len(build_header(parent_line, stats, ref)) + len(meta_line(pl.meta)) + 2
        first = max(settings.brief_chars, int(settings.brief_share * len(pl.inner)))
        sizes = [max(1, min(first, limit))]
        while sizes[-1] < limit:   # grow in steps, stopping at the first size that keeps every template
            sizes.append(min(limit, max(sizes[-1] + 1, sizes[-1] * 3 // 2)))
        fingerprints = error_fingerprints(pl.inner)
        for size in sizes:
            budget = size - reserve
            rendered = body = None
            for attempt in range(3):   # the reserve is an estimate: the real header carries the stats
                if budget <= 0:
                    break
                try:
                    body = brief_context(
                        pl.inner, budget_chars=budget, query=" ".join(query), gap_marker=_INLINING_ELISION,
                    )
                except Exception:
                    return None
                rendered = self._body_row(parent_line, pl, ref, "brief", body)
                if len(rendered) <= limit:
                    break
                # Whole lines: a budget a few chars lower can give the same body, so cut below the body.
                budget = min(budget, len(body)) - (len(rendered) - limit) - 64 * (attempt + 1)
                rendered = None
            if rendered is None:
                break
            if fingerprints.keys() <= error_fingerprints(body).keys():
                return rendered
        return None

    # ---- committing rows ----------------------------------------------------------------------
    def _keep_parent(self, result: list[dict], idx: int, row: dict, tool: str) -> bool:
        """Commit Hermes' own rewrite (``row``, from the shadow call)."""
        drop_stale_api_content(row)
        before = result[idx].get("content")
        result[idx] = row
        self._telemetry.count("parent_line", tool=tool)
        if isinstance(before, str) and isinstance(row.get("content"), str):
            self._telemetry.add_chars(len(before), len(row["content"]))
        return True

    def _commit(
        self, result: list[dict], idx: int, content: str, tool: str, ref: str, pl: HermesPayload,
        scope: _Pass, rung: str, rendered: str,
    ) -> bool:
        """Keep the original's text in the store and put the rendered row in the pass's list."""
        self._store.put(ref, pl.inner, tool=tool, meta=pl.meta)
        row = {**result[idx], "content": rendered}
        drop_stale_api_content(row)
        result[idx] = row
        scope.renders += 1
        if rung in _BODYLESS:
            scope.gone.append(ref)
        self._telemetry.count("render", rung=rung, tool=tool)
        self._telemetry.add_chars(len(content), len(rendered))
        self._telemetry.emit({
            "event": "render", "rung": rung, "tool": tool, "ref": ref,
            "chars_before": len(content), "chars_after": len(rendered),
        })
        return True

    def _demote_rendered(
        self, result: list[dict], idx: int, content: str, kind: str, tool: str, raw_args: Any,
    ) -> bool:
        """Cut an already rendered row down to its header (pressure pass or retained budget)."""
        msg = result[idx]
        if kind == "legacy":
            pl = parse_payload(content)
            ref = make_ref(msg, content)
            line = summarize_tool_result(tool, raw_args, content)
            new = self._header_row(line, pl, ref, "header")
            self._store.put(ref, pl.inner, tool=tool, meta=pl.meta)
        elif "\n" in content:
            header = parse_header(content)
            scope = _SCOPE.get()
            if header is not None and header["rung"] == "errors" and not (scope and idx in scope.age_demote):
                return False   # already the smallest row that keeps the errors; only its own budget retires it
            new = self._demoted_errors_row(content, header, scope) if header and header["rung"] != "errors" else None
            if new is None:
                new = retarget_rung(content, "header")
                if scope is not None and header is not None:
                    scope.gone.append(header["ref"])
        else:
            return False   # already header-only
        if new is None or new == content:
            return False
        row = {**msg, "content": new}
        drop_stale_api_content(row)
        result[idx] = row
        landed = parse_header(new)
        self._telemetry.count("render", rung=landed["rung"] if landed else "header", tool=tool, demoted="1")
        self._telemetry.add_chars(len(content), len(new))
        return True

    def _demoted_errors_row(self, content: str, header: dict, scope: _Pass | None) -> str | None:
        """The ``errors`` row an extract or brief steps down to, when its original has error templates."""
        if scope is None:
            return None
        found = self._store.entry(header["ref"])
        parent = header_parent(content)
        if found is None or parent is None:
            return None
        pl = HermesPayload(inner=found.text, field=None, meta=dict(found.meta or {}), wrapped=False)
        rendered = self._errors_row(parent, pl, header["ref"], scope.err_room)
        if rendered is not None:
            scope.err_room -= len(rendered)
        return rendered

    # ---- seam B: the lean tail ----------------------------------------------------------------
    def _demote_stale_tail_tools(self, messages, tail_start):
        """Hermes' lean tail demotion; stubs of Tameru rows (and of any other eligible raw row) become
        header-only rows that keep a ``ref``."""
        out = super()._demote_stale_tail_tools(messages, tail_start)
        if not (self._settings.enabled and self._seams.lean_tail):
            return out
        try:
            calls: dict[str, tuple[str, Any]] | None = None
            for i in range(tail_start, len(messages)):
                before, after = messages[i], out[i]
                stub = after.get("content") if after is not before else None
                if not (isinstance(stub, str) and LEAN_STUB_RE.match(stub)):
                    continue
                content = before.get("content")
                if not isinstance(content, str):
                    continue
                if calls is None:
                    calls = tool_calls_by_id(messages)
                tool, raw_args = calls.get(before.get("tool_call_id") or "", ("unknown", ""))
                if classify_render(content) == "v1":
                    new = retarget_rung(content, "header")
                elif _spill_note(content) and tool not in self._exempt:
                    pl = parse_payload(content)
                    ref = make_ref(before, content)
                    new = self._header_row(
                        self._terminal_line(summarize_tool_result(tool, raw_args, content), pl), pl, ref, "header",
                        _spill_note(content),
                    )
                    self._store.put(ref, pl.inner, tool=tool, meta=pl.meta)
                elif self._leaves_to_hermes(tool, raw_args, content):
                    continue
                else:
                    pl = parse_payload(content)
                    ref = make_ref(before, content)
                    new = self._header_row(
                        self._terminal_line(summarize_tool_result(tool, raw_args, content), pl), pl, ref, "header",
                    )
                    self._store.put(ref, pl.inner, tool=tool, meta=pl.meta)
                if new is None:
                    continue
                row = {**before, "content": new}
                drop_stale_api_content(row)
                out[i] = row
                self._telemetry.count("lean_tail", tool=tool)
        except Exception:
            self._telemetry.count("error", where="lean_tail")
        return out

    def _leaves_to_hermes(self, tool: str, raw_args: Any, content: str) -> bool:
        """True for rows whose Hermes stub must stay (exempt tools, refusals, spill previews)."""
        return (
            tool in self._exempt or refused_summary(tool, _json_args(raw_args), content) is not None
            or is_persisted_block(content) or SKILL_PRUNED_MARKER_PREFIX in content
            or classify_render(content) == "legacy"
        )

    # ---- summary ledger -----------------------------------------------------------------------
    def _augment_summary_lean(self, summary, turns_to_summarize):
        """Hermes' lean sections plus, with ``ledger`` on, the exact artifact trail."""
        out = super()._augment_summary_lean(summary, self._summary_view(turns_to_summarize))
        if not (self._settings.enabled and self._settings.ledger and self._seams.augment):
            return out
        try:
            previous = getattr(self, "_previous_summary", None) or ""
            return merge_into_summary(
                out, build_ledger(
                    turns_to_summarize, self._store, previous=previous, skip_tools=self._exempt,
                    extra_originals=self._bodyless_originals(),
                ),
            )
        except Exception:
            self._telemetry.count("error", where="ledger")
            return out

    @staticmethod
    def _summary_view(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``turns`` with the Tameru rows cut to what a summary can use (``render.for_summary``).

        Only rows that change are copied; the transcript itself is never touched.
        """
        out = []
        for msg in turns:
            content = msg.get("content") if isinstance(msg, dict) and msg.get("role") == "tool" else None
            seen = for_summary(content) if isinstance(content, str) else content
            out.append(msg if seen is content else {**msg, "content": seen})
        return out

    def _serialize_records_for_summary(self, turns):
        """Hermes' records for the summarizer, from the summary view of ``turns``."""
        if not (self._settings.enabled and self._seams.augment):
            return super()._serialize_records_for_summary(turns)
        try:
            view = self._summary_view(turns)
        except Exception:
            self._telemetry.count("error", where="summary_view")
            view = turns
        return super()._serialize_records_for_summary(view)

    def _bodyless_originals(self) -> list[str]:
        """Originals of the rows that hold no text of them any more, wherever they sit in the transcript.

        The summary covers only the middle of a compaction; a row cut down to its header and kept in the
        tail would otherwise take its errors with it. Rows cut in this pass are tracked as they are
        committed, earlier ones are found by their header in the list Hermes handed in.
        """
        scope = _SCOPE.get()
        if scope is None:
            return []
        refs = list(scope.gone)
        for msg in scope.messages or ():
            content = msg.get("content") if msg.get("role") == "tool" else None
            header = parse_header(content) if isinstance(content, str) and "\n" not in content else None
            if header is not None and header["rung"] in _BODYLESS:
                refs.append(header["ref"])
        texts = (self._store.get(ref) for ref in dict.fromkeys(refs))
        return [text for text in texts if text]

    # ---- tools --------------------------------------------------------------------------------
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = super().get_tool_schemas()
        if not (self._settings.enabled and self._settings.expand_tool):
            return schemas
        self._expand_injected = True
        return [EXPAND_SCHEMA, *schemas]

    def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if name != EXPAND_TOOL_NAME:
            return super().handle_tool_call(name, args, **kwargs)
        db = getattr(self, "_session_db", None)
        session_id = getattr(self, "_session_id", "") or ""
        lookup = None
        if session_db_usable(db):
            archived = session_lookup_factory(db, session_id, make_ref)

            def lookup(ref: str) -> str | None:
                raw = archived(ref)
                return None if raw is None else parse_payload(raw).inner

        return expand(
            self._store, args, session_lookup=lookup, session_id=session_id,
            grep_timeout=self._settings.expand_grep_timeout, grep_line_chars=self._settings.expand_grep_line_chars,
        )

    # ---- status -------------------------------------------------------------------------------
    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        db = getattr(self, "_session_db", None)
        status["tameru"] = {
            "version": _plugin_version(),
            "engine_version": ENGINE_VERSION,
            "enabled": self._settings.enabled,
            "seams": asdict(self._seams),
            "expand_injected": self._expand_injected,
            "store": {"entries": len(self._store), "chars": self._store.total_chars},
            "isolated": db is not None and not session_db_usable(db),
            "telemetry": self._telemetry.snapshot(),
            "warnings": list(self._warnings),
        }
        return status

