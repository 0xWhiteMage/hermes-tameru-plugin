"""Unit tests of the replay harness itself (fixtures, scenario, metrics). No Hermes needed."""
from __future__ import annotations

import json
import random
import sys
import time
import types

import pytest
from fixtures import hermes_payloads as hp
from replay import (
    OVERFLOW_RETRY_PAUSE_SECONDS,
    FakeSummarizer,
    VirtualClock,
    _common_prefix_len,
    _Session,
    _wire,
    error_fingerprint,
    error_fingerprints,
    pairing_violations,
    simulated_clock,
    visible_text,
)
from scenario import CATEGORIES, N_TURNS, build_scenario


def rng(seed=1):
    return random.Random(seed)


# ---- fixtures ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "make",
    [
        lambda r: hp.orders_service_source(r, patched=False),
        lambda r: hp.orders_service_source(r, patched=True),
        lambda r: hp.server_log(r)[0],
        lambda r: hp.cjk_log(r),
        lambda r: hp.pytest_output(r, failed=["test_a"], n_passed=10),
        lambda r: hp.git_diff(r, service_old=hp.RESERVE_STOCK_BUGGY, service_new=hp.RESERVE_STOCK_FIXED),
        lambda r: hp.grep_n_output(r, "Foo"),
        lambda r: hp.gh_api_runs(r, 50),
        lambda r: hp.settings_yaml(r),
        lambda r: hp.skill_body(r),
    ],
)
def test_generators_are_deterministic_per_seed(make):
    assert make(rng(7)) == make(rng(7))
    assert make(rng(7)) != make(rng(8))


def test_envelope_shapes_match_hermes_tools():
    rf = json.loads(hp.read_file_result("a\nb\n"))
    assert list(rf) == ["content", "total_lines", "file_size", "truncated", "is_binary", "is_image", "not_found"]
    assert rf["content"] == "1|a\n2|b" and rf["total_lines"] == 2
    term = json.loads(hp.terminal_result("out", exit_code=1))
    assert list(term) == ["output", "exit_code", "error"] and term["error"] is None
    few = json.loads(hp.search_files_result([("a.py", 1, "x")] * 3))
    many = json.loads(hp.search_files_result([("a.py", 1, "x")] * 5))
    assert "matches" in few and "matches_text" in many and many["matches_text"].startswith("a.py\n  1: x")
    patch = json.loads(hp.patch_result("d", ["f.py"]))
    assert patch == {"success": True, "diff": "d", "files_modified": ["f.py"]}


def test_persisted_output_block_shape():
    block = hp.persisted_output("line1\nline2\n", 187_402, "/spill/call_1.txt")
    assert block.startswith("<persisted-output>\nThis tool result was too large (187,402 characters, 183.0 KB).")
    assert "Full output saved to: /spill/call_1.txt\n" in block
    assert "Preview (first 12 chars):\nline1\nline2\n\n..." in block and block.endswith("</persisted-output>")


def test_patch_fixture_is_consistent_with_the_files():
    old = hp.orders_service_source(rng(3), patched=False)
    new = hp.orders_service_source(rng(3), patched=True)
    assert hp.RESERVE_STOCK_BUGGY in old and hp.RESERVE_STOCK_FIXED in new
    assert old.replace(hp.RESERVE_STOCK_BUGGY, hp.RESERVE_STOCK_FIXED) == new


def test_server_log_has_one_fingerprint_per_error_template():
    text, errors = hp.server_log(rng(5), n_lines=450)
    fps = error_fingerprints("\n".join(errors))
    assert len(fps) == len(hp.ERROR_TEMPLATES)
    # the surrounding INFO/WARN lines never count as errors
    assert set(error_fingerprints(text)) == set(fps)
    quiet, none = hp.server_log(rng(5), n_lines=450, with_errors=False)
    assert none == [] and error_fingerprints(quiet) == {}


