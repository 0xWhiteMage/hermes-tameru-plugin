"""Property / fuzz harness over the ladder and the vendored engine entry points (red-team round, 1.4.1).

Runs a small budget by default (``FUZZ_EXAMPLES``, default 25 per property); the red-team round
ran it with a large budget (see docs/REPORT-1.4.1.md). Skipped when ``hypothesis`` is not installed.

Invariants checked:

* engine: ``compress_context`` / ``brief_context`` / ``preprocess_test_output`` / ``adapt_grep_lines`` never
  raise, are deterministic, and a non-fail-open extract is never longer than its input;
* ladder (seam A): no swallowed exception (telemetry ``error``), deterministic across fresh engines, a rendered
  row is never longer than the original, the extract rung keeps an exemplar of every error fingerprint,
  ``role`` / ``tool_call_id`` are untouched, and a second demotion of the rendered row changes nothing.
"""
from __future__ import annotations

import json
import os

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from engine_support import Chat, demote, make_engine  # noqa: E402
from fixtures import hermes_payloads as hp  # noqa: E402

from hermes_tameru_plugin.hermes_compat import elision_marker  # noqa: E402
from hermes_tameru_plugin.render import classify_render, parse_header  # noqa: E402
from hermes_tameru_plugin.tameru.agent_formats import adapt_grep_lines, preprocess_test_output  # noqa: E402
from hermes_tameru_plugin.tameru.compress_context import (  # noqa: E402
    brief_context,
    compress_context,
    error_fingerprints,
)

EXAMPLES = int(os.environ.get("FUZZ_EXAMPLES", "25"))
SETTINGS = settings(
    max_examples=EXAMPLES, deadline=None, derandomize=os.environ.get("FUZZ_RANDOM") != "1",
    suppress_health_check=list(HealthCheck), database=None,
)

_WORDS = ["payment", "gateway", "timeout", "order", "terminal", "read", "file", "search", "注文", "タイムアウト",
          "خطأ", "שגיאה", "reserve_stock", "-n", "--stat", "ok", "x" * 40]
_LINE = st.one_of(
    st.sampled_from([
        "", " ", "\t", "\r", "ok", "ERROR payment gateway timeout order=991", "Traceback (most recent call last):",
        '  File "/app/orders.py", line 42, in reserve_stock', "ValueError: stock below zero",
        "FAILED tests/test_orders.py::test_reserve_stock_rollback - AssertionError: assert 3 == 2",
        "E       AssertionError: assert 3 == 2", "=" * 30 + " FAILURES " + "=" * 30,
        "_" * 20 + " test_reserve_stock_rollback " + "_" * 20, "-" * 20 + " Captured log call " + "-" * 20,
        "-" * 20 + " Captured stdout call " + "-" * 20, "=" * 40, "-" * 40,
        "INFO     orders:orders.py:12 reserving", "WARNING  orders:orders.py:13 low stock",
        "=" * 10 + " 1 failed, 120 passed in 3.2s " + "=" * 10, "tests/test_orders.py ....F....",
        "src/orders.py:42:def reserve_stock(order):", "src/orders.py:43:    reserve_stock(order)",
        "src/a.py-44-    pass", "--", "commit " + "a" * 40, "Author: A <a@b.c>",
        "<persisted-output>", "</persisted-output>", "Full output saved to: /tmp/x.txt", "\ufeff<persisted-output>",
        "```", '{"a": [1, 2, {"b": "c"}]}', "api_key = 'sk_live_ABCDEFGHIJKLMNOPQRSTUV'",
        "注文 991 のタイムアウト エラー", "خطأ في البوابة", "\x00\x01\x02binary\ufffd\ufffd",
        "make: *** [test] Error 1", "npm ERR! code ELIFECYCLE", "warning: unused variable `x`",
    ]),
    st.builds(lambda w, n: " ".join(w) * n, st.lists(st.sampled_from(_WORDS), min_size=1, max_size=6),
              st.integers(1, 3)),
    st.text(max_size=80),
    st.builds(lambda c, n: c * n, st.sampled_from(["a", "=", "-", "語", " "]), st.integers(100, 6000)),
)
_TEXT = st.builds(
    lambda lines, rep, crlf: ("\r\n" if crlf else "\n").join(lines * rep),
    st.lists(_LINE, min_size=1, max_size=60), st.integers(1, 8), st.booleans(),
)
_QUERY = st.lists(st.sampled_from(_WORDS + ["why did the payment gateway time out?", "rg -n reserve_stock src",
                                            "pytest -q tests/test_orders.py"]), max_size=4)


