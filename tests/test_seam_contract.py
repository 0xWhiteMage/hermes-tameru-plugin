"""Contract between the plugin and the Hermes internals it hooks.

The plugin's prune ladder lives inside ``ContextCompressor._demote_tool_result_at`` (a
``@staticmethod`` that Hermes calls through ``self.`` — pass 2 of ``_prune_old_tool_results``
with 5 positional args, pass 4 ``_pressure_demote_tail`` with 4) and post-processes
``_demote_stale_tail_tools``. None of that is public API, so this file pins every assumption:

* each ``CHECKS`` function asserts one property on an ``agent.context_compressor`` /
  ``agent.compression_marker`` module pair; ``test_real_hermes_contract`` runs them against the
  real checkout (``real_hermes``) and ``test_stub_reproduces_contract`` against
  ``tests/hermes_stub`` in every mode, so the stub cannot drift from what the plugin relies on;
* ``test_parity_*`` compares stub and real behaviour (summary-stub detection, skip rules, marker
  regex, payload shapes) so the stub stays an honest stand-in.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
import sys
import textwrap
import types

import pytest
from conftest import REAL_HERMES, STUB_ROOT


# ---- module access -------------------------------------------------------------------------
def _load_stub(filename: str, modname: str):
    """Load a ``tests/hermes_stub/agent`` file under a private name (works next to real Hermes)."""
    spec = importlib.util.spec_from_file_location(modname, STUB_ROOT / "agent" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stub_mods():
    return types.SimpleNamespace(
        cc=_load_stub("context_compressor.py", "_stub_agent_context_compressor"),
        cm=_load_stub("compression_marker.py", "_stub_agent_compression_marker"),
    )


@pytest.fixture(scope="module")
def real_mods():
    if not REAL_HERMES:
        pytest.skip("needs real Hermes (set HERMES_REPO_ROOT)")
    return types.SimpleNamespace(
        cc=importlib.import_module("agent.context_compressor"),
        cm=importlib.import_module("agent.compression_marker"),
    )


def _reaches(func, target: str) -> bool:
    """True when ``target`` is a global/attribute name used by ``func`` or any nested code object."""
    stack, seen = [func.__code__], set()
    while stack:
        code = stack.pop()
        if id(code) in seen:
            continue
        seen.add(id(code))
        if target in code.co_names:
            return True
        stack.extend(c for c in code.co_consts if isinstance(c, types.CodeType))
    return False


def _engine(cc, **kw):
    kw.setdefault("quiet_mode", True)
    kw.setdefault("config_context_length", 64_000)  # no model-metadata probing
    return cc.ContextCompressor(model="test-model", **kw)


def _big_tool_session(rounds: int = 6, size: int = 30_000):
    """system, user, <rounds> x (assistant tool_call, big tool result), user — no pending round."""
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "start"}]
    for i in range(rounds):
        msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "terminal", "arguments": json.dumps({"command": f"cmd {i}"})}}],
        })
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"line {i} " * (size // 7)})
    msgs.append({"role": "user", "content": "next question"})
    return msgs


# ---- the contract (one function per property) ----------------------------------------------
def check_demote_seam_is_static_with_expected_params(cc, cm):
    attr = inspect.getattr_static(cc.ContextCompressor, "_demote_tool_result_at")
    assert isinstance(attr, staticmethod), "seam must stay a @staticmethod"
    params = list(inspect.signature(cc.ContextCompressor._demote_tool_result_at).parameters)
    assert params[:4] == ["result", "idx", "call_id_to_tool", "min_prune_chars"], params
    assert "protected_skills" in params[4:]


def check_seam_reachable_from_both_prune_passes(cc, cm):
    for name in ("_prune_old_tool_results", "_pressure_demote_tail"):
        func = getattr(cc.ContextCompressor, name)
        assert _reaches(func, "_demote_tool_result_at"), f"{name} no longer reaches the seam"


def check_lean_hooks_exist(cc, cm):
    klass = cc.ContextCompressor
    stale = inspect.signature(klass._demote_stale_tail_tools).parameters
    assert list(stale)[1:3] == ["messages", "tail_start"], list(stale)
    augment = inspect.signature(klass._augment_summary_lean).parameters
    assert list(augment)[1:3] == ["summary", "turns_to_summarize"], list(augment)


def check_compress_signature_has_bypass_cooldown(cc, cm):
    assert "bypass_cooldown" in inspect.signature(cc.ContextCompressor.compress).parameters


def check_prune_returns_input_object_on_noop(cc, cm):
    engine = _engine(cc, proactive_prune_tokens=0)
    msgs = _big_tool_session()
    out, n = engine.prune_tool_results_only(msgs, current_tokens=10**6)
    assert out is msgs and n == 0, "proactive_prune_tokens == 0 must return the INPUT object"


def check_prune_commit_gates(cc, cm):
    """INPUT object <=> nothing committed: the plugin tells a Hermes no-op from a commit by identity."""
    msgs = _big_tool_session(rounds=14)
    kw = dict(proactive_prune_tokens=1_000)
    below_trigger, n = _engine(cc, **{**kw, "proactive_prune_tokens": 10**7}).prune_tool_results_only(
        msgs, current_tokens=5_000)
    assert below_trigger is msgs and n == 0, "current_tokens below proactive_prune_tokens"
    below_reclaim, n = _engine(cc, **kw, proactive_prune_min_reclaim_tokens=10**7).prune_tool_results_only(
        msgs, current_tokens=10**6)
    assert below_reclaim is msgs and n == 0, "reclaim below proactive_prune_min_reclaim_tokens"
    committed, n = _engine(cc, **kw, proactive_prune_min_reclaim_tokens=1).prune_tool_results_only(
        msgs, current_tokens=10**6)
    assert committed is not msgs and n > 0 and len(committed) == len(msgs), "a real prune commits a new list"
    assert all(m["content"] for m in msgs if m["role"] == "tool"), "the input rows are never mutated"


def check_stale_tail_demotion(cc, cm):
    """Seam B: lean mode demotes tail tool results older than the newest 6 rounds, outside seam A."""
    msgs = _big_tool_session(rounds=9, size=3_000)
    tool_idx = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
    out = _engine(cc, tail_mode="lean")._demote_stale_tail_tools(msgs, 2)
    assert out is not msgs and len(out) == len(msgs)
    for i in tool_idx[:-6]:
        assert "output demoted at compaction" in out[i]["content"], i
    for i in tool_idx[-6:]:
        assert out[i] is msgs[i], "the newest 6 tool rounds stay verbatim (same row objects)"
    assert all("demoted" not in m["content"] for m in msgs if m["role"] == "tool"), "input rows not mutated"
    legacy = _engine(cc, tail_mode="legacy")
    assert legacy._augment_summary_lean("summary", msgs) == "summary", "legacy mode adds no lean sections"


def check_seam_call_arities(cc, cm):
    """Pass 2 calls the seam with exactly 5 positional args, pass 4 with exactly 4."""
    calls: list[tuple[int, tuple]] = []

    class Spy(cc.ContextCompressor):
        @staticmethod
        def _demote_tool_result_at(*args, **kwargs):
            calls.append((len(args), tuple(sorted(kwargs))))
            return False

    engine = Spy(model="test-model", quiet_mode=True, config_context_length=64_000)
    engine._prune_old_tool_results(_big_tool_session(), protect_tail_count=4, protect_tail_tokens=2_000)
    shapes = set(calls)
    assert (5, ()) in shapes, f"pass 2 did not call the seam with 5 positional args: {shapes}"
    assert (4, ()) in shapes, f"pass 4 did not call the seam with 4 positional args: {shapes}"
    assert shapes <= {(5, ()), (4, ())}, shapes


def check_prune_pass_shallow_copies_rows(cc, cm):
    msgs = _big_tool_session(rounds=3, size=2_000)
    out, _n = _engine(cc)._prune_old_tool_results(msgs, protect_tail_count=2)
    assert out is not msgs and all(a is not b for a, b in zip(out, msgs))
    assert all(m["content"] for m in msgs if m["role"] == "tool"), "input rows must not be mutated"


def check_marker_matches_artifact_regex(cc, cm):
    marker = cm._elision_marker(5, 10)
    assert cm._COMPRESSION_MARKER_ARTIFACT_RE.search(marker)
    assert marker.startswith("⟪HERMES-CONTEXT-COMPRESSION:")
    assert len(marker) <= cm.ELISION_MARKER_MAX_LEN


def check_seam_strips_image_parts_only(cc, cm):
    """Multimodal tool results go through Hermes' image-strip policy; text-only parts are left alone."""
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    row = {"role": "tool", "tool_call_id": "c1", "content": [{"type": "text", "text": "page"}, img],
           "api_content": "wire copy"}
    result = [row]
    assert cc.ContextCompressor._demote_tool_result_at(result, 0, {}, 200) is True
    assert result[0]["content"] == [
        {"type": "text", "text": "page"}, {"type": "text", "text": "[screenshot removed to save context]"},
    ]
    assert "api_content" not in result[0], "the stale sidecar must go with the rewritten content"
    assert row["content"][1] is img and "api_content" in row, "the input row must not be mutated"
    text_only = {"role": "tool", "tool_call_id": "c1", "content": [{"type": "text", "text": "x" * 5000}]}
    assert cc.ContextCompressor._demote_tool_result_at([text_only], 0, {}, 200) is False