# ---- error fingerprints -----------------------------------------------------------------------
def test_error_fingerprint_masks_volatile_fields_and_count_prefix():
    a = "2025-03-14T02:14:36.875Z ERROR payments.gateway Timeout after 1437ms order=ORD-21413 trace=9f3a1c2d"
    b = "[×499] 2025-03-14T03:55:01.001Z ERROR payments.gateway Timeout after 12ms order=ORD-9 trace=deadbeef"
    assert error_fingerprint(a) == error_fingerprint(b)
    uid = "ERROR x entry_id=0a1b2c3d-0000-4000-8000-0123456789ab"
    assert "<uuid>" in error_fingerprint(uid)
    assert len(error_fingerprint("ERROR " + "x" * 500)) == 160


def test_error_fingerprints_selects_error_lines_only():
    text = (
        "INFO all good\ncompleted with 0 errors\nfailed=0\nerrors: 0\n"
        "ERROR boom 1\nERROR boom 2\nValueError: bad\nTraceback (most recent call last):\npanic: nil map\n"
        "an error in prose\nFAILED tests/x.py::t"
    )
    fps = error_fingerprints(text)
    assert list(fps.values()) == ["ERROR boom 1", "ValueError: bad", "Traceback (most recent call last):", "panic: nil map", "FAILED tests/x.py::t"]


# ---- scenario ---------------------------------------------------------------------------------
def test_scenario_is_deterministic_and_complete():
    a, b = build_scenario(0, N_TURNS), build_scenario(0, N_TURNS)
    assert [t.user for t in a.turns] == [t.user for t in b.turns]
    assert [s.result for t in a.turns for r in t.rounds for s in r.steps] == [
        s.result for t in b.turns for r in t.rounds for s in r.steps
    ]
    assert len(a.turns) == N_TURNS - 1  # the final-question turn is added by run_session
    assert {g.category for g in a.gold} == set(CATEGORIES)
    assert len({g.id for g in a.gold}) == len(a.gold) == 17
    assert sum(g.needed for g in a.gold) == 11
    # needles never contain JSON-significant characters, so they match raw and unwrapped text alike
    assert all('"' not in g.needle and "\\" not in g.needle for g in a.gold)
    other = build_scenario(1, N_TURNS)
    assert [g.needle for g in a.gold if g.id == "A3"] != [g.needle for g in other.gold if g.id == "A3"]


def test_scenario_turns_parameter_drops_later_gold():
    short = build_scenario(0, 8)
    assert len(short.turns) == 7
    assert all(g.turn <= 7 for g in short.gold)
    with pytest.raises(ValueError):
        build_scenario(0, 3)
    with pytest.raises(ValueError):
        build_scenario(0, N_TURNS + 1)


def test_scenario_has_the_required_payload_kinds():
    sc = build_scenario(0, N_TURNS)
    results = [(s.name, s.result) for t in sc.turns for r in t.rounds for s in r.steps]
    joined = "\n".join(r for _, r in results)
    assert any(n == "read_file" and '"content": "1|' in r for n, r in results)  # N| gutter
    assert any('"exit_code": 1' in r and "short test summary info" in r for _, r in results)  # pytest failure
    assert "diff --git" in joined and "@@ -" in joined  # git diff
    assert any("StockReservationConflict:" in r and ".py:" in r for _, r in results)  # rg -n
    assert "     1\\t" in joined  # cat -n
    assert any(r.count("head_sha") >= 300 for _, r in results)  # gh api list of 300
    assert "库存预留失败" in joined and "決済タイムアウト" in joined and "재고" in joined  # CJK
    assert "<persisted-output>" in joined  # spill stub
    assert any(n == "skill_view" for n, _ in results)
    reads = [s for t in sc.turns for r in t.rounds for s in r.steps if s.name == "read_file"]
    assert len(reads) >= 6
    # read -> patch -> re-read of the same file, and identical repeated calls
    seq = [(s.name, s.args.get("path") or s.args.get("command")) for t in sc.turns for r in t.rounds for s in r.steps]
    i = seq.index(("patch", "src/orders/service.py"))
    assert ("read_file", "src/orders/service.py") in seq[:i] and ("read_file", "src/orders/service.py") in seq[i:]
    assert len(seq) != len(set(seq))