def _cc(text: str, query: list[str]):
    return compress_context(
        text, query, ccr=False, citations=False, gap_marker=elision_marker, inline_short_gaps=True,
        recursion_markers=("[tameru:",), min_savings_ratio=0.1,
    )


@SETTINGS
@given(_TEXT, _QUERY)
def test_fuzz_engine_entry_points(text: str, query: list[str]) -> None:
    first = _cc(text, query)
    second = _cc(text, query)
    assert first.compressed_text == second.compressed_text
    if not first.fail_open:
        assert len(first.compressed_text) <= len(text)
    q = " ".join(query)
    assert brief_context(text, budget_chars=1200, query=q, gap_marker=elision_marker) == brief_context(
        text, budget_chars=1200, query=q, gap_marker=elision_marker)
    a = preprocess_test_output(text, q, gap_marker=elision_marker)
    assert a == preprocess_test_output(text, q, gap_marker=elision_marker)
    g = adapt_grep_lines(text, q, gap_marker=elision_marker)
    g2 = adapt_grep_lines(text, q, gap_marker=elision_marker)
    assert (g is None) == (g2 is None) and (g is None or g.text == g2.text)


_TOOLS = st.sampled_from(["terminal", "read_file", "search_files", "web_extract", "mcp_x_fetch", "patch",
                          "browser_snapshot", "read", "search"])


def _payload(tool: str, text: str, exit_code: int) -> str:
    if tool == "terminal":
        return hp.terminal_result(text, exit_code=exit_code)
    if tool == "read_file":
        return hp.read_file_result(text)
    return text


@SETTINGS
@given(_TOOLS, _TEXT, st.integers(0, 2), st.sampled_from(["continue", "why did the payment gateway time out?",
                                                          "terminal", "read", "注文のエラーは?"]))
def test_fuzz_ladder(tool: str, text: str, exit_code: int, task: str) -> None:
    content = _payload(tool, text, exit_code)
    rows = []
    for _ in range(2):
        chat = Chat(task)
        idx = chat.tool(tool, {"command": "pytest -q", "path": "src/orders.py"}, content)
        msgs = chat.finish()
        engine = make_engine()
        changed, out = demote(engine, msgs, idx)
        assert "error" not in engine._telemetry.snapshot()["events"], engine._telemetry.snapshot()
        row = out[idx]
        assert row["role"] == "tool" and row["tool_call_id"] == msgs[idx]["tool_call_id"]
        rows.append(row["content"])
    assert rows[0] == rows[1]
    rendered = rows[0]
    if classify_render(rendered) == "v1":
        assert len(rendered) <= len(content)
        header = parse_header(rendered)
        if header["rung"] == "extract":
            from hermes_tameru_plugin.payload import parse_payload
            assert error_fingerprints(parse_payload(content).inner).keys() <= error_fingerprints(rendered).keys()
        # committed bytes never change outside the pressure pass
        again = [dict(m) for m in out]
        changed2, out2 = demote(engine, again, idx)
        assert out2[idx]["content"] == rendered


@SETTINGS
@given(st.lists(st.tuples(_TOOLS, _TEXT, st.integers(0, 2)), min_size=1, max_size=6),
       st.sampled_from([600, 3000, 6000, 20_000]))
def test_fuzz_prune_pass_pairing_and_stability(rounds, budget) -> None:
    """A whole prune (Hermes passes 1-4 with the ladder): every tool row keeps its call pairing, no swallowed
    exception, and a second prune of the result rewrites no row the first one rendered."""
    from engine_support import prune_pass

    from hermes_tameru_plugin.config import TameruSettings
    from hermes_tameru_plugin.hermes_compat import tool_calls_by_id

    chat = Chat("why did the payment gateway time out?")
    for tool, text, code in rounds:
        chat.tool(tool, {"command": "pytest -q"}, _payload(tool, text, code))
    msgs = chat.finish()
    engine = make_engine(TameruSettings(retained_extract_budget_chars=budget))
    out, _ = prune_pass(engine, [dict(m) for m in msgs])
    assert "error" not in engine._telemetry.snapshot()["events"], engine._telemetry.snapshot()
    assert [m.get("role") for m in out] == [m.get("role") for m in msgs]
    calls = tool_calls_by_id(out)
    for before, after in zip(msgs, out):
        if before.get("role") == "tool":
            assert after["tool_call_id"] == before["tool_call_id"] and after["tool_call_id"] in calls
    again, _ = prune_pass(engine, [dict(m) for m in out])
    for first, second in zip(out, again):
        if first.get("role") == "tool" and isinstance(first.get("content"), str) and classify_render(first["content"]):
            assert second["content"] == first["content"]
