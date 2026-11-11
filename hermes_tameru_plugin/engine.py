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
from collections import OrderedDict, deque
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace as dataclass_replace
from pathlib import Path
from typing import Any, NamedTuple

from .adaptive import AdaptiveMixin, native_budget_kwargs
from .adaptive_full import check_attempt, full_result
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
    has_untrusted_wrapper,
    is_persisted_block,
    is_untrusted_tool,
    marker_artifact_re,
    message_budget_tokens,
    native_compaction_active,
    probe_seams,
    refused_summary,
    session_db_usable,
    summarize_tool_result,
    tool_calls_by_id,
)
from .ledger import build_ledger, merge_into_summary
from .payload import HermesPayload, content_hint, meta_line, parse_payload
from .query import build_query, usable_query
from .edits import UNCONFIRMED, EditState
from .engine_edits import EditsMixin
from .engine_host import HostSettingsMixin
from .engine_usage import UsageMixin
from .host_tuning import HostTuning
from .pins import PinState, merge_pins
from .quiet import swallowed

from .manifest import ManifestState, build_note, written_files
from .recovery import (
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
from .render import (
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

REREAD_GAP = 5          # a ref's re-read counts as a signal at most once per this many turns (a forced loop of re-reads cannot widen the tail)

# A dropped span shorter than Hermes's elision marker is kept verbatim (extract and brief alike).
_INLINING_ELISION = InliningGapMarker(elision_marker)

try:
    from agent.context_compressor import ContextCompressor as _Base
except Exception:  # Hermes absent: the module stays importable, the engine cannot be built
    _Base = None

DISPLAY_NAME = "Tameru (ためる)"

# Tools whose results the agent re-reads as instructions or state: Hermes' own summary line applies.
_EXEMPT_TOOLS = frozenset({
    "skill_view", "skills_list", "skill_manage", "clarify", "todo_list", "todo", "memory",
    "session_search", EXPAND_TOOL_NAME, READ_ORIGINAL_TOOL_NAME, "delegate_task", "cronjob_manage", "process_manage",
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

    def fork(self) -> "_Memo":
        """A same-cap copy of the bounded items; a prune pass may publish it or drop it."""
        out = type(self)(self._max, self._max_chars)
        with self._lock:
            out._items = self._items.copy()
            out._chars = self._chars
        return out

    def adopt(self, prepared: "_Memo") -> None:
        """Replace this memo's items with ``prepared``'s, keeping this identity and caps."""
        with prepared._lock:
            items = prepared._items.copy()
            chars = prepared._chars
        with self._lock:
            self._items = items
            self._chars = chars

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
    parent_lines: int = 0                         # rows shortened by Hermes' own one-liner (no original kept)
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
    changes: dict[str, Change] = field(default_factory=dict)  # staged activity records, keyed by ref
    owner: object | None = None                 # engine this scope plans for (anti cross-engine claim)
    lifecycle_id: str = ""                      # lifecycle the plan was staged under
    base_store: "OriginalStore | None" = None   # live store the plan forked from
    base_memo: "_Memo | None" = None            # live memo the plan forked from
    store: "OriginalStore | None" = None        # lazily forked shadow store
    memo: "_Memo | None" = None                 # lazily forked shadow memo
    serial: int = 0                             # transaction serial at scope creation
    host_generation: "int | None" = None        # native compression-attempt generation captured
    session_id: str = ""                        # session the plan belongs to
    policy_plans: tuple = ()                    # the plan tuple captured for this pass
    plan: "Any" = None                          # the plan this scope executes
    settings: "TameruSettings | None" = None    # immutable effective settings for this pass
    native_options: dict = field(default_factory=dict)  # per-pass native gate overrides
    policy_before_tokens: int = 0               # body tokens before the plan's stage
    policy_after_tokens: "int | None" = None    # body tokens after the selected stage
    policy_suffix_tokens: "int | None" = None   # tokens from the first changed message to the end (the rewritten cache suffix)
    policy_stage_index: int = 0
    full_local: bool = False
    prepared_prune: Any = None                  # one native replay: input, candidate, count, held telemetry


@dataclass
class _Prepared:
    scope: "_Pass"
    expected: "tuple[str, ...] | None"
    tail_user_mutable: bool
    held: "Any"


class _Transactions:
    def __init__(self):
        self.serial = 0
        self.pending = None
        self.active = False

    def clear(self):
        self.serial += 1
        self.pending = None
        self.active = False

    def __deepcopy__(self, memo):
        return type(self)()


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


class TameruContextEngine(AdaptiveMixin, EditsMixin, HostSettingsMixin, UsageMixin, _base()):  # type: ignore[misc]
    """Hermes' compressor with the Tameru tool-result ladder, recovery tool and artifact ledger."""

    name = "tameru"
    DISPLAY_NAME = DISPLAY_NAME
    display_name = DISPLAY_NAME
    _seam_report: SeamReport | None = None     # probed once per class

    @staticmethod
    def _native_tail_property():
        return getattr(_Base, "tail_token_budget", None)

    def __init__(
        self, model: str = "pending", *, settings: TameruSettings | None = None,
        _placeholder_context: bool = False, **kwargs: Any,
    ) -> None:
        if _Base is None:
            raise ImportError("hermes-tameru-plugin needs Hermes Agent (agent.context_compressor)")
        warnings: list[str] = []
        if settings is None:
            settings, warnings = load_settings()
        scaled = False
        params: set[str] = set()
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
        native_kwargs = native_budget_kwargs(settings, {**configured, **kwargs}, params)
        known_zero = type(native_kwargs.get("max_tokens")) is int and native_kwargs["max_tokens"] == 0
        self._placeholder_context = _placeholder_context
        super().__init__(model=model, **native_kwargs)
        self._settings = settings
        if known_zero:
            self.__dict__["_tameru_native_max_tokens"] = 0
        self._scaled_prune = scaled
        self._scale_prune_trigger()
        self._warnings = warnings
        self._store = OriginalStore(settings.store_max_entries, settings.store_max_chars)
        self._telemetry = Telemetry(settings.telemetry_log)
        self._manifest = ManifestState()
        self._host = HostTuning()
        self._pins = PinState()
        self._reread_turn: dict[str, int] = {}     # ref -> turn of its last counted re-read
        self._host_anchor_n: int | None = None
        self._host_saved: dict | None = None
        self._edits = EditState()
        self._edits_inflight: list[str] = []
        self._edits_digests: dict[str, str] = {}      # ref -> digest of the text the overlay put in the request
        self._edits_staged: dict[str, list[str]] = {}  # api request id -> refs its request carried, until the provider answers
        self._timing_ring: deque = deque(maxlen=8)   # (api ms, first-chunk ms or None) of the last replies
        self._usage_ring: deque = deque(maxlen=8)    # (prompt, cache_read, cache_write, input) of the last replies; ints or None
        self._memo = _Memo()
        self._transactions = _Transactions()
        self._init_policy(self._settings)
        self._activity = ActivityState()
        self._activity_registry = SessionRegistry()
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
        return f"Tameru · {message}{self._status_suffix()}"

    def _status_suffix(self) -> str:
        """One short clause on what Tameru is doing, appended to Hermes's own compaction status line.

        The host's template text stays intact at the start, so the TUI/Desktop 'compacting' indicators and the
        messaging filter, which match that template, keep working."""
        try:
            with self._activity.lifecycle_lock:
                policy = self._policy
                if policy.paused:
                    return " Tameru is paused."
                from .progress import fmt_tokens
                saved = f", {fmt_tokens(policy.saved_tokens)} saved so far" if policy.saved_tokens else ""
                posture = policy.tuner.posture() if self._settings.autotune else "fixed policy"
                return f" Tameru is {posture}{saved}."
        except Exception:
            return ""

    # ---- lifecycle ----------------------------------------------------------------------------
    def update_model(self, model: str, context_length: int, *args: Any, **kwargs: Any) -> None:
        """Seed Hermes' threshold with its Codex autoraise (host-side only for the built-in engine).

        Also with the plugin off: ``enabled: false`` is the built-in compressor, which has the autoraise."""
        call = inspect.signature(_Base.update_model).bind(self, model, context_length, *args, **kwargs)
        supplied_reserve = call.arguments.get("max_tokens")
        before = (
            getattr(self, "model", None), getattr(self, "provider", None),
            getattr(self, "base_url", None), getattr(self, "api_mode", None),
            getattr(self, "_resolved_context_length", None),
        )
        self._model_update_budget(call)
        if hasattr(self, "_config_threshold_percent"):
            try:
                provider = kwargs.get("provider", args[2] if len(args) > 2 else "")
                section = read_hermes_config()[0].get("compression")
                raised = compression_threshold(model, provider, section if isinstance(section, dict) else {})
                if raised is not None:
                    self._config_threshold_percent = raised
            except Exception:
                swallowed(self, "update_model_threshold")
        super().update_model(*call.args[1:], **call.kwargs)
        if supplied_reserve is not None:
            self.__dict__["_tameru_native_max_tokens"] = (
                0 if type(supplied_reserve) is int and supplied_reserve == 0 else None)
        self._placeholder_context = False
        after = (
            getattr(self, "model", None), getattr(self, "provider", None),
            getattr(self, "base_url", None), getattr(self, "api_mode", None), context_length,
        )
        if before != after:
            self._clear_policy_observations()
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
                    self._settings, self._policy_threshold_tokens(),
                )
            except Exception:
                swallowed(self, "prune_tokens")

    def _retained_budget(self) -> int:
        """The retained-extract budget now: the configured floor, or the window-scaled value when larger.

        Computed at use time because Hermes' compaction threshold changes with ``update_model``.
        """
        settings = self._pass_settings()
        try:
            return scaled_retained_budget(settings, self._policy_threshold_tokens())
        except Exception:
            return settings.retained_extract_budget_chars

    def _retained_error_budget(self) -> int:
        """The retained ``errors``-row budget now (``_retained_budget``'s counterpart)."""
        settings = self._pass_settings()
        try:
            return scaled_error_budget(settings, self._policy_threshold_tokens())
        except Exception:
            return settings.retained_error_budget_chars

    def clone_for_agent(self) -> TameruContextEngine:
        """A per-agent copy; the store, memo and telemetry copy as fresh empty objects."""
        return copy.deepcopy(self)

    def _discard_session_views(self, *, preserve_policy: bool = True):
        self._store.clear()
        self._store = OriginalStore(self._settings.store_max_entries, self._settings.store_max_chars)
        self._memo.clear()
        self._memo = copy.copy(self._memo)
        self._activity.clear()
        self._usage_ring.clear()
        self._edits = EditState()
        self._pins = PinState()
        self._edits_inflight = []
        self._edits_digests = {}
        self._edits_staged = {}
        self._transactions.clear()
        if getattr(self, "_policy", None) is not None:
            self._discard_policy_views(preserve_controls=preserve_policy)

    def on_session_reset(self) -> None:
        with self._activity.lifecycle_lock:
            super().on_session_reset()
            self._discard_session_views()

    def on_session_end(self, *args: Any, **kwargs: Any) -> None:
        with self._activity.lifecycle_lock:
            sid = getattr(self, "_session_id", "") or ""
            super().on_session_end(*args, **kwargs)
            self._discard_session_views()
            self._activity_registry.bind(self, sid, "")

    def on_session_start(self, session_id: str, **kwargs):
        with self._activity.lifecycle_lock:
            previous = getattr(self, "_session_id", "") or ""
            pending = self._transactions.pending
            db = getattr(self, "_session_db", None)
            rotation = (previous, session_id) if (previous and kwargs.get("boundary_reason") == "compression"
                and kwargs.get("old_session_id") == previous) else None
            self._activity_rotation = rotation
            try:
                out = super().on_session_start(session_id, **kwargs)
            finally:
                self._activity_rotation = None
        # authoritative host commit proof: actual rows of the new session
        count = 0
        if rotation is not None and pending is not None and db is not None:
            actual = None
            try:
                if callable(getattr(db, "get_messages_as_conversation", None)):
                    actual = db.get_messages_as_conversation(session_id)
                elif callable(getattr(db, "get_messages", None)):
                    rows = db.get_messages(session_id)
                    if isinstance(rows, list) and all(isinstance(m, dict) for m in rows):
                        actual = [
                            dict(m, _tool_call_uid=(m.get("_tool_call_uid")
                                                    or m.get("tool_call_uid")))
                            for m in rows]
            except Exception:
                actual = None
            if isinstance(actual, list) and all(isinstance(m, dict) for m in actual):
                with self._activity.lifecycle_lock:
                    if (pending is self._transactions.pending
                            and self._pass_is_current(pending.scope)
                            and pending.scope.session_id == previous
                            and getattr(self, "_session_id", "") == session_id
                            and getattr(self, "_session_db", None) is db):
                        count = self._accept_prepared(pending, actual)
        if count:
            self._activity_registry.notify(self, count)
        return out

    def bind_session_state(self, session_db=None, session_id=""):
        if getattr(self, "_activity", None) is None:
            # parent construction may bind before _activity exists
            return super().bind_session_state(session_db, session_id)
        with self._activity.lifecycle_lock:
            previous = getattr(self, "_session_id", "") or ""
            previous_db = getattr(self, "_session_db", None)
            current = session_id or ''
            rotation = ((previous, current) == getattr(self, '_activity_rotation', None)
                        and previous_db is session_db)
            if previous_db is not session_db or (previous != current and not rotation):
                # an unrelated rebind or a different DB object: the old session's
                # originals, table and saved preferences must not follow
                self._discard_session_views(preserve_policy=False)
            super().bind_session_state(session_db, session_id)
            from .session_policy import bind_controls
            from .session_tuner import bind_tuner
            bind_controls(self, rotation=rotation)
            bind_tuner(self, rotation=rotation)
            from .session_edits import bind_edits
            bind_edits(self, rotation=rotation)
            from .session_host import bind_host
            bind_host(self, rotation=rotation)
            from .session_pins import bind_pins
            bind_pins(self, rotation=rotation)
            registry = getattr(self, "_activity_registry", None)
            if registry is not None:
                registry.bind(self, previous, getattr(self, "_session_id", "") or "")

    def _known_render(self, msg: dict) -> Change | None:
        content = msg.get("content")
        if msg.get("role") != "tool" or not isinstance(content, str):
            return None
        fingerprint = _digest(content)
        previous = self._activity.receipt(fingerprint)
        if previous is not None and same_occurrence(previous.identity, msg):
            return previous
        scope = _SCOPE.get()
        if scope is not None and scope.owner is self:
            for change in scope.changes.values():
                if change.current_digest == fingerprint and same_occurrence(change.identity, msg):
                    return change
        return None

    def _note_activity(self, before, after, tool, original=None):
        scope = _SCOPE.get()
        if scope is None:
            return
        header = parse_header(after.get("content", ""))
        if header is None:
            return
        previous = self._known_render(before)
        change = proposed_change(before, after, tool, original=original, previous=previous)
        if change is not None:
            scope.changes[change.ref] = change

    def _observe_activity(self, messages):
        with self._activity.lifecycle_lock:
            prepared = self._transactions.pending
            confirmed = 0
            if prepared is not None:
                if not self._pass_is_current(prepared.scope):
                    self._transactions.pending = None
                else:
                    confirmed = self._accept_prepared(prepared, messages)
            self._restore_activity(messages)
            count = confirmed + self._activity.observe(messages)
        self._activity_registry.notify(self, count)

    _RESTORE_TOOL_RE = re.compile(r"\[([A-Za-z0-9_.:-]{1,64})\]")

    def _restore_activity(self, messages, *, consume: bool = True) -> int:
        """Rebuild the activity table after a restart from rows Tameru already shortened.

        A row counts only if an archived original reproduces its header ref for that exact message
        (``make_ref`` binds the ref to the message id and the original's hash), so a header printed by a
        tool or a document proves nothing. Recovered rows are marked ``recovered_after_restart``. Needs the session archive;
        without it the attempt is repeated on a later observation. Never posts a notification.
        """
        if not self._activity.restore_due():
            return 0
        lookup = self._session_original_lookup()
        if lookup is None:
            return 0
        changes = []
        try:
            for row in messages:
                content = row.get("content") if isinstance(row, dict) else None
                if row.get("role") != "tool" or not isinstance(content, str) or TAMERU_TAG not in content[:400]:
                    continue
                header = parse_header(content)
                if header is None:
                    continue
                original = lookup.vouch(header["ref"], row, make_ref_from_digest)
                if original is None:
                    continue
                named = self._RESTORE_TOOL_RE.match(content)
                change = proposed_change(dict(row, content=original), row, named.group(1) if named else "unknown",
                                         original=original, previous=None)
                if change is not None:
                    changes.append(dataclass_replace(change, recovered_after_restart=True))
        except Exception:
            self._telemetry.count("error", where="activity_restore")
            if consume:
                self._activity.restore_done()
            return 0
        if consume or changes:
            self._activity.restore_done()           # an ask-time read that found nothing leaves the turn-time one
        return self._activity.confirm(changes[-self._activity.limit:], messages) if changes else 0

    @staticmethod
    def _prepared_matches(prepared: _Prepared, messages: list[dict]) -> bool:
        expected = prepared.expected
        if expected is None:
            return False
        if message_signatures(messages[:len(expected)]) == expected:
            return True
        if prepared.tail_user_mutable and expected:
            cut = len(expected) - 1
            return (len(messages) > cut and messages[cut].get('role') == 'user'
                    and message_signatures(messages[:cut]) == expected[:cut])
        return False

    def _accept_prepared(self, prepared: _Prepared, messages: list[dict]) -> int:
        """Commit a prepared transaction: caches, held telemetry and activity, under the lock."""
        if prepared is not self._transactions.pending or not self._prepared_matches(prepared, messages):
            return 0
        if prepared.scope.plan is not None:
            prepared.scope.policy_after_tokens = message_budget_tokens(messages[:len(prepared.expected)])
        if not self._publish_pass_state(prepared.scope):
            return 0
        prepared.held.apply()
        count = self._activity.confirm(prepared.scope.changes.values(), messages, allow_missing=True)
        self._transactions.pending = None
        return count

    def _lazy_tool_counts(self) -> dict | None:
        """What the ``lazy_tool`` middleware did with this chat's requests (hidden / listed / unsupported ...), or None when it is off."""
        if not self._settings.lazy_tool:
            return None
        counts = dict(self._telemetry.snapshot()["events"].get("lazy_tool", {}).get("labels", {}).get("state", {}))
        unmatched = getattr(getattr(self, "_activity_registry", None), "lazy_unmatched", 0)
        if unmatched:
            counts["no_chat_found"] = unmatched
        return counts

    def update_from_response(self, usage):
        """Keep the provider's own numbers for the last few replies, then let Hermes do its accounting."""
        callback = getattr(super(), "update_from_response", None)
        if callable(callback):
            callback(usage)
        self.confirm_edits_applied(None)             # a reply came back: whatever the last request carried reached the provider
        try:
            def count(key):
                value = usage.get(key)
                return value if type(value) is int and value >= 0 else None
            self._responses = getattr(self, "_responses", 0) + 1
            self._usage_ring.append((count("prompt_tokens"), count("cache_read_tokens"),
                                     count("cache_write_tokens"), count("input_tokens")))
        except Exception:
            pass                                    # a malformed usage dict must never disturb Hermes' accounting

    # ---- Keep and Restore: the request overlay --------------------------------------------------------------

    # ---- adaptive host settings --------------------------------------------------------------------------------














    def manifest_for_turn(self, messages) -> str | None:
        """The note for the model about this turn, or None. Cheap, never raises, off with ``manifest: off`` / ``TAMERU_MANIFEST=off``."""
        try:
            if not self._settings.manifest:
                return None
            with self._activity.lifecycle_lock:
                if isinstance(messages, list) and self._activity.restore_due():
                    # The first turn after a restart: rebuild the rows from the history Hermes just handed over (only
                    # rows an archived original vouches for), because nothing has asked for the panel yet.
                    self._restore_activity(messages, consume=False)
                shortened = sum(1 for row in self._activity.rows() if row.live)
                return build_note(self._manifest, shortened, written_files(messages))
        except Exception:
            return None



    def on_turn_complete(self, messages, usage=None, **kwargs):
        callback = getattr(super(), "on_turn_complete", None)
        if callable(callback):
            callback(messages, usage, **kwargs)
        self._observe_activity(messages)

    def activity_snapshot(self):
        with self._activity.lifecycle_lock:
            return self._activity_snapshot_locked()

    def _restore_from_session(self) -> None:
        """After a restart, rebuild the activity table from the chat's stored messages, without waiting for a turn.

        ``/tameru`` and the Desktop bar ask before any turn has run in a fresh process; the rows Tameru shortened
        earlier are already in the stored conversation. Nothing is read when there is no usable session archive.
        """
        db = getattr(self, "_session_db", None)
        sid = getattr(self, "_session_id", "") or ""
        if not sid or not session_db_usable(db):
            return
        rows = None
        try:
            if callable(getattr(db, "get_messages_as_conversation", None)):
                rows = db.get_messages_as_conversation(sid)
            elif callable(getattr(db, "get_messages", None)):
                fetched = db.get_messages(sid)
                if isinstance(fetched, list) and all(isinstance(m, dict) for m in fetched):
                    rows = [dict(m, _tool_call_uid=(m.get("_tool_call_uid") or m.get("tool_call_uid"))) for m in fetched]
        except Exception:
            return
        if isinstance(rows, list) and all(isinstance(m, dict) for m in rows):
            self._restore_activity(rows, consume=False)

    def _activity_snapshot_locked(self):
        if self._activity.restore_due() and self._activity.stored_read_once():
            self._restore_from_session()
        items = []
        archive_available = session_db_usable(getattr(self, "_session_db", None))
        for change in self._activity.rows():
            found = self._store.peek(change.ref)
            if found is not None and activity_digest(found.text) == change.original_digest:
                availability = "memory"
            else:
                availability = self._activity.original_check(change.ref)
                if availability is None:
                    availability = "unchecked" if archive_available else "unavailable"
            items.append({
                "ref": change.ref, "tool": change.tool,
                "operation": ACTIVITY_LABELS.get(change.rung, "Context changed"),
                "before_lines": change.before_lines, "kept_lines": change.kept_lines,
                "before_chars": change.before_chars, "after_chars": change.after_chars,
                "before_tokens": -(-change.before_chars // 4), "after_tokens": -(-change.after_chars // 4),
                "original": availability,
                "external": change.external, "live": change.live,
                "context_state": change.context_state, "recovered_after_restart": change.recovered_after_restart,
                "actor": change.actor, "why": change.why, "saved_chars": change.saved_chars,
                "reversible": availability in ("memory", "archive", "unchecked"),
            })
        policy = self.get_policy_status()
        from .policy_commands import policy_presentation
        policy["display"] = policy_presentation(policy)
        from .progress import SCHEMA, build_snapshot
        progress = build_snapshot(self)
        from .progress import PANEL_BUILD
        from .recovery import SCREEN_REFUSALS
        from .ledger import LEDGER_DROPPED
        progress["doctor"] = {"version": _plugin_version(), "engine_version": ENGINE_VERSION, "autotune": bool(self._settings.autotune),
                              "panel_build": PANEL_BUILD, "schema": SCHEMA,
                              "secret_refusals": SCREEN_REFUSALS["count"],
                              "ledger_lines_dropped": LEDGER_DROPPED["count"],
                              "applied_unconfirmed": UNCONFIRMED["count"],
                              "errors_ignored": self._telemetry.snapshot()["events"].get("error", {}).get("count", 0),
                              "refused_commands": getattr(self._activity_registry, "refused_verbs", 0),
                              "archive": archive_available,
                              "lazy_tool": self._lazy_tool_counts(),
                              "native_compaction": native_compaction_active(
                                  getattr(self, "model", None), getattr(self, "provider", None), getattr(self, "base_url", None)),
                              "active": bool(self._settings.enabled and self._seams.demote)}
        return {"schema": SCHEMA, "progress": progress,
                "session_id": getattr(self, "_session_id", "") or "", "items": items,
                "enabled": self._settings.enabled,
                "lifecycle_id": self._activity.lifecycle_id,
                "policy": policy,
                "edits": self._edits.snapshot(),
                "pins": list(self._pins.items),
                "capabilities": {"view": True, "restore": self.edits_enabled(), "keep": self.edits_enabled(),
                                 "pause": False, "policy": policy["controls_available"],
                                 "pins": bool(self._settings.enabled and self._settings.pins)}}

    def _session_original_lookup(self):
        db = getattr(self, "_session_db", None)
        session_id = getattr(self, "_session_id", "") or ""
        if not session_id or not session_db_usable(db):
            return None
        return session_lookup_factory(
            db, session_id, make_ref, project=lambda raw: parse_payload(raw).inner)

    def inspect_original(self, ref, *, offset=0, max_chars=12_000):
        if not isinstance(ref, str) or re.fullmatch(r"(?:[0-9a-f]{16}|[0-9a-f]{8})", ref) is None:
            return {"error": "Use a valid original-output reference."}
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 50_000:
            return {"error": "Invalid preview range."}
        with self._activity.lifecycle_lock:
            lifecycle_id = self._activity.lifecycle_id
            change = self._activity.get(ref)
            store = self._store
            lookup = self._session_original_lookup()
        entry, source = lookup_original(store, ref, lookup, cache=False)
        text = screen_original(entry.text) if entry is not None else None
        if text is not None and change is not None and activity_digest(text) != change.original_digest:
            entry = None
            text = screen_original(lookup(ref)) if lookup is not None else None
            source = "archive"
        with self._activity.lifecycle_lock:
            if lifecycle_id != self._activity.lifecycle_id or store is not self._store:
                return {"error": "The chat changed while the original was being read. Open context details again."}
            if source == SECRET_SOURCE:
                return {"error": "Held a secret, so Tameru does not keep or show this original.", "ref": ref}
            if text is None or (change is not None and activity_digest(text) != change.original_digest):
                if change is not None:
                    self._activity.record_original_check(ref, False, lifecycle_id=lifecycle_id)
                return {"error": "The original is unavailable for this result.", "ref": ref}
            if source == "archive":
                self._activity.record_original_check(ref, True, lifecycle_id=lifecycle_id)
            if offset > len(text):
                return {"error": "The preview starts beyond the original output.", "ref": ref}
            end = min(len(text), offset + max_chars)
            external = bool((change and change.external) or has_untrusted_wrapper(text)
                            or (entry and is_untrusted_tool(entry.tool))
                            or (source == "archive" and change is None))
            page = text[offset:end]
            result = {"ref": ref, "content": page, "offset": offset, "end_offset": end,
                      "next_offset": end if end < len(text) else None, "total_chars": len(text),
                      "external": external, "source": source, "read_only": True,
                      "lifecycle_id": lifecycle_id}
            if change is not None and change.kept:
                # Per line of this page: 1 = Tameru kept it, 0 = left out. Blank lines count as kept (nothing to recover).
                result["first_line"] = text.count("\n", 0, offset) + 1
                result["kept_flags"] = [1 if not line.strip() or line_key(line) in change.kept else 0
                                        for line in page.split("\n")][:5000]
                result["tool"] = change.tool
            return result

    # ---- public overrides: scope only ---------------------------------------------------------
    def _settings_for_plan(self, plan):
        if plan is None:
            return self._settings
        return dataclass_replace(self._settings,
            max_extract_chars=plan.retention.extract_chars,
            brief_chars=plan.retention.brief_chars,
            retained_extract_budget_chars=plan.retention.retained_chars,
            retained_extract_budget_ratio=0.0,
            retained_error_budget_chars=plan.retention.error_chars,
            small_window_threshold_tokens=0,
            errors_row_chars=plan.retention.error_row_chars)

    def _new_scope(self, messages: list[dict] | None, **fields: Any) -> _Pass:
        with self._activity.lifecycle_lock:
            self._transactions.clear()
            fields.setdefault("settings", self._settings)
            plan = fields.get("plan")
            if plan is not None:
                fields["settings"] = self._settings_for_plan(plan)
            self._transactions.active = True
            return _Pass(budget=self._settings.pass_char_budget, messages=messages,
                         owner=self, lifecycle_id=self._activity.lifecycle_id,
                         base_store=self._store, base_memo=self._memo,
                         serial=self._transactions.serial,
                         host_generation=getattr(self, "_compression_attempt_generation", None),
                         session_id=getattr(self, "_session_id", "") or "",
                         **fields)

    def _pass_settings(self) -> TameruSettings:
        scope = _SCOPE.get()
        if (scope is not None and scope.owner is self and self._transactions.active
                and scope.serial == self._transactions.serial and scope.settings is not None):
            return scope.settings
        return self._settings

    def _working_store(self) -> OriginalStore:
        scope = _SCOPE.get()
        if scope is None or scope.owner is not self:
            return self._store
        if scope.store is None:
            with self._activity.lifecycle_lock:
                if scope.store is None:
                    scope.store = scope.base_store.fork()
        return scope.store

    def _working_memo(self) -> "_Memo":
        scope = _SCOPE.get()
        if scope is None or scope.owner is not self:
            return self._memo
        if scope.memo is None:
            with self._activity.lifecycle_lock:
                if scope.memo is None:
                    scope.memo = scope.base_memo.fork()
        return scope.memo

    def _candidate_scope(self, anchor: _Pass, plan: Any = None, **fields: Any) -> _Pass | None:
        """A sibling stage scope sharing the anchor's epoch; None when the anchor went stale."""
        with self._activity.lifecycle_lock:
            if not self._pass_is_current(anchor):
                return None
            scope = _Pass(
                budget=anchor.budget, messages=anchor.messages, focus=anchor.focus,
                full=anchor.full, owner=self, lifecycle_id=anchor.lifecycle_id,
                base_store=anchor.base_store, base_memo=anchor.base_memo,
                serial=anchor.serial, host_generation=anchor.host_generation,
                session_id=anchor.session_id, plan=plan,
                policy_plans=anchor.policy_plans, **fields)
            scope.settings = self._settings_for_plan(plan) if plan is not None else anchor.settings
            scope.native_options = dict(anchor.native_options)
            return scope

    def _close_pass(self, scope: _Pass) -> None:
        """Expire a pass's scope overrides; copied contexts must not keep them."""
        with self._activity.lifecycle_lock:
            if scope.serial == self._transactions.serial:
                self._transactions.active = False

    def _pass_is_current(self, scope: _Pass) -> bool:
        return (scope.owner is self
                and scope.lifecycle_id == self._activity.lifecycle_id
                and scope.base_store is self._store and scope.base_memo is self._memo
                and scope.serial == self._transactions.serial
                and scope.host_generation == getattr(self, "_compression_attempt_generation", None))

    def _publish_pass_state(self, scope: _Pass) -> bool:
        """Adopt a pass's shadow caches when its plan actually committed."""
        with self._activity.lifecycle_lock:
            if not self._pass_is_current(scope):
                return False
            if scope.store is not None:
                self._store.adopt(scope.store)
            if scope.memo is not None:
                self._memo.adopt(scope.memo)
            if scope.plan is not None:
                self._record_commit(scope)
            return True

    def prune_tool_results_only(self, messages, current_tokens=None, *args, **kwargs):
        """Hermes' gated prune with the ladder active; the result object is Hermes' own.

        When Hermes declines to commit (its min-reclaim gate) although rows were rendered, the pass
        runs once more with header-only rungs, which reclaim more.
        """
        parent = super().prune_tool_results_only
        self._observe_activity(messages)
        if not (self._settings.enabled and self._seams.demote):
            return parent(messages, current_tokens, *args, **kwargs)
        if self._policy.controls.mode != "legacy":
            return self._adaptive_prune(messages, current_tokens, *args, **kwargs)
        scope = self._new_scope(messages)
        token = _SCOPE.set(scope)
        started = time.perf_counter()
        try:
            initial = self._regrowth_trigger(self._raw_native("proactive_prune_tokens"))
            if initial <= 0 or (current_tokens is not None and current_tokens < initial):
                return messages, 0
            scope.native_options["proactive_prune_tokens"] = initial
            scope.native_options["proactive_prune_min_reclaim_tokens"] = self._reprune_min_reclaim(
                messages, self._raw_native("proactive_prune_min_reclaim_tokens"))
            # Counters of a pass Hermes declines to commit (it returns the input object) are dropped:
            # nothing was rewritten, so no row was rendered and no character saved.
            with self._telemetry.hold() as held:
                res = parent(messages, current_tokens, *args, **kwargs)
            final_scope = _SCOPE.get()
            if res[0] is messages or res[1] <= 0:
                if final_scope is not None and final_scope.degrade:
                    self._telemetry.count("escalation")   # the escalate-and-decline pass did run
                return messages, 0
            if final_scope is not None:
                with self._activity.lifecycle_lock:
                    if self._publish_pass_state(final_scope):
                        held.apply()
                        if final_scope.degrade:
                            self._telemetry.count("escalation")
                        count = self._activity.confirm(final_scope.changes.values(), res[0])
                        self._activity.refresh(res[0])
                    else:
                        count = 0
                self._activity_registry.notify(self, count)
            self._arm_regrowth(res[0])
            return res
        finally:
            self._close_pass(scope)
            _SCOPE.reset(token)
            self._telemetry.time(time.perf_counter() - started)

    def _regrowth_active(self) -> bool:
        """The regrowth re-arm applies to the plugin's own, window-scaled trigger; a user's or caller's value is Hermes'."""
        return (
            bool(getattr(self, "_scaled_prune", False)) and self._settings.prune_regrowth_ratio > 0
            and prune_trigger_follows_window(self._settings, self._policy_threshold_tokens())
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
            or not reprune_gate_applies(self._settings, self._policy_threshold_tokens())
        ):
            return min_reclaim
        try:
            target = round(ratio * int(self._policy_threshold_tokens() or 0))
            return max(min_reclaim, message_budget_tokens(messages) - target) if target > 0 else min_reclaim
        except Exception:
            return min_reclaim

    def _arm_regrowth(self, pruned: list[dict], gap: int = 0) -> None:
        """After a commit, shorten Hermes' durable rearm runway to the pass's regrowth interval.

        Hermes sets the next rearm at the retained body plus ``max(reclaimed, trigger, min_reclaim)``;
        the plugin's regrowth floor shortens it to ``body + max(gap, floor, min_reclaim)`` so the next
        prune follows the configured interval rather than a full trigger-sized runway. Only the plugin's
        own window-scaled trigger may shorten the runway (``_regrowth_active``); a user's or caller's
        explicit trigger leaves Hermes' value alone. ``gap <= 0`` likewise keeps the native runway.
        """
        if not self._regrowth_active():
            return
        if gap <= 0:
            gap = prune_regrowth_tokens(self._settings, self._policy_threshold_tokens())
        rearm = getattr(self, "_proactive_prune_rearm_tokens", 0)
        if gap <= 0 or not isinstance(rearm, int) or isinstance(rearm, bool) or rearm <= 0:
            return
        try:
            lowered = message_budget_tokens(pruned) + max(
                gap, int(getattr(self, "proactive_prune_min_reclaim_tokens", 0)))
            if lowered < rearm:
                self._proactive_prune_rearm_tokens = lowered
                try:
                    from agent.context_compressor import PROACTIVE_PRUNE_REARM_MODEL_CONFIG_KEY
                except Exception:
                    PROACTIVE_PRUNE_REARM_MODEL_CONFIG_KEY = "_proactive_prune_rearm_tokens"
                # Hermes persists its full runway inside the prune's own durable commit; a resumed
                # session must reload the same shortened mark the live engine now follows.
                self._durable_write("patch_session_model_config", "proactive prune rearm floor",
                                    {PROACTIVE_PRUNE_REARM_MODEL_CONFIG_KEY: lowered})
        except Exception:
            self._telemetry.count("error", where="regrowth")


    def compress(
        self, messages, current_tokens=None, focus_topic=None, force=False, memory_context="",
        **kwargs,
    ):
        """Hermes' compression with the ladder active; ``kwargs`` (``bypass_cooldown``) pass through."""
        parent = super().compress
        fenced = check_attempt(self)
        self._observe_activity(messages)
        if not (self._settings.enabled and self._seams.demote):
            return parent(
                messages, current_tokens=current_tokens, focus_topic=focus_topic, force=force,
                memory_context=memory_context, **kwargs,
            )
        scope = self._new_scope(messages, focus=focus_topic, full=True)
        token = _SCOPE.set(scope)
        started = time.perf_counter()
        count_before = getattr(self, "compression_count", 0)
        final_scope = None
        try:
            with self._telemetry.hold() as held:
                result = full_result(
                    self, parent, super()._prune_old_tool_results, scope, messages,
                    current_tokens, focus_topic, force, memory_context, kwargs, fenced=fenced)
        finally:
            final_scope = _SCOPE.get()
            self._close_pass(scope)
            _SCOPE.reset(token)
            self._telemetry.time(time.perf_counter() - started)
        if final_scope is None or final_scope.owner is not self:
            final_scope = scope
        # prepare only when the parent produced a committed-looking new candidate;
        # a no-op result carries no cache, telemetry or UI claim.
        if isinstance(result, list) and result and result is not messages and (
            getattr(self, "compression_count", 0) > count_before
            or any(context_state(change, [m for m in result if m.get("role") == "tool"]) == "present"
                   for change in final_scope.changes.values())
        ):
            expected = message_signatures(result)
            tail_user_mutable = bool(
                len(result) > 1 and result[-1].get("role") == "user"
                and expected is not None
                and expected[:-1] != message_signatures(messages[:len(result) - 1])
            )
            with self._activity.lifecycle_lock:
                if (expected is not None and expected != message_signatures(messages)
                        and self._pass_is_current(final_scope)):
                    if final_scope.plan is not None:
                        final_scope.policy_after_tokens = message_budget_tokens(result)
                    self._transactions.pending = _Prepared(
                        final_scope, expected, tail_user_mutable, held)
        return result

    def select_context(self, request_messages, *, conversation_messages=None,
                       incoming_message=None, budget_tokens=0):
        """Hermes' context assembly seam: an installed conversation confirms the prepared plan."""
        parent = getattr(super(), "select_context", None)
        if parent is None:
            return None
        if isinstance(conversation_messages, list):
            self._observe_activity(conversation_messages)
        selected = parent(request_messages, conversation_messages=conversation_messages,
                          incoming_message=incoming_message, budget_tokens=budget_tokens)
        overlaid = self._edits_overlay(selected if isinstance(selected, list) and selected else request_messages)
        return overlaid if overlaid is not None else selected

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
        replay_scope = _SCOPE.get()
        if replay_scope is not None and replay_scope.owner is self and replay_scope.prepared_prune is not None:
            expected, candidate, count, held = replay_scope.prepared_prune
            replay_scope.prepared_prune = None
            with self._activity.lifecycle_lock:
                if not self._pass_is_current(replay_scope):
                    return messages, 0
                if messages is not expected:
                    raise RuntimeError('Native full-prune input changed during preparation')
                held.apply()
                return candidate, count
        if protect_tail_tokens is None and self._token_tail_scope() is not None:
            budget = getattr(self, "tail_token_budget", None)
            if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
                protect_tail_tokens = budget
        scope = _SCOPE.get()
        if (scope is None or scope.owner is not self or scope.full):
            return super()._prune_old_tool_results(
                messages, protect_tail_count, protect_tail_tokens, *args, **kwargs)
        if scope.policy_plans:
            return self._adaptive_candidates(
                super()._prune_old_tool_results, messages, protect_tail_count,
                protect_tail_tokens, *args, **kwargs)
        with self._telemetry.hold() as held:
            candidate_msgs, n = super()._prune_old_tool_results(
                messages, protect_tail_count, protect_tail_tokens, *args, **kwargs)
            if not self._pass_is_current(scope):
                return messages, 0
            gain = message_budget_tokens(messages) - message_budget_tokens(candidate_msgs)
            choose_first = (not scope.renders or scope.degrade or n == 0
                            or gain >= self.proactive_prune_min_reclaim_tokens)
            held.keep = choose_first
        if choose_first:
            return candidate_msgs, n
        retry_scope = self._candidate_scope(scope, degrade=True)
        if retry_scope is None:
            return messages, 0
        _SCOPE.set(retry_scope)
        with self._telemetry.hold() as held2:
            candidate2 = super()._prune_old_tool_results(
                messages, protect_tail_count, protect_tail_tokens, *args, **kwargs)
            if not self._pass_is_current(retry_scope):
                return messages, 0
            held2.keep = True
        return candidate2

    def _prune_boundary(self, result, protect_tail_count, protect_tail_tokens):
        """First index of the protected tail; in a token-tail prune never earlier than Hermes' count tail."""
        boundary = super()._prune_boundary(result, protect_tail_count, protect_tail_tokens)
        scope = _SCOPE.get()
        planned = scope is not None and scope.owner is self and scope.plan is not None
        if protect_tail_tokens and self._token_tail_scope() is not None and not planned:
            boundary = max(boundary, len(result) - protect_tail_count)
        if scope is not None and scope.owner is self and isinstance(boundary, int):
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
            if isinstance(content, str) and "\n" in content and self._known_render(msg) is not None:
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
        settings = self._pass_settings()
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

        kind = ("v1" if self._known_render(msg) is not None
                else ("legacy" if classify_render(content) == "legacy" else None))
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
        if free < capped or error_fingerprints(pl.inner, status_rows=False):
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
            floor = max(self._pass_settings().min_tool_chars, min_prune_chars + 1)
            pending = []
            for j in range(min(boundary, len(result))):
                msg = result[j]
                content = msg.get("content")
                if (
                    msg.get("role") == "tool" and isinstance(content, str) and len(content) >= floor
                    and self._known_render(msg) is None and not is_persisted_block(content)
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
        settings = self._pass_settings()
        inner = parse_payload(content).inner
        size = max(settings.brief_chars, int(settings.brief_share * len(inner)))
        if error_fingerprints(inner, status_rows=False):
            size = size * 3 // 2
        return min(size, settings.max_extract_chars, int((1 - settings.min_savings) * len(content)))

    # ---- ladder rungs -------------------------------------------------------------------------
    def _errors_body(self, inner: str) -> str:
        """One exemplar line per error template of ``inner``, in order, within ``errors_row_chars``."""
        cap = self._pass_settings().errors_row_chars
        if cap <= 0:
            return ""
        lines: list[str] = []
        used = 0
        for line in error_fingerprints(inner, status_rows=False).values():
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
        found = self._working_memo().get(key)
        if found is not None:
            return found
        settings = self._pass_settings()
        fixed = {} if ratio is None else {"mode": "fixed", "budget_ratio": ratio}
        try:
            res = compress_context(
                inner, query, ccr=False, citations=False, content_hint=hint,
                pin_patterns=list(settings.protect_patterns) or None, gap_marker=elision_marker,
                inline_short_gaps=True,
                anchor_errors=settings.anchor_errors,
                prose_brief=settings.prose_brief,
                recursion_markers=(TAMERU_TAG.strip(),), min_savings_ratio=settings.min_savings,
                **fixed,
            )
        except Exception:
            return None
        out = _Extract(res.compressed_text, bool(res.fail_open), res.compression_risk)
        if len(out.text) <= _MEMO_MAX_TEXT:
            self._working_memo().put(key, out)
        return out

    def _acceptable(self, out: _Extract | None, fingerprints: dict[str, str]) -> bool:
        """Not fail-open, within ``max_risk`` and an exemplar of every error of the original kept."""
        if out is None or out.fail_open:
            return False
        ceiling = _RISK_ORDER.get(self._settings.max_risk, 1)
        if _RISK_ORDER.get(out.risk or "", 2) > ceiling:
            return False
        return fingerprints.keys() <= error_fingerprints(out.text, status_rows=False).keys()

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
        return render(parent_line, stats, ref, meta_line=meta_line(pl.meta), body=body,
                      external=has_untrusted_wrapper(pl.inner))

    def _extract_row(
        self, parent_line: str, pl: HermesPayload, ref: str, query: list[str], hint: str | None,
        limit: int,
    ) -> str | None:
        """The query-aware extract row, within ``limit`` chars; fixed-budget retries if too big."""
        if limit <= 0:
            return None
        fingerprints = error_fingerprints(pl.inner, status_rows=False)
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
            if not error_fingerprints(line, status_rows=False) and line in out.text
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
        settings = self._pass_settings()
        stats = RenderStats(0, 0, 0, 0, (), "brief")
        reserve = len(build_header(parent_line, stats, ref)) + len(meta_line(pl.meta)) + 2
        first = max(settings.brief_chars, int(settings.brief_share * len(pl.inner)))
        sizes = [max(1, min(first, limit))]
        while sizes[-1] < limit:   # grow in steps, stopping at the first size that keeps every template
            sizes.append(min(limit, max(sizes[-1] + 1, sizes[-1] * 3 // 2)))
        fingerprints = error_fingerprints(pl.inner, status_rows=False)
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
            if fingerprints.keys() <= error_fingerprints(body, status_rows=False).keys():
                return rendered
        return None

    # ---- committing rows ----------------------------------------------------------------------
    def _keep_parent(self, result: list[dict], idx: int, row: dict, tool: str) -> bool:
        """Commit Hermes' own rewrite (``row``, from the shadow call)."""
        drop_stale_api_content(row)
        before = result[idx].get("content")
        result[idx] = row
        self._telemetry.count("parent_line", tool=tool)
        scope = _SCOPE.get()
        if scope is not None and scope.owner is self:
            scope.parent_lines += 1                 # reported on the pass's timeline event; no original is kept for it
        if isinstance(before, str) and isinstance(row.get("content"), str):
            self._telemetry.add_chars(len(before), len(row["content"]))
        return True

    def _commit(
        self, result: list[dict], idx: int, content: str, tool: str, ref: str, pl: HermesPayload,
        scope: _Pass, rung: str, rendered: str,
    ) -> bool:
        """Keep the original's text in the store and put the rendered row in the pass's list."""
        before = result[idx]
        self._working_store().put(ref, pl.inner, tool=tool, meta=pl.meta)
        row = {**result[idx], "content": rendered}
        drop_stale_api_content(row)
        result[idx] = row
        self._note_activity(before, row, tool, pl.inner)
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
        original = None
        if kind == "legacy":
            pl = parse_payload(content)
            ref = make_ref(msg, content)
            line = summarize_tool_result(tool, raw_args, content)
            new = self._header_row(line, pl, ref, "header")
            self._working_store().put(ref, pl.inner, tool=tool, meta=pl.meta)
            original = pl.inner
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
        self._note_activity(msg, row, tool, original)
        landed = parse_header(new)
        self._telemetry.count("render", rung=landed["rung"] if landed else "header", tool=tool, demoted="1")
        self._telemetry.add_chars(len(content), len(new))
        return True

    def _demoted_errors_row(self, content: str, header: dict, scope: _Pass | None) -> str | None:
        """The ``errors`` row an extract or brief steps down to, when its original has error templates."""
        if scope is None:
            return None
        found = self._working_store().entry(header["ref"])
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
                original = None
                before, after = messages[i], out[i]
                stub = after.get("content") if after is not before else None
                if not (isinstance(stub, str) and LEAN_STUB_RE.match(stub)):
                    continue
                if SKILL_PRUNED_MARKER_PREFIX in stub:
                    continue  # keep Hermes' reload marker verbatim
                content = before.get("content")
                if not isinstance(content, str):
                    continue
                if calls is None:
                    calls = tool_calls_by_id(messages)
                tool, raw_args = calls.get(before.get("tool_call_id") or "", ("unknown", ""))
                if self._known_render(before) is not None:
                    new = retarget_rung(content, "header")
                elif _spill_note(content) and tool not in self._exempt:
                    pl = parse_payload(content)
                    ref = make_ref(before, content)
                    new = self._header_row(
                        self._terminal_line(summarize_tool_result(tool, raw_args, content), pl), pl, ref, "header",
                        _spill_note(content),
                    )
                    self._working_store().put(ref, pl.inner, tool=tool, meta=pl.meta)
                    original = pl.inner
                elif self._leaves_to_hermes(tool, raw_args, content):
                    continue
                else:
                    pl = parse_payload(content)
                    ref = make_ref(before, content)
                    new = self._header_row(
                        self._terminal_line(summarize_tool_result(tool, raw_args, content), pl), pl, ref, "header",
                    )
                    self._working_store().put(ref, pl.inner, tool=tool, meta=pl.meta)
                    original = pl.inner
                if new is None:
                    continue
                row = {**before, "content": new}
                drop_stale_api_content(row)
                self._note_activity(before, row, tool, original)
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
        if self._settings.enabled and self._settings.pins and self._seams.augment:
            try:
                out = merge_pins(out, self._pins)         # the user's own lines, word for word, in every summary
            except Exception:
                self._telemetry.count("error", where="pins")
        if not (self._settings.enabled and self._settings.ledger and self._seams.augment):
            return out
        try:
            previous = getattr(self, "_previous_summary", None) or ""
            return merge_into_summary(
                out, build_ledger(
                    turns_to_summarize, self._working_store(), previous=previous, skip_tools=self._exempt,
                    extra_originals=self._bodyless_originals(),
                    is_rendered=lambda msg: self._known_render(msg) is not None,
                    is_external=lambda msg: bool((change := self._known_render(msg)) and change.external),
                ),
            )
        except Exception:
            self._telemetry.count("error", where="ledger")
            return out

    def _summary_view(self, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``turns`` with the Tameru rows cut to what a summary can use (``render.for_summary``).

        Only rows that change are copied; the transcript itself is never touched.
        """
        out = []
        for msg in turns:
            content = msg.get("content") if isinstance(msg, dict) and msg.get("role") == "tool" else None
            seen = (for_summary(content) if isinstance(content, str) and self._known_render(msg) is not None
                    else content)
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
            header = (parse_header(content)
                      if isinstance(content, str) and "\n" not in content and self._known_render(msg) is not None
                      else None)
            if header is not None and header["rung"] in _BODYLESS:
                refs.append(header["ref"])
        texts = (self._working_store().get(ref) for ref in dict.fromkeys(refs))
        return [text for text in texts if text]

    # ---- tools --------------------------------------------------------------------------------
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = super().get_tool_schemas()
        if not (self._settings.enabled and self._settings.expand_tool):
            return schemas
        self._expand_injected = True
        # One schema, not two: the old name ``tameru_expand`` has the same parameters, and listing both made every request
        # carry the same ~1,600 characters twice. ``handle_tool_call`` still answers the old name for transcripts that use it.
        return [READ_ORIGINAL_SCHEMA, *schemas]

    def handle_tool_call(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if name not in (EXPAND_TOOL_NAME, READ_ORIGINAL_TOOL_NAME):
            return super().handle_tool_call(name, args, **kwargs)
        with self._activity.lifecycle_lock:
            lifecycle_id = self._activity.lifecycle_id
            store = self._store
            session_id = getattr(self, "_session_id", "") or ""
            lookup = self._session_original_lookup()
        result = expand(
            store, args, session_lookup=lookup, session_id=session_id,
            grep_timeout=self._settings.expand_grep_timeout, grep_line_chars=self._settings.expand_grep_line_chars,
        )
        with self._activity.lifecycle_lock:
            if lifecycle_id != self._activity.lifecycle_id or store is not self._store:
                return json.dumps({"error": "The chat changed while the original was being read. "
                                           "Retry in the current chat."})
            self._note_reread(args, result)
            return result

    def _note_reread(self, args: Any, result: str) -> None:
        """The model asked for an original it had been given a shortened row for: the tuner keeps more detail."""
        try:
            ref = args.get("ref") if isinstance(args, dict) else None
            if (isinstance(ref, str) and len(ref) >= 8 and not json.loads(result).get("error")):
                match = next((change.ref for change in self._activity.rows() if change.ref.startswith(ref)), None)
                turn = self._edits.turn
                if match is None or turn - self._reread_turn.get(match, -REREAD_GAP) < REREAD_GAP:
                    return                           # an unknown ref, or the same one again too soon: not a new signal
                if len(self._reread_turn) >= 256:
                    self._reread_turn.clear()
                self._reread_turn[match] = turn
                self._signal("original_reread")
                self._host.note_reach(1.0)
        except Exception:
            return                                   # a bookkeeping hint must never fail the tool call

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
            "policy": self.get_policy_status(),
            "warnings": list(self._warnings),
            "activity": self.activity_snapshot(),
        }
        return status
