"""``hermes_compat``: every guarded name agrees with Hermes, and so does its local fallback.

* The module is loaded by path under a throwaway name, the way Hermes' directory loader imports a
  sibling (never through the package ``__init__``).
* ``cc`` / ``cm`` are ``agent.context_compressor`` / ``agent.compression_marker`` of the active mode:
  real Hermes with ``HERMES_REPO_ROOT``, else ``tests/hermes_stub``. Fallback-vs-Hermes comparisons
  run in both modes where the stub carries the name, and in real mode (``real_hermes``) for the
  names the stub omits; the literal expectations (written from Hermes' source) run everywhere, so a
  fallback is pinned even where no Hermes counterpart is importable.
* ``probe_seams`` is checked against real Hermes, the stub and fake classes.
"""
from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import subprocess
import sys
import textwrap
import types
from types import SimpleNamespace

import pytest
from conftest import REPO_ROOT, STUB_ROOT

COMPAT_FILE = REPO_ROOT / "hermes_tameru_plugin" / "hermes_compat.py"


# ---- fixtures --------------------------------------------------------------------------------
def _load_by_path(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve string annotations through sys.modules
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


@pytest.fixture(scope="module")
def compat():
    module = _load_by_path("_tameru_compat_under_test", COMPAT_FILE)
    yield module
    sys.modules.pop(module.__name__, None)


@pytest.fixture(scope="module")
def cc():
    return importlib.import_module("agent.context_compressor")


@pytest.fixture(scope="module")
def cm():
    return importlib.import_module("agent.compression_marker")


@pytest.fixture(scope="module")
def stub_cc():
    module = _load_by_path("_compat_test_stub_cc", STUB_ROOT / "agent" / "context_compressor.py")
    yield module
    sys.modules.pop(module.__name__, None)


# ---- constants -------------------------------------------------------------------------------
def test_constants_match_hermes(compat, cc, cm):
    assert compat.MARKER_PREFIX == cm._COMPRESSION_MARKER_PREFIX
    assert compat.SKILL_PRUNED_MARKER_PREFIX == cc.SKILL_PRUNED_MARKER_PREFIX
    assert compat.PRUNED_PLACEHOLDER == cc._PRUNED_TOOL_PLACEHOLDER
    assert compat._PRUNE_MIN_CHARS == cc._PRUNE_MIN_CHARS


def test_duplicate_prefix_is_what_hermes_dedupe_emits(compat, cc):
    body = "same output " * 40
    rows = [{"role": "tool", "tool_call_id": f"c{i}", "content": body} for i in range(2)]
    cc.ContextCompressor._dedupe_tool_results(rows)
    assert rows[0]["content"].startswith(compat.DUPLICATE_PREFIX)
    assert rows[1]["content"] == body, "the newest copy stays"


@pytest.mark.real_hermes
def test_identity_keys_and_persisted_tag_match_hermes(compat):
    from agent import message_metadata
    from tools import tool_result_storage

    assert compat.TOOL_CALL_UID_KEY == message_metadata.TOOL_CALL_UID == "_tool_call_uid"
    assert compat.MESSAGE_UID_KEY == message_metadata.MESSAGE_UID == "message_uid"
    assert compat.PERSISTED_OUTPUT_TAG == tool_result_storage.PERSISTED_OUTPUT_TAG
    block = tool_result_storage._build_persisted_message("preview", False, 5_000, "/tmp/spill.txt")
    assert block.startswith(compat.PERSISTED_OUTPUT_TAG)


# ---- marker ----------------------------------------------------------------------------------
_MARKER_COUNTS = [(0, 0), (1, 2), (5, 10), (1234, 56789), (10**12 - 1, 10**12 - 1)]


@pytest.mark.parametrize(("omitted", "total"), _MARKER_COUNTS)
def test_elision_marker_equals_hermes(compat, cm, omitted, total):
    expected = cm._elision_marker(omitted, total)
    assert compat.elision_marker(omitted, total) == expected
    assert compat._local_elision_marker(omitted, total) == expected


def test_elision_marker_literal(compat):
    assert compat._local_elision_marker(1234, 56789) == (
        "⟪HERMES-CONTEXT-COMPRESSION: 1,234 of 56,789 chars omitted here by Hermes's "
        "context compressor.⟫"
    )


def test_marker_len_reserve_is_the_widest_marker(compat, cm):
    assert compat.marker_len_reserve() == cm.ELISION_MARKER_MAX_LEN
    assert compat._LOCAL_MARKER_MAX_LEN == cm.ELISION_MARKER_MAX_LEN
    assert len(compat.elision_marker(10**12 - 1, 10**12 - 1)) == compat.marker_len_reserve()


def test_marker_artifact_re_equals_hermes(compat, cm):
    assert compat.marker_artifact_re().pattern == cm._COMPRESSION_MARKER_ARTIFACT_RE.pattern
    assert compat._ARTIFACT_RE.pattern == cm._COMPRESSION_MARKER_ARTIFACT_RE.pattern
    for regex in (compat.marker_artifact_re(), compat._ARTIFACT_RE):
        assert regex.search("kept " + compat.elision_marker(5, 10) + " kept")
        assert regex.search(compat.MARKER_PREFIX + " 12"), "a copy cut after its first count"
        assert not regex.search(compat.MARKER_PREFIX), "the bare prefix may appear in documentation"
        assert not regex.search(compat.MARKER_PREFIX + " abc")


# ---- summary stub / api_content / summarizers ------------------------------------------------
_STUB_SAMPLES = [
    ("", False),
    ("plain output", False),
    ("[terminal] ran `ls` -> exit 0, 3 lines output (5,000 chars)", True),
    ("[read_file] read /a/b.py from line 1 (12,345 chars)", True),
    ("[no counter here]", False),
    ("not bracketed (12 chars)", False),
    ("[x] (3 chars)", True),
    ("[x] (3 chars)" + "y" * 386, True),  # 399 chars: still a stub
    ("[x] (3 chars)" + "y" * 387, False),  # 400 chars: too long to be one
    ("[Duplicate tool output — same content as a more recent call]", False),
]


@pytest.mark.parametrize(("content", "expected"), _STUB_SAMPLES)
def test_is_summary_stub_equals_hermes(compat, cc, content, expected):
    assert cc._is_summary_stub(content) is expected
    assert compat.is_summary_stub(content) is expected
    assert compat._local_is_summary_stub(content) is expected


def test_is_summary_stub_rejects_non_strings(compat):
    assert compat.is_summary_stub(None) is False
    assert compat.is_summary_stub([{"type": "text", "text": "[x] (3 chars)"}]) is False


def test_drop_stale_api_content_pops_the_sidecar_in_place(compat):
    for drop in (compat.drop_stale_api_content, compat._local_drop_stale_api_content):
        row = {"role": "tool", "content": "new", "api_content": "wire copy", "tool_call_id": "c1"}
        assert drop(row) is None
        assert row == {"role": "tool", "content": "new", "tool_call_id": "c1"}
        drop(row)  # absent sidecar: nothing to do
        assert row == {"role": "tool", "content": "new", "tool_call_id": "c1"}


def test_drop_stale_api_content_equals_hermes(compat):
    turn_context = importlib.import_module("agent.turn_context")
    rows = [{"content": "x", "api_content": "y"} for _ in range(3)]
    turn_context.drop_stale_api_content(rows[0])
    compat.drop_stale_api_content(rows[1])
    compat._local_drop_stale_api_content(rows[2])
    assert rows[0] == rows[1] == rows[2] == {"content": "x"}


def test_summarize_tool_result_is_hermes(compat, cc, payloads):
    content = payloads.terminal("a\nb\nc\n" * 100, exit_code=3)
    assert compat.summarize_tool_result("terminal", '{"command": "ls"}', content) == (
        cc._summarize_tool_result("terminal", '{"command": "ls"}', content)
    )


@pytest.mark.real_hermes
def test_summarize_fallback_is_hermes_error_line(compat, cc, monkeypatch):
    """The bare size line is what Hermes itself emits when a summarizer fails."""
    def explode(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(cc, "_summarize_tool_result_unguarded", explode)
    for content in ("x" * 1234, "", None):
        assert compat._local_summarize_tool_result("t", "{}", content) == (
            cc._summarize_tool_result("t", "{}", content)
        )
    assert compat._local_summarize_tool_result("t", "{}", "x" * 1234) == "[t] (1,234 chars result)"
    assert compat._local_summarize_tool_result("t", "{}", None) == "[t] (0 chars result)"


@pytest.mark.real_hermes
def test_summarize_fallback_is_hermes_generic_line_for_unknown_tools(compat, cc):
    for content in ("x" * 500, "line\n" * 90):
        assert compat._local_summarize_tool_result("mystery_tool", "", content) == (
            cc._summarize_tool_result("mystery_tool", "", content)
        )


# ---- refused calls and failure suffix --------------------------------------------------------
_REFUSED = [
    ("terminal", {"command": "rm -rf /"}, json.dumps({"status": "pending_approval"}),
     "[terminal] `rm -rf /` awaiting the user's approval, not run"),
    ("terminal", {"command": "rm -rf /"}, "BLOCKED: dangerous command",
     "[terminal] `rm -rf /` BLOCKED, not run"),
    ("write_file", {"path": "/etc/passwd"}, json.dumps({"status": "blocked", "error": "denied"}),
     "[write_file] `/etc/passwd` BLOCKED, not run"),
    ("terminal", {"command": "x"}, json.dumps({"error": "BLOCKED: the user NOT consented"}),
     "[terminal] `x` BLOCKED, not run; the user did NOT consent, do not retry or reach the same "
     "outcome another way"),
    ("terminal", {"command": "c" * 100}, "BLOCKED: nope",
     "[terminal] `" + "c" * 57 + "...` BLOCKED, not run"),
    ("browser_click", {}, "BLOCKED: nope", "[browser_click] BLOCKED, not run"),
    ("terminal", {"command": 42}, "BLOCKED: nope", "[terminal] `42` BLOCKED, not run"),
    ("t" * 300, {}, json.dumps({"status": "pending_approval"}), ("[" + "t" * 300)[:199]),
    ("terminal", {"command": "ls"}, json.dumps({"output": "ok", "exit_code": 0}), None),
    ("terminal", {"command": "ls"}, json.dumps({"error": "boom"}), None),
    ("terminal", {"command": "ls"}, json.dumps({"status": "blocked"}), None),
    ("terminal", {"command": "ls"}, "output that merely mentions BLOCKED", None),
    ("terminal", {"command": "ls"}, "", None),
]


@pytest.mark.parametrize(("tool", "args", "content", "expected"), _REFUSED)
def test_refused_summary_fallback_is_pinned(compat, tool, args, content, expected):
    assert compat._local_refused_summary(tool, args, content) == expected
    assert compat.refused_summary(tool, args, content) == expected


@pytest.mark.real_hermes
@pytest.mark.parametrize(("tool", "args", "content", "expected"), _REFUSED)
def test_refused_summary_fallback_equals_hermes(compat, cc, tool, args, content, expected):
    assert cc._summarize_refused_tool_result(tool, args, content) == expected
    assert compat._local_refused_summary(tool, args, content) == expected


def test_refused_summary_tolerates_odd_arguments(compat):
    assert compat.refused_summary("terminal", None, "BLOCKED: x") == "[terminal] BLOCKED, not run"
    assert compat.refused_summary("terminal", {}, None) is None
    assert compat.refused_summary("terminal", {}, [{"type": "text", "text": "BLOCKED"}]) is None


# Hermes' own JSON probe raises on these; the helpers promise not to.
_PATHOLOGICAL_JSON = ["1" * 5000, "[" * 100_000, '{"a": ' * 100_000]


@pytest.mark.parametrize("content", _PATHOLOGICAL_JSON, ids=["digits", "deep-list", "deep-object"])
def test_json_probing_helpers_survive_pathological_content(compat, content):
    assert compat.refused_summary("terminal", {"command": "ls"}, content) is None
    assert compat.result_failure_suffix(content) == ""
    assert compat._local_refused_summary("terminal", {}, content) is None
    assert compat._local_result_failure_suffix(content) == ""


_FAILURES = [
    (json.dumps({"error": "boom"}), " FAILED: boom"),
    (json.dumps({"success": False}), " FAILED"),
    (json.dumps({"success": False, "error": "disk full\nsecond   line"}),
     " FAILED: disk full second line"),
    (json.dumps({"error": "x" * 200}), " FAILED: " + "x" * 80),
    (json.dumps({"error": {"code": 5}}), " FAILED: {'code': 5}"),
    (json.dumps({"error": "", "success": True}), ""),
    (json.dumps({"error": None, "success": True}), ""),
    (json.dumps({"exit_code": 1, "output": "x"}), ""),
    ("plain text", ""),
    ("", ""),
    ("null", ""),
    ("[1, 2]", ""),
]


@pytest.mark.parametrize(("content", "expected"), _FAILURES)
def test_result_failure_suffix_fallback_is_pinned(compat, content, expected):
    assert compat._local_result_failure_suffix(content) == expected
    assert compat.result_failure_suffix(content) == expected


@pytest.mark.real_hermes
@pytest.mark.parametrize(("content", "expected"), _FAILURES)
def test_result_failure_suffix_fallback_equals_hermes(compat, cc, content, expected):
    assert cc._result_failure_suffix(content) == expected
    assert compat._local_result_failure_suffix(content) == expected


# ---- tool calls, synthetic rows, summaries ---------------------------------------------------
def _tool_call_messages():
    return [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "terminal", "arguments": '{"c": 1}'}},
            SimpleNamespace(id="b", function=SimpleNamespace(name="read_file", arguments='{"p": 2}')),
            {"id": "c", "function": {}},
            {"function": {"name": "noid", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "out"},
        {"role": "assistant", "content": "thinking", "tool_calls": None},
        {"role": "assistant", "tool_calls": [
            {"id": "a", "function": {"name": "terminal", "arguments": "{}"}},
        ]},
    ]


def test_tool_calls_by_id_equals_hermes(compat, cc):
    expected = {
        "a": ("terminal", "{}"),  # the later call with the same id wins
        "b": ("read_file", '{"p": 2}'),
        "c": ("unknown", ""),
        "": ("noid", "{}"),
    }
    messages = _tool_call_messages()
    assert cc._tool_calls_by_id(messages) == expected
    assert compat.tool_calls_by_id(messages) == expected
    assert compat._local_tool_calls_by_id(messages) == expected
    assert compat.tool_calls_by_id([]) == {}


_SYNTHETIC = [
    ("", True), ("   \n", True), (None, True), (["part"], True),
    ("hello there", False), ("please run [System: x]", False), ("[IMPORTANT: something else", False),
    ("[System: gateway restarted]", True), ("  [CONTEXT COMPACTION — REFERENCE ONLY] x", True),
    ("[PRIOR CONTEXT]", True), ("[IMPORTANT: Background process finished", True),
    ("[Your active task list was preserved", True), ("[Planning state preserved", True),
    ("[ASYNC DELEGATION] done", True), ("[OUT-OF-BAND] note", True), ("Cronjob Response: ok", True),
]


@pytest.mark.parametrize(("content", "expected"), _SYNTHETIC)
def test_is_synthetic_user_row_fallback_is_pinned(compat, content, expected):
    assert compat._local_is_synthetic_user_row(content) is expected
    assert compat.is_synthetic_user_row(content) is expected


@pytest.mark.real_hermes
@pytest.mark.parametrize(("content", "expected"), _SYNTHETIC)
def test_is_synthetic_user_row_fallback_equals_hermes(compat, cc, content, expected):
    assert cc._synthetic_user_row(content) is expected
    assert compat._local_is_synthetic_user_row(content) is expected


def _summary_samples(cc):
    prefix = cc.SUMMARY_PREFIX
    merged = "preserved user words\n[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]\n"
    return [
        ("", False), ("hello", False), ("a [CONTEXT COMPACTION — REFERENCE ONLY] mid-text", False),
        (prefix + "\n## Goal\nship it", True), ("  \n" + prefix, True),
        ("[CONTEXT SUMMARY]: legacy body", True), ("[CONTEXT SUMMARY] no colon", False),
        (merged + prefix + "\nbody", True), (merged + "no handoff here", False),
        ("[CONTEXT COMPACTION] a different banner", False),
    ]


def test_is_compaction_summary(compat, cc):
    for content, expected in _summary_samples(cc):
        assert compat.is_compaction_summary(content) is expected, content
        assert compat._local_is_compaction_summary(content) is expected, content
    assert compat.is_compaction_summary(None) is False
    assert compat.is_compaction_summary([{"type": "text", "text": cc.SUMMARY_PREFIX}]) is False


@pytest.mark.real_hermes
def test_is_compaction_summary_covers_every_shipped_prefix(compat, cc):
    prefixes = (cc.SUMMARY_PREFIX, cc.LEGACY_SUMMARY_PREFIX, *cc._HISTORICAL_SUMMARY_PREFIXES)
    assert len(prefixes) >= 3
    for prefix in prefixes:
        for content in (prefix, prefix + "\nbody", "x\n" + cc._MERGED_SUMMARY_DELIMITER + "\n" + prefix):
            assert cc.ContextCompressor.classify_summary_content(content) is not None
            assert compat.is_compaction_summary(content) is True
            assert compat._local_is_compaction_summary(content) is True
    assert compat._MERGED_SUMMARY_DELIMITER == cc._MERGED_SUMMARY_DELIMITER
    assert all(prefix.startswith(compat._SUMMARY_HEADS) for prefix in prefixes)


@pytest.mark.real_hermes
def test_is_compaction_summary_fallback_is_a_superset_of_hermes(compat, cc):
    """The fallback keys on the shared head, so it also accepts a bare head Hermes would not."""
    bare = compat._SUMMARY_HEADS[0] + " short banner"
    assert cc.ContextCompressor.classify_summary_content(bare) is None
    assert compat._local_is_compaction_summary(bare) is True


# ---- lean stub regex -------------------------------------------------------------------------
@pytest.mark.parametrize(("tool", "size", "session"), [
    ("terminal", 12_345, "20260101_120000_abcd"), ("", 5, ""), ("mcp_gh_search", 1_234_567, "s"),
    ("read_file", 1_500, ""),
])
def test_lean_stub_re_matches_hermes_stubs(compat, cc, tool, size, session):
    stub = cc._lean_recovery_stub(tool, size, session)
    assert compat.LEAN_STUB_RE.match(stub), stub
    assert compat.LEAN_STUB_RE.fullmatch(stub)


@pytest.mark.parametrize("content", [
    "plain output",
    "[terminal] ran `ls` -> exit 0, 3 lines output (5,000 chars)",
    "[terminal] ran `ls` (5,000 chars) [tameru:header kept 1/9 lines (60 of 5,000 chars) ref=0a1b2c3d]",
    "[terminal output demoted at compaction — 5 chars preserved in session history.] and more",
    "[terminal output demoted at compaction — 5 chars preserved in session history.]\nsecond line",
    "see [terminal output demoted at compaction — 5 chars preserved in session history.]",
    "[terminal output demoted at compaction — many chars preserved in session history.]",
])
def test_lean_stub_re_rejects_everything_else(compat, content):
    assert not compat.LEAN_STUB_RE.match(content)


# ---- compression threshold and session db ----------------------------------------------------
@pytest.mark.real_hermes
def test_compression_threshold_is_hermes_with_codex_autoraise(compat):
    from agent import agent_init

    cfg = {"threshold": 0.5}
    raised = compat.compression_threshold("gpt-5.5", "openai-codex", cfg)
    direct = agent_init._compression_threshold(SimpleNamespace(model="gpt-5.5", provider="openai-codex"), cfg)
    assert raised == direct[0] and raised > 0.5, "the Codex autoraise reaches the engine"
    assert compat.compression_threshold("claude-sonnet-4", "anthropic", {"threshold": 0.6}) == 0.6
    opted_out = {"threshold": 0.5, "codex_gpt55_autoraise": False}
    assert compat.compression_threshold("gpt-5.5", "openai-codex", opted_out) == 0.5
    assert cfg == {"threshold": 0.5}, "the caller's config is not mutated"


@pytest.mark.real_hermes
@pytest.mark.parametrize("cfg", [{"threshold": "not a number"}, None, "threshold", 7])
def test_compression_threshold_is_none_on_bad_config(compat, cfg):
    assert compat.compression_threshold("m", "p", cfg) is None


@pytest.mark.stub_only
def test_compression_threshold_is_none_without_agent_init(compat):
    assert compat.compression_threshold("gpt-5.5", "openai-codex", {"threshold": 0.5}) is None


class _FullDb:
    def get_messages(self, session_id, **kwargs):
        return []

    def archive_and_compact(self, session_id, rows, **kwargs):
        return None


class _ReadOnlyDb:
    def get_messages(self, session_id, **kwargs):
        return []


class _OpaqueLookalike(_FullDb):
    """Answers the capability check but is named like Hermes' isolation placeholder."""


class _ExplodingDb:
    def __getattr__(self, name):
        raise RuntimeError("proxy is gone")


def test_session_db_usable_with_fakes(compat):
    assert compat.session_db_usable(_FullDb()) is True
    assert compat.session_db_usable(_ReadOnlyDb()) is False
    assert compat.session_db_usable(SimpleNamespace(get_messages=None, archive_and_compact="x")) is False
    assert compat.session_db_usable(SimpleNamespace(get_messages=print)) is False
    assert compat.session_db_usable(_OpaqueLookalike()) is False
    assert compat.session_db_usable(_ExplodingDb()) is False
    assert compat.session_db_usable(None) is False
    assert compat.session_db_usable(object()) is False


@pytest.mark.real_hermes
def test_session_db_usable_with_hermes_objects(compat, tmp_path):
    from hermes_cli.plugin_host_wire import Opaque
    from hermes_state import SessionDB

    placeholder = Opaque("SessionDB")
    assert bool(placeholder) is True, "Hermes' placeholder is truthy, so truthiness proves nothing"
    assert compat.session_db_usable(placeholder) is False
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        assert compat.session_db_usable(db) is True
    finally:
        db.close()


# ---- seam probe ------------------------------------------------------------------------------
def _good_namespace() -> dict:
    """A class body with every seam in the shape Hermes has it (see ``probe_seams``)."""
    def prune_old_tool_results(self, messages):
        return [self._demote_tool_result_at(messages, 0, {}, 200, set())]

    def pressure_demote_tail(self, result):
        def shrink_at(i):  # nested, like Hermes' ``_shrink_at``
            return self._demote_tool_result_at(result, i, {}, 200)

        return shrink_at(0)

    def demote_stale_tail_tools(self, messages, tail_start):
        return messages

    def augment_summary_lean(self, summary, turns_to_summarize):
        return summary

    def compress(self, messages, current_tokens=None, focus_topic=None, force=False,
                 memory_context="", bypass_cooldown=False):
        return messages

    def demote_tool_result_at(result, idx, call_id_to_tool, min_prune_chars, protected_skills=None):
        return False

    return {
        "_demote_tool_result_at": staticmethod(demote_tool_result_at),
        "_prune_old_tool_results": prune_old_tool_results,
        "_pressure_demote_tail": pressure_demote_tail,
        "_demote_stale_tail_tools": demote_stale_tail_tools,
        "_augment_summary_lean": augment_summary_lean,
        "compress": compress,
    }


def _fake(drop=(), **replace) -> type:
    namespace = {k: v for k, v in _good_namespace().items() if k not in drop}
    namespace.update(replace)
    return type("FakeCompressor", (), namespace)


def _flags(report) -> dict:
    return {k: v for k, v in dataclasses.asdict(report).items() if k != "reasons"}


ALL_SEAMS = {"demote": True, "pressure": True, "lean_tail": True, "augment": True, "compress_kw": True}


def test_probe_is_all_true_on_hermes(compat, cc):
    report = compat.probe_seams(cc.ContextCompressor)
    assert _flags(report) == ALL_SEAMS and report.ok is True and report.reasons == ()


def test_probe_is_all_true_on_the_stub(compat, stub_cc):
    report = compat.probe_seams(stub_cc.ContextCompressor)
    assert _flags(report) == ALL_SEAMS and report.ok is True and report.reasons == ()


def test_probe_is_all_true_on_a_well_formed_fake(compat):
    assert _flags(compat.probe_seams(_fake())) == ALL_SEAMS


def test_probe_flags_a_missing_seam(compat):
    report = compat.probe_seams(_fake(drop=("_demote_tool_result_at",)))
    assert report.demote is False and report.ok is False
    assert any("_demote_tool_result_at is missing" in reason for reason in report.reasons)
    assert report.pressure and report.lean_tail and report.augment and report.compress_kw


def test_probe_flags_a_seam_that_is_no_longer_static(compat):
    def demote(self, result, idx, call_id_to_tool, min_prune_chars, protected_skills=None):
        return False

    report = compat.probe_seams(_fake(_demote_tool_result_at=demote))
    assert report.demote is False
    assert any("no longer a staticmethod" in reason for reason in report.reasons)


def test_probe_flags_changed_seam_parameters(compat):
    def demote(messages, index, call_id_to_tool, min_prune_chars):
        return False

    report = compat.probe_seams(_fake(_demote_tool_result_at=staticmethod(demote)))
    assert report.demote is False
    assert any("parameters changed" in reason for reason in report.reasons)


def test_probe_flags_a_prune_pass_that_no_longer_reaches_the_seam(compat):
    def prune_old_tool_results(self, messages):
        return messages

    report = compat.probe_seams(_fake(_prune_old_tool_results=prune_old_tool_results))
    assert report.demote is False and report.ok is False
    assert any("_prune_old_tool_results" in reason for reason in report.reasons)


def test_probe_pressure_pass_is_independent_of_the_seam_flag(compat):
    def pressure_demote_tail(self, result):
        return 0

    report = compat.probe_seams(_fake(_pressure_demote_tail=pressure_demote_tail))
    assert report.pressure is False and report.demote is True and report.ok is True
    assert any("_pressure_demote_tail" in reason for reason in report.reasons)


def test_probe_flags_missing_lean_hooks_and_compress_keyword(compat):
    def compress(self, messages, current_tokens=None, focus_topic=None, force=False,
                 memory_context=""):
        return messages

    report = compat.probe_seams(_fake(drop=("_demote_stale_tail_tools", "_augment_summary_lean"),
                                      compress=compress))
    assert (report.lean_tail, report.augment, report.compress_kw) == (False, False, False)
    assert report.demote and report.pressure
    assert len(report.reasons) == 3


def test_probe_flags_renamed_lean_hook_parameters(compat):
    def augment_summary_lean(self, text, turns):
        return text

    report = compat.probe_seams(_fake(_augment_summary_lean=augment_summary_lean))
    assert report.augment is False
    assert any("_augment_summary_lean parameters changed" in reason for reason in report.reasons)


class _HostileMeta(type):
    def __getattr__(cls, name):
        raise RuntimeError(f"no {name}")


@pytest.mark.parametrize("target", [object, None, 42, "not a class", _HostileMeta("Hostile", (), {})])
def test_probe_never_raises(compat, target):
    report = compat.probe_seams(target)
    assert report.ok is False and not any(
        (report.pressure, report.lean_tail, report.augment, report.compress_kw)
    )
    assert report.reasons and all(isinstance(reason, str) for reason in report.reasons)


def test_seam_report_is_a_frozen_all_false_default(compat):
    report = compat.SeamReport()
    assert _flags(report) == dict.fromkeys(ALL_SEAMS, False) and report.reasons == () and not report.ok
    assert compat.SeamReport(demote=True).ok is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.demote = True


# ---- import safety and the Hermes-absent path ------------------------------------------------
def test_without_hermes_every_helper_uses_its_fallback(compat, monkeypatch):
    monkeypatch.setattr(compat, "_module", lambda name: None)
    marker = "⟪HERMES-CONTEXT-COMPRESSION: 5 of 10 chars omitted here by Hermes's context compressor.⟫"
    assert compat.elision_marker(5, 10) == marker
    assert compat.marker_len_reserve() == 115
    assert compat.marker_artifact_re().search(marker)
    assert compat.is_summary_stub("[x] (3 chars)") and not compat.is_summary_stub("[x]")
    row = {"content": "c", "api_content": "a"}
    compat.drop_stale_api_content(row)
    assert row == {"content": "c"}
    assert compat.summarize_tool_result("terminal", "{}", "x" * 2000) == "[terminal] (2,000 chars result)"
    assert compat.refused_summary("terminal", {"command": "ls"}, "BLOCKED: no") == (
        "[terminal] `ls` BLOCKED, not run"
    )
    assert compat.result_failure_suffix('{"error": "boom"}') == " FAILED: boom"
    assert compat.tool_calls_by_id(_tool_call_messages())["b"] == ("read_file", '{"p": 2}')
    assert compat.is_synthetic_user_row("[System: x]") and not compat.is_synthetic_user_row("hi")
    assert compat.is_compaction_summary("[CONTEXT SUMMARY]: x")
    assert compat.compression_threshold("gpt-5.5", "openai-codex", {"threshold": 0.5}) is None
    assert compat.session_db_usable(_FullDb()) is True


_ABSENT_SCRIPT = textwrap.dedent("""
    import importlib.util, json, sys

    sys.modules["agent"] = None  # "import agent..." raises ImportError whatever is on sys.path
    spec = importlib.util.spec_from_file_location("throwaway_pkg.hermes_compat", sys.argv[1])
    compat = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = compat
    spec.loader.exec_module(compat)
    report = compat.probe_seams(object)
    print(json.dumps({
        "imported_agent": sorted(m for m in sys.modules if m.startswith("agent") and sys.modules[m]),
        "marker": compat.elision_marker(1, 2),
        "stub": compat.is_summary_stub("[x] (3 chars)"),
        "threshold": compat.compression_threshold("m", "p", {}),
        "probe_ok": report.ok,
    }))
""")


def test_module_imports_cleanly_as_a_sibling_when_hermes_is_absent(tmp_path):
    done = subprocess.run(
        [sys.executable, "-c", _ABSENT_SCRIPT, str(COMPAT_FILE)],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen == {
        "imported_agent": [],
        "marker": "⟪HERMES-CONTEXT-COMPRESSION: 1 of 2 chars omitted here by Hermes's context compressor.⟫",
        "stub": True,
        "threshold": None,
        "probe_ok": False,
    }
    assert done.stdout.strip().count("\n") == 0 and not done.stderr, "no import-time output"


def test_module_lookup_does_not_remember_a_failed_import(compat, monkeypatch):
    name = "_tameru_late_hermes_module"
    assert compat._module(name) is None
    late = types.ModuleType(name)
    monkeypatch.setitem(sys.modules, name, late)
    assert compat._module(name) is late  # a module that appears (or is swapped) later is seen
    monkeypatch.setitem(sys.modules, name, None)
    assert compat._module(name) is None  # a blocked import stays "unavailable", never raises