# ---- metrics machinery ------------------------------------------------------------------------
def test_common_prefix_len():
    assert _common_prefix_len("abcdef", "abcxef") == 3
    assert _common_prefix_len("abc", "abcdef") == 3
    assert _common_prefix_len("", "x") == 0
    assert _common_prefix_len("same", "same") == 4
    big = "x" * 100_000
    assert _common_prefix_len(big + "a", big + "b") == 100_000


def _tool_pair(cid, content="x"):
    return (
        {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": cid, "content": content},
    )


def test_pairing_violations_detects_each_kind():
    a1, t1 = _tool_pair("c1")
    a2, t2 = _tool_pair("c2")
    sysm, user = {"role": "system", "content": "s"}, {"role": "user", "content": "u"}
    assert pairing_violations([sysm, user, a1, t1, a2, t2]) == []
    assert any("unanswered" in v for v in pairing_violations([sysm, user, a1, t1, a2]))
    assert any("orphan" in v for v in pairing_violations([sysm, user, t1]))
    assert any("leading tool" in v for v in pairing_violations([sysm, t1]))
    assert any("orphan" in v for v in pairing_violations([sysm, user, t1, a1]))  # result before its call
    assert any("duplicate tool result" in v for v in pairing_violations([sysm, user, a1, t1, t1]))
    assert any("duplicate tool_call" in v for v in pairing_violations([sysm, user, a1, t1, a1]))
    # providers want each result directly after the assistant message that made the call
    assert any("directly follow" in v for v in pairing_violations([sysm, user, a1, user, t1]))
    assert any("directly follow" in v for v in pairing_violations([sysm, a1, t1, a2, user, t2]))
    two = {"role": "assistant", "content": "", "tool_calls": a1["tool_calls"] + a2["tool_calls"]}
    assert pairing_violations([sysm, user, two, t2, t1]) == []  # parallel calls: any order inside the block
    assert any("directly follow" in v for v in pairing_violations([sysm, user, a1, t1, a2, t2, t1]))


class _InertEngine:
    """Just enough engine for ``_Session``: never compresses, never prunes."""

    def should_compress(self, tokens=None):
        return False

    def update_from_response(self, usage):
        self.usage = usage


class _SpyEngine(_InertEngine):
    """Records which gate ran; ``compress_next`` makes ``should_compress`` fire once."""

    awaiting_real_usage_after_compression = False

    def __init__(self):
        self.calls: list[str] = []
        self.compress_next = False

    def should_compress(self, tokens=None):
        self.calls.append("should_compress")
        fire, self.compress_next = self.compress_next, False
        return fire

    def compress(self, messages, current_tokens=None):
        self.calls.append("compress")
        return messages[:1] + messages[-1:]

    def prune_tool_results_only(self, messages, current_tokens=None):
        self.calls.append("prune")
        return messages, 0


def _chars4(messages):
    return sum(len(_wire(m)) for m in messages) // 4


def test_cache_cost_model_prefix_read_and_write():
    sess = _Session(_InertEngine(), None, estimate=_chars4)
    base = [{"role": "system", "content": "s" * 1000}, {"role": "user", "content": "u" * 500}]
    msgs = sess.request(list(base))
    first = sess.request_chars[0]
    assert sess.cached_chars == [0] and sess.invalidated_chars == [0]
    assert sess.cost_x100 == 125 * first  # first request: everything is a cache write
    # appending: the whole previous request is a cached prefix
    msgs = msgs + [{"role": "assistant", "content": "a" * 300}]
    sess.request(msgs)
    second = sess.request_chars[1]
    assert sess.cached_chars[1] == first and sess.invalidated_chars[1] == 0
    assert sess.cost_x100 == 125 * first + 10 * first + 125 * (second - first)
    # rewriting the middle message breaks the prefix at that message
    rewritten = [msgs[0], {"role": "user", "content": "u" * 100}, msgs[2]]
    sess.request(rewritten)
    third = sess.request_chars[2]
    shared = len(_wire(msgs[0])) + 1 + _common_prefix_len(_wire(msgs[1]), _wire(rewritten[1]))
    assert sess.cached_chars[2] == shared
    assert sess.invalidated_chars[2] == second - shared
    assert sess.prefix_breaks == 1
    assert sess.cost_x100 == 125 * first + 10 * first + 125 * (second - first) + 10 * shared + 125 * (third - shared)
    assert sess.engine.usage["prompt_tokens"] == _chars4(rewritten) + 3_000


def test_wire_projection_drops_internal_keys():
    m = {"role": "tool", "name": "t", "tool_name": "t", "content": "c", "tool_call_id": "x", "_compaction_tail": True,
         "timestamp": 1.0, "finish_reason": "stop"}
    assert json.loads(_wire(m)) == {"role": "tool", "name": "t", "content": "c", "tool_call_id": "x"}


def test_visible_text_searches_raw_and_unwrapped_tool_output():
    msg = {"role": "tool", "content": json.dumps({"output": "line1\nERROR boom 7\n"})}
    text = visible_text([msg])
    assert "ERROR boom 7" in error_fingerprints(text).values()
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "patch", "arguments": '{"new_string": "a\\nrelease_holds"}'}}]}
    assert "release_holds" in visible_text([call])


