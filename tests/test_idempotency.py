"""Committed rows never change: later passes, other queries and re-reading the plugin's own source."""
from __future__ import annotations

from agent.context_compressor import _is_summary_stub
from engine_support import Chat, make_engine, prune_pass, pytest_failure, server_log, source_file
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.render import classify_render, is_tameru_rendered, parse_header


def _session() -> tuple[Chat, list[int]]:
    chat = Chat("why did the payment gateway time out?")
    rows = [
        chat.tool("terminal", {"command": "pytest -q"}, pytest_failure()),
        chat.tool("terminal", {"command": "tail -n 450 app.log"}, server_log()),
        chat.tool("read_file", {"path": "orders/service.py"}, source_file()),
    ]
    return chat, rows


def test_committed_rows_are_byte_stable_across_five_passes_with_changing_queries():
    chat, rows = _session()
    messages = chat.finish("first question: StockReservationConflict")
    engine = make_engine()
    committed, n = prune_pass(engine, messages)
    assert n >= 3
    snapshot = {i: committed[i]["content"] for i in rows}
    assert all(is_tameru_rendered(c) for c in snapshot.values())

    current = committed
    questions = ["worker crash out of memory", "ledger duplicate key", "redis connection", "x", "what next?"]
    for question in questions:
        current = [*current, {"role": "assistant", "content": "ok"}, {"role": "user", "content": question}]
        for k in range(3):   # fresh tool rounds keep the tail moving, as a live session does
            fill = Chat()
            fill.tool("terminal", {"command": f"echo {question} {k}"}, hp.terminal_result("o" * 900))
            current = [*current, *fill.messages[2:]]
        current, _ = prune_pass(engine, current)
        assert {i: current[i]["content"] for i in rows} == snapshot


def test_a_second_pass_over_its_own_output_changes_nothing():
    chat, rows = _session()
    engine = make_engine()
    once, _ = prune_pass(engine, chat.finish())
    twice, n = prune_pass(engine, once)
    assert twice == once and n == 0


def test_header_only_rows_are_summary_stubs_even_with_worst_case_paths():
    path = "/srv/" + "/".join(["very-long-directory-name"] * 80) + "/orders.py"
    chat = Chat("q")
    idx = chat.tool("read_file", {"path": path}, source_file())
    engine = make_engine()
    out, _ = prune_pass(engine, chat.finish(), pressure=True)
    row = out[idx]["content"]
    assert parse_header(row)["rung"] == "header" and "\n" not in row
    assert len(row) <= 380 and _is_summary_stub(row)


def test_header_only_rows_survive_the_pressure_pass_unchanged():
    chat, rows = _session()
    engine = make_engine()
    headers, _ = prune_pass(engine, chat.finish(), degrade=True)
    assert all(parse_header(headers[i]["content"])["rung"] == "header" for i in rows)
    again, n = prune_pass(engine, headers, pressure=True)
    assert again == headers and n == 0


def _legacy_row() -> str:
    """The 1.3.0 shape: kept blocks joined by a bare ``[…]`` line, no header."""
    blocks = [
        "\n".join(f"def handler_{i}(request):\n    return request.get('k{i}') * {i}" for i in range(j, j + 8))
        for j in (0, 30, 60)
    ]
    return "\n[…]\n".join(blocks)


def test_legacy_rows_are_not_extracted_again():
    legacy = _legacy_row()
    assert classify_render(legacy) == "legacy" and len(legacy) > 200
    chat = Chat("q")
    idx = chat.tool("terminal", {"command": "cat handlers.py"}, legacy)
    out, n = prune_pass(make_engine(), chat.finish())
    assert out[idx]["content"] == legacy and n == 0


def test_legacy_rows_go_header_only_in_the_pressure_pass():
    chat = Chat("q")
    idx = chat.tool("terminal", {"command": "cat handlers.py"}, _legacy_row())
    out, _ = prune_pass(make_engine(), chat.finish(), pressure=True)
    row = out[idx]["content"]
    assert parse_header(row)["rung"] == "header" and _is_summary_stub(row)


def test_source_that_quotes_the_header_grammar_is_still_pruned():
    """Reading the plugin's own render.py is a tool result like any other, not a Tameru row."""
    from pathlib import Path

    import hermes_tameru_plugin.render as render_module

    text = Path(render_module.__file__).read_text(encoding="utf-8")
    assert "[tameru:" in text
    chat = Chat("q")
    idx = chat.tool("read_file", {"path": "render.py"}, hp.read_file_result(text))
    out, n = prune_pass(make_engine(), chat.finish())
    assert n >= 1 and out[idx]["content"] != hp.read_file_result(text)