def check_constructor_rejects_unknown_kwargs(cc, cm):
    """No ``**kwargs``: a plugin forwarding an extra kwarg to ``super().__init__`` must fail loudly."""
    with pytest.raises(TypeError):
        cc.ContextCompressor(model="test-model", quiet_mode=True, not_a_hermes_kwarg=1)


def check_session_lifecycle(cc, cm):
    """``on_session_start`` binds the db and re-arms the prune; session end keeps counters, /reset zeroes them."""
    db = object()
    engine = _engine(cc)
    engine._proactive_prune_rearm_tokens = 123
    engine.on_session_start("s1", session_db=db)
    assert engine._session_db is db and engine._session_id == "s1"
    assert engine._proactive_prune_rearm_tokens == 0, "(re)binding a session re-arms the proactive prune"
    engine.compression_count, engine._previous_summary, engine._proactive_prune_rearm_tokens = 2, "s", 5
    engine.on_session_end("s1", [])
    assert engine._previous_summary is None and engine._proactive_prune_rearm_tokens == 0
    assert engine.compression_count == 2, "session end clears compaction state but not the counters"
    engine.on_session_reset()
    assert engine.compression_count == 0


CHECKS = [
    check_demote_seam_is_static_with_expected_params,
    check_seam_reachable_from_both_prune_passes,
    check_lean_hooks_exist,
    check_compress_signature_has_bypass_cooldown,
    check_prune_returns_input_object_on_noop,
    check_prune_commit_gates,
    check_stale_tail_demotion,
    check_seam_call_arities,
    check_prune_pass_shallow_copies_rows,
    check_marker_matches_artifact_regex,
    check_seam_strips_image_parts_only,
    check_constructor_rejects_unknown_kwargs,
    check_session_lifecycle,
]