# ---- fake summary LLM -------------------------------------------------------------------------
def _fresh_prompt(records):
    return "preamble\n\nTURNS TO SUMMARIZE:\n" + "\n\n".join(records) + "\n\nUse this exact structure:\n\n## Historical Task Snapshot\n..."


def test_fake_summarizer_keeps_first_200_chars_of_every_turn():
    s = FakeSummarizer()
    long_user = "[USER]: " + "u" * 500
    out = s(messages=[{"role": "user", "content": _fresh_prompt([long_user, "[ASSISTANT]: hi", "[TOOL RESULT call_1]: " + "t" * 400])}])
    text = out["choices"][0]["message"]["content"]
    assert out["choices"][0]["finish_reason"] == "stop"
    assert text.startswith("## Historical Task Snapshot\n" + "u" * 200 + "\n")
    assert "1. [USER]: " + "u" * (200 - len("[USER]: ")) + "\n" in text
    assert "u" * 201 not in text and "t" * 190 not in text
    assert s.calls == 1 and s.prompt_chars > 900


def test_fake_summarizer_iterative_update_carries_and_degrades_previous_entries():
    s = FakeSummarizer()
    first = s(messages=[{"role": "user", "content": _fresh_prompt(["[USER]: ask", "[ASSISTANT]: " + "a" * 300])}])
    prev = first["choices"][0]["message"]["content"]
    prompt = (
        "preamble\n\nPREVIOUS SUMMARY:\n" + prev + "\n\nNEW TURNS TO INCORPORATE:\n[ASSISTANT]: " + "b" * 300
        + "\n\nUpdate the summary using this exact structure."
    )
    text = s(messages=[{"role": "user", "content": prompt}])["choices"][0]["message"]["content"]
    entries = [ln.split(". ", 1)[1] for ln in text.split("## Completed Actions\n")[1].splitlines() if ln.strip()]
    assert [len(e) for e in entries] == [len("[USER]: ask"), 120, 200]  # carried entry cut to 120, new one to 200
    assert text.startswith("## Historical Task Snapshot\nask\n")  # no new user turn: the previous task is kept


