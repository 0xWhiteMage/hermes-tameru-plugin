"""When Hermes' seams are not what the plugin expects, the engine is the stock compressor plus a status flag."""
from __future__ import annotations

import pytest
from engine_support import Chat, make_engine, prune_pass, pytest_failure, server_log

from hermes_tameru_plugin import engine as engine_module
from hermes_tameru_plugin.hermes_compat import SeamReport, probe_seams
from hermes_tameru_plugin.render import classify_render


@pytest.fixture
def broken_seams(monkeypatch):
    """Every probe answers 'seam missing'; the per-class cache is bypassed for the test."""
    report = SeamReport(reasons=("_demote_tool_result_at is not a staticmethod",))
    monkeypatch.setattr(engine_module, "probe_seams", lambda cls: report)
    monkeypatch.setattr(engine_module.TameruContextEngine, "_seam_report", None)
    return report


def _messages() -> list[dict]:
    chat = Chat("why did the payment gateway time out?")
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    chat.tool("terminal", {"command": "tail -n 450 app.log"}, server_log())
    return chat.finish()


def test_probe_says_all_seams_present_on_this_hermes():
    report = engine_module.TameruContextEngine._probe()
    assert report.ok and not report.reasons


def test_missing_seam_gives_stock_behaviour(broken_seams):
    base = engine_module._Base
    engine = make_engine()
    stock = base(model="pending", quiet_mode=True, config_context_length=64_000,
                 proactive_prune_tokens=1, proactive_prune_min_reclaim_tokens=0,
                 proactive_prune_min_result_chars=200, protect_last_n=4)
    messages = _messages()
    ours, n_ours = prune_pass(engine, messages)
    theirs, n_stock = stock._prune_old_tool_results(messages, protect_tail_count=4, min_prune_chars=200)
    assert n_ours == n_stock and ours == theirs
    assert all(classify_render(m["content"]) is None for m in ours if m.get("role") == "tool")
    assert len(engine._store) == 0


def test_missing_seam_status_flag(broken_seams):
    engine = make_engine()
    status = engine.get_status()["tameru"]
    assert status["seams"]["demote"] is False
    assert tuple(status["seams"]["reasons"]) == broken_seams.reasons
    assert status["enabled"] is True


def test_public_entry_points_skip_the_scope_without_the_seam(broken_seams, monkeypatch):
    engine = make_engine()
    seen = []
    parent = engine_module._Base.prune_tool_results_only

    def spy(self, *a, **kw):
        seen.append(engine_module._SCOPE.get())
        return parent(self, *a, **kw)

    monkeypatch.setattr(engine_module._Base, "prune_tool_results_only", spy)
    engine.prune_tool_results_only(_messages(), 500_000)
    assert seen == [None]


def test_a_probe_that_raises_degrades_instead_of_failing(monkeypatch):
    def boom(cls):
        raise RuntimeError("probe bug")

    monkeypatch.setattr(engine_module, "probe_seams", boom)
    monkeypatch.setattr(engine_module.TameruContextEngine, "_seam_report", None)
    engine = make_engine()
    status = engine.get_status()["tameru"]
    assert status["seams"]["demote"] is False and "probe" in status["seams"]["reasons"][0]
    out, _ = prune_pass(engine, _messages())
    assert all(classify_render(m["content"]) is None for m in out if m.get("role") == "tool")


def test_a_fake_class_without_the_seam_probes_as_missing():
    class NoSeam:
        def _prune_old_tool_results(self):
            pass

    report = probe_seams(NoSeam)
    assert not report.demote and not report.ok and report.reasons