@pytest.mark.real_hermes
@pytest.mark.parametrize("check", CHECKS, ids=lambda f: f.__name__.removeprefix("check_"))
def test_real_hermes_contract(check, real_mods):
    check(real_mods.cc, real_mods.cm)


@pytest.mark.parametrize("check", CHECKS, ids=lambda f: f.__name__.removeprefix("check_"))
def test_stub_reproduces_contract(check, stub_mods):
    """Runs in every mode: the stub must satisfy the same contract the plugin relies on."""
    check(stub_mods.cc, stub_mods.cm)


# ---- stub parity (real Hermes vs tests/hermes_stub) ----------------------------------------
@pytest.mark.real_hermes
@pytest.mark.parametrize("content", [
    "",
    "plain output",
    "[terminal] ran `ls` -> exit 0, 3 lines output",
    "[read_file] {\"path\": \"/a/b.py\"} (12,345 chars)",
    "[x] (3 chars) but long " + "y" * 400,
    "[no counter here]",
    "[Duplicate tool output — same content as a more recent call]",
    "[Old tool output cleared to save context space]",
    "[screenshot removed to save context]",
    "[grep output demoted at compaction — 9,000 chars preserved in session history.]",
])
def test_parity_is_summary_stub(content, real_mods, stub_mods):
    assert stub_mods.cc._is_summary_stub(content) == real_mods.cc._is_summary_stub(content)


def _row(content, role="tool", call_id="c1"):
    return {"role": role, "tool_call_id": call_id, "content": content}