def test_fake_summarizer_without_any_user_turn_uses_the_sentinel():
    out = FakeSummarizer()(messages=[{"role": "user", "content": _fresh_prompt(["[ASSISTANT]: x"])}])
    assert out["choices"][0]["message"]["content"].startswith(
        "## Historical Task Snapshot\nNone. This session contains no user-authored turns.\n"
    )


# ---- the Hermes gate placement ----------------------------------------------------------------
def test_prune_runs_only_on_requests_that_follow_tool_results():
    """turn_preflight.run_preflight_compression (turn start): compress gate only. compress_after_tool_results: + prune."""
    sysm, user = {"role": "system", "content": "s"}, {"role": "user", "content": "u"}
    a1, t1 = _tool_pair("c1")
    eng = _SpyEngine()
    sess = _Session(eng, None, estimate=_chars4)
    sess.request([sysm, user])  # turn start
    assert eng.calls == ["should_compress"]
    eng.calls.clear()
    sess.request([sysm, user, a1, t1])  # after a tool round
    assert eng.calls == ["should_compress", "prune"]
    eng.calls.clear()
    sess.request([sysm, user, a1, t1, {"role": "assistant", "content": "done"}, user])  # next user turn
    assert eng.calls == ["should_compress"] and sess.prune_calls == 1


def test_compress_replaces_the_prune_on_both_kinds_of_request():
    sysm, user = {"role": "system", "content": "s"}, {"role": "user", "content": "u"}
    a1, t1 = _tool_pair("c1")
    for transcript in ([sysm, user, user], [sysm, user, a1, t1]):
        eng = _SpyEngine()
        eng.compress_next = True
        sess = _Session(eng, None, estimate=_chars4)
        sess.request(list(transcript))
        assert eng.calls == ["should_compress", "compress"]
        assert sess.compress_calls == 1 and sess.compactions == 1 and sess.prune_calls == 0


def test_no_gate_acts_while_the_engine_awaits_real_usage():
    eng = _SpyEngine()
    eng.awaiting_real_usage_after_compression = True
    sess = _Session(eng, None, estimate=_chars4)
    a1, t1 = _tool_pair("c1")
    sess.request([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, a1, t1])
    assert eng.calls == []  # Hermes returns early: a fresh checkpoint must reach the provider first


def test_prune_commit_needs_a_new_list_and_a_count():
    class Pruning(_SpyEngine):
        mode = "same_list"

        def prune_tool_results_only(self, messages, current_tokens=None):
            if self.mode == "same_list":
                return messages, 3  # count without a new list: not a commit
            if self.mode == "zero":
                return list(messages), 0  # new list without a count: not a commit
            return messages[:1] + messages[-2:], 2

    sysm, user = {"role": "system", "content": "s"}, {"role": "user", "content": "u"}
    a1, t1 = _tool_pair("c1")
    transcript = [sysm, user, a1, t1]
    for mode, committed in (("same_list", 0), ("zero", 0), ("commit", 1)):
        eng = Pruning()
        eng.mode = mode
        sess = _Session(eng, None, estimate=_chars4)
        sess.request(list(transcript))
        assert sess.prune_commits == committed, mode


# ---- error fingerprints follow the engine's line rules -----------------------------------------
def test_error_fingerprints_split_lines_like_the_engine_and_cap_them():
    # only \n, \r\n and \r end a line (not \x0c / U+2028 as str.splitlines() would)
    assert list(error_fingerprints("ERROR a\r\nERROR b\rERROR c")) == ["error a", "error b", "error c"]
    assert len(error_fingerprints("x\x0cERROR y\n")) == 1 and len(error_fingerprints("ERROR\u2028ERROR z")) == 1
    # a level token beyond the first 2,000 chars of a line is not seen (the engine's _RX_LINE_CAP)
    assert error_fingerprints("ok " * 800 + "ERROR late") == {}
    assert len(error_fingerprints("ERROR " + "z" * 5_000)) == 1


