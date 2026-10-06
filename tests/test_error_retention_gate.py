"""An extract is accepted only when it keeps an exemplar of every error of the original."""
from __future__ import annotations

import pytest
from engine_support import Chat, demote, make_engine, server_log

from hermes_tameru_plugin import engine as engine_module
from hermes_tameru_plugin.config import TameruSettings
from hermes_tameru_plugin.payload import parse_payload
from hermes_tameru_plugin.render import parse_header
from hermes_tameru_plugin.tameru.compress_context import CompressResult, error_fingerprints

LOG = server_log(4)
INNER = parse_payload(LOG).inner
ERRORS = [line for line in INNER.splitlines() if any(f" {lvl} " in line for lvl in ("ERROR", "FATAL", "CRITICAL"))]
assert len(error_fingerprints(INNER)) >= 3


def _setup(settings: TameruSettings | None = None):
    chat = Chat("why did the payment gateway time out?")
    idx = chat.tool("terminal", {"command": "tail -n 450 app.log"}, LOG)
    return make_engine(settings), chat.finish(), idx


def _fake(monkeypatch, texts, *, risk="low", fail_open=False):
    """``compress_context`` that returns ``texts`` in turn (the last one repeats); records the kwargs."""
    calls: list[dict] = []

    def fake(context, query, **kwargs):
        calls.append(kwargs)
        text = texts[min(len(calls), len(texts)) - 1]
        return CompressResult(compressed_text=text, compression_risk=risk, fail_open=fail_open)

    monkeypatch.setattr(engine_module, "compress_context", fake)
    return calls


def test_an_extract_that_drops_an_error_is_rejected_for_the_brief(monkeypatch):
    engine, msgs, idx = _setup()
    calls = _fake(monkeypatch, ["\n".join(INNER.splitlines()[:5])])   # no ERROR line at all
    _, out = demote(engine, msgs, idx)
    header = parse_header(out[idx]["content"])
    assert calls and header["rung"] == "brief"


def test_an_extract_that_keeps_every_error_is_accepted(monkeypatch):
    engine, msgs, idx = _setup()
    _fake(monkeypatch, ["\n".join(ERRORS)])
    _, out = demote(engine, msgs, idx)
    row = out[idx]["content"]
    assert parse_header(row)["rung"] == "extract"
    assert all(line in row for line in ERRORS)


def test_one_missing_error_class_is_enough_to_reject(monkeypatch):
    engine, msgs, idx = _setup()
    kinds = error_fingerprints(INNER)
    some = [line for line in ERRORS if "WorkerCrash" not in line]
    assert len(some) < len(ERRORS) and len(error_fingerprints("\n".join(some))) < len(kinds)
    _fake(monkeypatch, ["\n".join(some)])
    _, out = demote(engine, msgs, idx)
    assert parse_header(out[idx]["content"])["rung"] == "brief"


@pytest.mark.parametrize("kwargs", [{"fail_open": True}, {"risk": "high"}, {"risk": None}])
def test_fail_open_and_risk_above_the_ceiling_are_rejected(monkeypatch, kwargs):
    engine, msgs, idx = _setup()
    _fake(monkeypatch, ["\n".join(ERRORS)], **kwargs)
    _, out = demote(engine, msgs, idx)
    assert parse_header(out[idx]["content"])["rung"] == "brief"


def test_a_higher_risk_ceiling_accepts_a_high_risk_extract(monkeypatch):
    engine, msgs, idx = _setup(TameruSettings(max_risk="high"))
    _fake(monkeypatch, ["\n".join(ERRORS)], risk="high")
    _, out = demote(engine, msgs, idx)
    assert parse_header(out[idx]["content"])["rung"] == "extract"


def test_an_engine_exception_falls_through_to_the_brief(monkeypatch):
    engine, msgs, idx = _setup()

    def boom(*a, **kw):
        raise RuntimeError("engine bug")

    monkeypatch.setattr(engine_module, "compress_context", boom)
    _, out = demote(engine, msgs, idx)
    assert parse_header(out[idx]["content"])["rung"] == "brief"


def test_a_too_big_extract_is_retried_once_with_a_fixed_budget(monkeypatch):
    engine, msgs, idx = _setup()
    big = "\n".join(ERRORS * 12)
    assert len(big) > engine._settings.max_extract_chars
    calls = _fake(monkeypatch, [big, "\n".join(ERRORS)])
    _, out = demote(engine, msgs, idx)
    assert parse_header(out[idx]["content"])["rung"] == "extract"
    assert len(calls) == 2 and "mode" not in calls[0]
    assert calls[1]["mode"] == "fixed" and 0.05 <= calls[1]["budget_ratio"] <= 0.9


def test_the_retry_must_pass_the_same_gates(monkeypatch):
    engine, msgs, idx = _setup()
    big = "\n".join(ERRORS * 12)
    _fake(monkeypatch, [big, "no errors here"])
    _, out = demote(engine, msgs, idx)
    assert parse_header(out[idx]["content"])["rung"] == "brief"


def test_the_engine_is_called_with_the_plugin_options(monkeypatch):
    engine, msgs, idx = _setup(TameruSettings(protect_patterns=(r"ORD-\d+",)))
    calls = _fake(monkeypatch, ["\n".join(ERRORS)])
    demote(engine, msgs, idx)
    kw = calls[0]
    assert kw["ccr"] is False and kw["citations"] is False and kw["pin_patterns"] == [r"ORD-\d+"]
    assert kw["gap_marker"] is engine_module.elision_marker and kw["recursion_markers"] == ("[tameru:",)
    assert kw["min_savings_ratio"] == engine._settings.min_savings


def test_extracts_are_memoized_per_content_and_query(monkeypatch):
    engine, msgs, idx = _setup()
    calls = _fake(monkeypatch, ["\n".join(ERRORS)])
    demote(engine, msgs, idx)
    demote(engine, msgs, idx)
    assert len(calls) == 1
    chat = Chat("a different question about redis")
    idx2 = chat.tool("terminal", {"command": "tail -n 450 app.log"}, LOG)
    demote(engine, chat.finish(), idx2)
    assert len(calls) == 2