_SKIP_CASES = {
    "non_tool_row": ({"role": "assistant", "content": "x" * 5000}, None),
    "non_str_dict": (_row({"a": 1}), None),
    "non_str_text_parts": (_row([{"type": "text", "text": "x" * 5000}]), None),
    "empty": (_row(""), None),
    "placeholder": (_row("[Old tool output cleared to save context space]"), None),
    "duplicate": (_row("[Duplicate tool output — same content as a more recent call]" + "x" * 500), None),
    "screenshot": (_row("[screenshot removed" + "x" * 500), None),
    "summary_stub": (_row("[terminal] ran `ls` -> exit 0, 3 lines output (5,000 chars)"), None),
    "at_threshold": (_row("x" * 200), None),
    "over_threshold": (_row("x" * 201), None),
    "protected_skill": (_row("x" * 5000, call_id="skill1"), {"foo"}),
    "unprotected_skill": (_row("x" * 5000, call_id="skill1"), {"other"}),
    "skill_pressure_pass": (_row("x" * 5000, call_id="skill1"), None),
    "image_parts": (_row([{"type": "text", "text": "page"}, {"type": "image_url", "image_url": {"url": "data:,x"}}])
                    | {"api_content": "wire copy"}, None),
    "multimodal_envelope": (_row({"_multimodal": True, "content": [{"type": "image", "source": {}}],
                                  "text_summary": "a screenshot"}), None),
}


@pytest.mark.real_hermes
@pytest.mark.parametrize("case", sorted(_SKIP_CASES))
def test_parity_demote_skip_rules(case, real_mods, stub_mods):
    """The stub's seam skips (and rewrites) exactly the rows real Hermes does."""
    row, protected = _SKIP_CASES[case]
    call_map = {"c1": ("terminal", "{}"), "skill1": ("skill_view", json.dumps({"name": "foo"}))}
    outcomes, rows = [], []
    for mod in (real_mods.cc, stub_mods.cc):
        result = [dict(row)]
        changed = mod.ContextCompressor._demote_tool_result_at(result, 0, call_map, 200, protected)
        outcomes.append((changed, result[0] != row))
        rows.append(result[0])
        if mod is stub_mods.cc and changed and isinstance(row["content"], str):
            # Idempotency: the stub's own summary must be recognised, or every pass would re-demote it.
            assert mod._is_summary_stub(result[0]["content"])
    assert outcomes[0] == outcomes[1], f"real={outcomes[0]} stub={outcomes[1]}"
    if outcomes[0][0] and not isinstance(row["content"], str):
        assert rows[0] == rows[1], "multimodal rewrites are deterministic and must match Hermes exactly"


@pytest.mark.real_hermes
def test_parity_marker_module(real_mods, stub_mods):
    real, stub = real_mods.cm, stub_mods.cm
    assert stub._COMPRESSION_MARKER_ARTIFACT_RE.pattern == real._COMPRESSION_MARKER_ARTIFACT_RE.pattern
    assert stub.ELISION_MARKER_MAX_LEN == real.ELISION_MARKER_MAX_LEN
    for omitted, total in ((1, 2), (5, 10), (1234, 56789)):
        assert stub._elision_marker(omitted, total) == real._elision_marker(omitted, total)
    text = "abcdefghij" * 100
    assert stub.elide(text, 300) == real.elide(text, 300)
    assert stub.elide_middle(text, 100, 100) == real.elide_middle(text, 100, 100)


@pytest.mark.real_hermes
def test_parity_payload_builders_match_hermes_shapes(payloads):
    """conftest's Hermes-shaped payload builders serialize exactly what Hermes' tools emit."""
    from tools.file_operations_common import ReadResult, SearchMatch, SearchResult

    text = "alpha\nbeta\ngamma\n"
    read = ReadResult(content="1|alpha\n2|beta\n3|gamma", total_lines=3, file_size=len(text))
    assert json.loads(payloads.read_file(text)) == read.to_dict()
    assert list(json.loads(payloads.read_file(text))) == list(read.to_dict())

    for n in (3, 7):
        hits = [("a.py", i, f"hit {i}  ") for i in range(1, n + 1)]
        real = SearchResult(
            matches=[SearchMatch(path=p, line_number=ln, content=c) for p, ln, c in hits], total_count=n,
        ).to_dict(densify=True)
        assert json.loads(payloads.search_files(hits)) == real


_SIGNATURE_PARITY = [
    "__init__", "update_model", "update_from_response", "should_compress", "bind_session_state",
    "on_session_start", "on_session_reset", "on_session_end", "prune_tool_results_only", "compress",
    "_prune_old_tool_results", "_pressure_demote_tail", "_demote_tool_result_at", "_demote_stale_tail_tools",
    "_augment_summary_lean", "_protect_head_size", "_spared_pending_tool_round",
]