# ---- the fake summarizer notices a Hermes prompt it cannot read -------------------------------
def test_fake_summarizer_counts_prompts_it_cannot_parse():
    s = FakeSummarizer()
    s(messages=[{"role": "user", "content": _fresh_prompt(["[USER]: ask"])}])
    assert s.unparsed == 0
    s(messages=[{"role": "user", "content": "preamble\n\nTURNS TO SUMMARIZE:\n<turn role=user>ask</turn>\n\nUse this exact structure:\n"}])
    s(messages=[{"role": "user", "content": "no recognisable section at all"}])
    assert s.unparsed == 2


# ---- gold facts say what they claim -------------------------------------------------------------
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_gold_needles_occur_only_where_the_fact_is_planted(seed):
    """A needle that also shows up elsewhere (e.g. ``staging-3`` inside a generated run name) would let an engine
    keep the fact for the wrong reason. Artifacts legitimately recur in tool output of the same file."""
    sc = build_scenario(seed)
    for g in sc.gold:
        if g.category == "artifact" and g.source != "tool":
            continue  # a patched function name / test name is expected in the patch args and in re-reads
        texts = {"user": [], "assistant": [], "tool": []}
        for t in sc.turns:
            texts["user"].append(("", t.user))
            texts["assistant"].append(("", t.final))
            for r in t.rounds:
                texts["assistant"].append(("", r.say))
                for st in r.steps:
                    texts["tool"].append((st.key, st.result))
                    texts["assistant"].append(("", json.dumps(st.args)))  # call arguments ride in assistant rows
        for role, rows in texts.items():
            for key, text in rows:
                if g.needle in text or json.dumps(g.needle)[1:-1] in text:
                    if role == g.source == "tool":
                        assert key == g.call_key, f"{g.id} also appears in the result of {key}"
                    else:
                        assert role == g.source, f"{g.id} ({g.source}) also appears in a {role} row"


def test_byte_stability_check_flags_rewrites_only_outside_commits():
    sess = _Session(object(), None, estimate=lambda messages: 0)
    row = {"role": "tool", "tool_call_id": "a", "content": "x"}
    sess._check_stability([row, {"role": "tool", "tool_call_id": "b", "content": "k"}], committed=False)
    sess._check_stability([{**row, "content": "y"}, {"role": "tool", "tool_call_id": "b", "content": "k"}], committed=True)
    assert sess.rewrites == []                      # a commit (prune or compaction) may rewrite rows
    sess._check_stability([{**row, "content": "y"}, {"role": "tool", "tool_call_id": "b", "content": "k"}], committed=False)
    assert sess.rewrites == []                      # same bytes: stable
    sess._check_stability([{**row, "content": "z"}], committed=False)
    assert len(sess.rewrites) == 1 and "a" in sess.rewrites[0]


# ---- the simulated clock ------------------------------------------------------------------------------
def test_virtual_clock_moves_only_when_advanced_and_is_deterministic():
    a, b = VirtualClock(20), VirtualClock(20)
    t0, w0 = a.monotonic(), a.time()
    assert (t0, w0) == (b.monotonic(), b.time()) and a.monotonic() == t0  # does not drift on its own
    a.advance(300)
    assert a.monotonic() - t0 == 300 and a.time() - w0 == 300


def test_simulated_clock_patches_only_the_time_name_of_compression_modules_and_restores_it():
    mod = types.ModuleType("agent.context_compressor_unit_probe")  # matches the compression-module prefix
    mod.time = time
    other = types.ModuleType("agent.other_unit_probe")
    other.time = time
    sys.modules[mod.__name__], sys.modules[other.__name__] = mod, other
    try:
        clock = VirtualClock(20)
        with simulated_clock(clock):
            assert mod.time.monotonic() == clock.monotonic() and mod.time.time() == clock.time()
            clock.advance(301)
            assert mod.time.monotonic() == clock.monotonic()
            assert mod.time.perf_counter is time.perf_counter and mod.time.sleep is time.sleep  # the rest stays real
            assert other.time is time and time.monotonic() != clock.monotonic()  # nothing else is touched
        assert mod.time is time
        with pytest.raises(RuntimeError), simulated_clock(clock):
            raise RuntimeError("boom")
        assert mod.time is time  # restored when the run raises
    finally:
        del sys.modules[mod.__name__], sys.modules[other.__name__]


class _BackoffEngine(_SpyEngine):
    """Hermes-shaped gate: ``_automatic_compression_blocked`` is true until the (virtual) clock passes ``until``."""

    threshold_tokens = 0

    def __init__(self, clock=None):
        super().__init__()
        self.clock, self.until, self.kwargs = clock, 0.0, []

    def _now(self):
        return self.clock.monotonic() if self.clock else 0.0

    def _automatic_compression_blocked(self, *, ignore_cooldown=False):
        return self._now() < self.until

    def _compression_block_reason(self, *, ignore_cooldown=False):
        return f"structural_backoff:{self.until - self._now():.0f}" if self._now() < self.until else None


def _big(n, tail="x"):
    return [{"role": "system", "content": "s"}, {"role": "user", "content": tail * n}]


def _realistic(engine, window=100, clock=None, **kw):
    return _Session(engine, None, estimate=_chars4, realistic=True, clock=clock, context_length=window, **kw)


def test_realistic_clock_ticks_once_per_request_slot_sent_rejected_or_skipped():
    clock = VirtualClock(20)
    eng = _BackoffEngine(clock)
    sess = _realistic(eng, window=10_000_000, clock=clock)
    t0 = clock.monotonic()
    sess.request(_big(10))
    sess.request(_big(10))
    assert clock.monotonic() - t0 == 40
    sess.turn_failed = True
    sess.request(_big(10))  # a skipped slot of a failed turn still takes its time
    assert clock.monotonic() - t0 == 60


# ---- the context-overflow path --------------------------------------------------------------------------
def _shrunk(messages, keep):
    return messages[:-1] + [dict(messages[-1], content=messages[-1]["content"][: int(len(messages[-1]["content"]) * keep)])]


class _ShrinkingEngine(_BackoffEngine):
    """compress() keeps ``keep`` of the last message; the signature has Hermes' ``bypass_cooldown``."""

    def __init__(self, keep=0.1, clock=None):
        super().__init__(clock)
        self.keep = keep

    def compress(self, messages, current_tokens=None, focus_topic=None, force=False, memory_context="", bypass_cooldown=False):
        self.kwargs.append({"current_tokens": current_tokens, "bypass_cooldown": bypass_cooldown})
        return _shrunk(messages, self.keep)


class _PlainSignatureEngine(_ShrinkingEngine):
    """An engine whose ``compress`` knows only ``current_tokens`` (older plugin signatures)."""

    def compress(self, messages, current_tokens=None):
        self.kwargs.append({"current_tokens": current_tokens})
        return _shrunk(messages, self.keep)


_OVER = 5_016  # rough tokens of ``_big(8000)`` under ``_chars4`` plus the 3,000-token tool-schema overhead


def test_overflow_is_rejected_unbilled_compressed_with_bypass_and_retried():
    clock = VirtualClock(20)
    eng = _ShrinkingEngine(keep=0.1, clock=clock)
    sess = _realistic(eng, window=4000, clock=clock)
    sess.begin_turn()
    out = sess.request(_big(8000))
    assert sess.overflow_errors == 1 and sess.failed_turns == 0 and sess.turn_attempts == 1
    assert eng.kwargs == [{"current_tokens": _OVER, "bypass_cooldown": True}]
    assert len(sess.request_chars) == 1 and sess.request_chars[0] < 4000 * 4  # only the recovered request was sent
    assert sess.requests_over_window == 0 and sess.max_sent_tokens <= 4000 < sess.max_attempted_tokens == _OVER
    assert sess.overflow_compactions == 1 and sess.compactions == 1 and len(out[-1]["content"]) == 800
    assert clock.elapsed == OVERFLOW_RETRY_PAUSE_SECONDS + 20