def _shape(func):
    return [(p.name, p.kind, p.default if p.default is not p.empty else "<required>")
            for p in inspect.signature(func).parameters.values()]


@pytest.mark.real_hermes
@pytest.mark.parametrize("name", _SIGNATURE_PARITY)
def test_parity_method_signatures(name, real_mods, stub_mods):
    """Names, kinds and default VALUES agree, so a call that works on the stub works on Hermes."""
    real = getattr(real_mods.cc.ContextCompressor, name)
    stub = getattr(stub_mods.cc.ContextCompressor, name)
    assert _shape(stub) == _shape(real)


@pytest.mark.real_hermes
def test_parity_module_constants(real_mods, stub_mods):
    """Every constant the stub defines is Hermes' constant, with Hermes' value."""
    abbreviated = {"SUMMARY_PREFIX"}  # the stub's is a short stand-in
    checked = 0
    for name, value in vars(stub_mods.cc).items():
        if name.startswith("__") or name in abbreviated or not isinstance(value, (str, int, float, tuple, frozenset)):
            continue
        assert hasattr(real_mods.cc, name), f"{name} does not exist in Hermes' context_compressor"
        assert getattr(real_mods.cc, name) == value, name
        checked += 1
    assert checked >= 20, checked
    assert real_mods.cc.SUMMARY_PREFIX.startswith(stub_mods.cc.SUMMARY_PREFIX)
    base_attrs = ("threshold_percent", "protect_first_n", "protect_last_n", "emit_automatic_compaction_status")
    real_base = importlib.import_module("agent.context_engine").ContextEngine
    stub_base = stub_mods.cc.ContextEngine
    for attr in base_attrs:
        assert getattr(stub_base, attr) == getattr(real_base, attr), attr


_VERBATIM_FUNCTIONS = [
    "_is_summary_stub", "_lean_recovery_stub", "_tc_get", "_json_dict", "_tool_calls_by_id",
    "_skill_view_call_sites", "_collect_protected_skill_names", "_pending_tool_round", "_is_image_part",
    "_replace_image_parts", "_strip_images_from_tool_msg",
]
_VERBATIM_METHODS = ["_dedupe_tool_results", "_prune_boundary", "_tail_soft_ceiling", "_demote_tool_result_at"]


def _normalized_ast(func) -> str:
    """AST of ``func`` without docstrings: comparing source text would trip over wording, not logic."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, ast.FunctionDef) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


@pytest.mark.real_hermes
@pytest.mark.parametrize("name", _VERBATIM_FUNCTIONS + _VERBATIM_METHODS)
def test_parity_helpers_are_verbatim_copies(name, real_mods, stub_mods):
    """The helpers THIRD_PARTY_NOTICES calls verbatim copies are still the same code (docstrings aside)."""
    def get(mod):
        return getattr(mod.ContextCompressor, name) if name in _VERBATIM_METHODS else getattr(mod, name)

    assert _normalized_ast(get(stub_mods.cc)) == _normalized_ast(get(real_mods.cc)), (
        f"{name} drifted from Hermes: re-copy it into tests/hermes_stub (and keep the notice) or drop the claim"
    )


# ---- the stub itself behaves like a compressor (guards the stub, not Hermes) ---------------
def test_stub_compress_smoke(stub_mods):
    cc = stub_mods.cc
    engine = _engine(cc, protect_last_n=4)
    msgs = _big_tool_session(rounds=8, size=9_000)
    out = engine.compress(msgs, current_tokens=60_000, focus_topic="the repo", bypass_cooldown=True)
    assert out is not msgs and len(out) < len(msgs)
    assert engine.compression_count == 1 and engine._previous_summary
    assert engine._last_compress_args["bypass_cooldown"] is True
    assert out[-1] == msgs[-1], "the newest user message stays in the tail"
    call_ids = {tc["id"] for m in out for tc in m.get("tool_calls") or []}
    result_ids = {m["tool_call_id"] for m in out if m["role"] == "tool"}
    assert result_ids <= call_ids, "orphan tool results after compression"
    assert not any(m.get("role") == "assistant" and m.get("tool_calls") and not
                   {tc["id"] for tc in m["tool_calls"]} <= result_ids for m in out[:-1])
    assert any(cc.SUMMARY_PREFIX in (m.get("content") or "") for m in out), "summary row missing"
    assert engine.should_compress(engine.threshold_tokens) and not engine.should_compress(1)