def test_overflow_without_a_bypass_parameter_calls_the_engine_the_old_way():
    eng = _PlainSignatureEngine(keep=0.1)
    sess = _realistic(eng, window=4000)
    sess.begin_turn()
    sess.request(_big(8000))
    assert eng.kwargs == [{"current_tokens": _OVER}] and sess.failed_turns == 0


def test_overflow_budget_is_the_turns_and_exhaustion_fails_it_once_and_skips_the_rest():
    eng = _ShrinkingEngine(keep=0.97)  # only 3 % smaller
    sess = _realistic(eng, window=4000)
    sess.begin_turn()
    msgs = sess.request(_big(8000))
    # a 3 % shrink is "no progress" in Hermes' scoring (< 5 % fewer tokens, same message count): the turn ends at once
    assert sess.failed_turns == 1 and sess.failure_reasons == ["compression_exhausted"] and sess.overflow_errors == 1
    assert sess.request_chars == [] and sess.cost_x100 == 0  # nothing billed
    sess.request(msgs)  # later requests of the failed turn are skipped, not attempted
    assert sess.overflow_errors == 1 and sess.failed_turns == 1 and eng.kwargs.__len__() == 1
    sess.begin_turn()  # the next user turn starts with a fresh budget and tries again
    sess.request(msgs)
    assert sess.overflow_errors == 2 and sess.failed_turns == 2


def test_overflow_attempts_cap_at_max_compression_attempts():
    eng = _ShrinkingEngine(keep=0.6)  # shrinks >5 % but never enough to fit
    sess = _realistic(eng, window=100, max_compression_attempts=3)
    sess.begin_turn()
    sess.request(_big(8000))
    assert len(eng.kwargs) == 3 and sess.overflow_errors == 4  # 4th rejection: count_attempt exceeds the cap
    assert sess.failure_reasons == ["compression_exhausted"] and sess.request_chars == []


def test_overflow_behind_a_structural_backoff_defers_the_turn_and_refunds_the_attempt():
    clock = VirtualClock(20)
    eng = _ShrinkingEngine(keep=0.1, clock=clock)
    eng.until = clock.monotonic() + 300
    sess = _realistic(eng, window=4000, clock=clock)
    sess.begin_turn()
    sess.request(_big(8000))
    assert eng.kwargs == [] and sess.turn_attempts == 0  # compress never ran, the attempt is refunded
    assert sess.failure_reasons == ["deferred:structural_backoff"] and sess.failed_turns == 1
    clock.advance(301)  # the backoff lapses: the next turn compresses and goes through
    sess.begin_turn()
    sess.request(_big(8000))
    assert sess.failed_turns == 1 and len(eng.kwargs) == 1 and len(sess.request_chars) == 1


def test_normal_compression_spends_the_same_per_turn_budget():
    eng = _SpyEngine()
    sess = _realistic(eng, window=10_000_000, max_compression_attempts=2)
    sess.begin_turn()
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for _ in range(3):
        eng.compress_next = True
        sess.request(list(msgs))
    assert eng.calls.count("compress") == 2 and sess.turn_attempts == 2  # the third request finds the budget spent


def test_unrealistic_session_keeps_the_old_behaviour():
    sess = _Session(_SpyEngine(), None, estimate=_chars4, context_length=100)
    sess.begin_turn()
    sess.request(_big(8000))
    assert sess.overflow_errors == 0 and sess.failed_turns == 0 and len(sess.request_chars) == 1
    assert sess.requests_over_window == 1 and sess.max_sent_tokens > 100  # sent anyway; reported, not rejected
