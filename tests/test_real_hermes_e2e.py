"""The plugin through Hermes' own loader and agent-init path (the intent of the retired upstream e2e tests).

Real Hermes only. The plugin directory is copied into a temp ``HERMES_HOME/plugins/tameru``, ``context.engine``
is set in that home's ``config.yaml``, and the engine comes out of ``agent_init._select_context_engine`` /
``_build_context_engine``, exactly as ``init_agent`` builds it.

1.3.0 pruned below Hermes' gates and post-checked the summary; 1.4 does neither, so the old assertions moved:
below the gates the input list comes back untouched, above them the seam ladder demotes the old bulky rows.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import types
from pathlib import Path

import pytest
from engine_support import Chat, fake_summary_llm, pytest_failure
from fixtures import hermes_payloads as hp

from hermes_tameru_plugin.render import classify_render, is_tameru_rendered, parse_header

pytestmark = pytest.mark.real_hermes

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "hermes_tameru_plugin"
CONFIG = """\
context:
  engine: tameru
compression:
  protect_last_n: 4
  target_ratio: 0.35
  proactive_prune_tokens: 1000
  proactive_prune_min_result_chars: 200
  proactive_prune_min_reclaim_tokens: 0
"""
SKU_QUERY = "What is the SKU of titanium-torsion-rod?"


# ---- helpers ------------------------------------------------------------------------------------
def install_plugin(home: Path, config: str = CONFIG) -> None:
    """Directory install: ``HERMES_HOME/plugins/tameru`` plus a config.yaml selecting it."""
    (home / "plugins").mkdir(exist_ok=True)
    target = home / "plugins" / "tameru"
    if not target.exists():
        shutil.copytree(PLUGIN_DIR, target, ignore=shutil.ignore_patterns("__pycache__"))
    (home / "config.yaml").write_text(config, encoding="utf-8")


def forget_user_engine_modules() -> None:
    for name in [m for m in sys.modules if m.startswith("_hermes_user_context_engine")]:
        del sys.modules[name]


class FakeSessionDB:
    """The slice of ``SessionDB`` Hermes' prune commit uses; records what ``archive_and_compact`` receives."""

    def __init__(self) -> None:
        self.commits: list[tuple[str, list[dict], dict]] = []

    def archive_and_compact(self, session_id, messages, **kwargs):
        self.commits.append((session_id, [dict(m) for m in messages], kwargs))

    def get_messages(self, session_id, include_compacted=False):
        return []


def agent_stub(session_db=None, **extra):
    return types.SimpleNamespace(
        model="test-model", provider="", base_url="", api_key="", api_mode="chat_completions",
        quiet_mode=True, session_id="sess-1", _compression_threshold_autoraised=None, **extra,
    )


def build_through_agent_init(session_db=None):
    """``config.yaml`` -> ``_select_context_engine`` -> ``_build_context_engine`` -> ``agent.context_compressor``."""
    from agent import agent_init
    from hermes_cli.config import load_config

    cfg = load_config()
    agent = agent_stub()
    settings = agent_init._parse_compression_config(agent, cfg)
    agent_init._build_context_engine(agent, cfg, settings, [], 128_000, session_db)
    return agent.context_compressor


@pytest.fixture
def home(hermes_home):
    install_plugin(hermes_home)
    try:
        yield hermes_home
    finally:
        forget_user_engine_modules()


@pytest.fixture
def engine(home):
    return build_through_agent_init()


def catalog() -> str:
    parts = [{"id": i, "sku": f"SNS-{i:03d}", "name": f"widget-{i}"} for i in range(80)]
    parts[61]["name"] = "titanium-torsion-rod"
    return json.dumps({"catalog": {"parts": parts}})


def catalog_chat(query: str = SKU_QUERY) -> list[dict]:
    chat = Chat(query)
    chat.tool("terminal", {"command": "curl -s https://parts.example/catalog.json"}, hp.terminal_result(catalog()))
    return chat.finish(query)


def tool_rows(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") == "tool"]


# ---- discovery and selection -------------------------------------------------------------------
def test_hermes_loader_finds_the_directory_plugin_by_name(home):
    from plugins.context_engine import find_engine_dir, load_context_engine

    assert find_engine_dir("tameru") == home / "plugins" / "tameru"
    loaded = load_context_engine("tameru")
    assert loaded is not None and loaded.name == "tameru" and loaded.name != "lcm"


def test_context_engine_config_selects_tameru_without_plugins_enabled(home):
    from agent import agent_init
    from agent.context_compressor import ContextCompressor
    from hermes_cli.config import load_config

    cfg = load_config()
    assert cfg["context"]["engine"] == "tameru"
    selected = agent_init._select_context_engine(cfg)
    assert selected is not None and selected.name == "tameru"
    assert isinstance(selected, ContextCompressor)
    assert type(selected).__name__ == "TameruContextEngine"


def test_default_config_keeps_the_builtin_compressor(hermes_home):
    from agent import agent_init
    from hermes_cli.config import load_config

    install_plugin(hermes_home, "compression:\n  protect_last_n: 4\n")
    try:
        assert agent_init._select_context_engine(load_config()) is None
    finally:
        forget_user_engine_modules()


def test_selected_engine_is_independent_of_the_profile_home(tmp_path, monkeypatch):
    from plugins.context_engine import load_context_engine

    for name in ("profile_a", "profile_b"):
        home = tmp_path / name
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        install_plugin(home)
        try:
            assert load_context_engine("tameru").name == "tameru"
        finally:
            forget_user_engine_modules()


def test_reinstalling_over_an_existing_copy_is_idempotent(home):
    from plugins.context_engine import load_context_engine

    first = load_context_engine("tameru")
    shutil.rmtree(home / "plugins" / "tameru")
    install_plugin(home)
    forget_user_engine_modules()
    second = load_context_engine("tameru")
    assert first.name == second.name == "tameru"


def test_the_plugin_system_candidate_survives_the_clone_fallback(home, monkeypatch):
    """Entry-point plugins reach ``_select_context_engine`` as ONE shared instance that gets cloned per agent."""
    from agent import agent_init
    from hermes_cli.config import load_config

    registered = build_through_agent_init()
    monkeypatch.setitem(sys.modules, "plugins.context_engine", types.SimpleNamespace(load_context_engine=lambda n: None))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", types.SimpleNamespace(get_plugin_context_engine=lambda: registered))
    chosen = agent_init._select_context_engine(load_config())
    assert chosen is not None and chosen is not registered and chosen.name == "tameru"
    assert copy.deepcopy(registered).name == "tameru"   # the plain deepcopy route Hermes used before clone_for_agent


# ---- the engine Hermes built --------------------------------------------------------------------
def test_build_context_engine_hands_the_user_compression_config_to_the_engine(home):
    db = FakeSessionDB()
    engine = build_through_agent_init(session_db=db)
    assert engine.name == "tameru" and engine.protect_last_n == 4 and engine.summary_target_ratio == 0.35
    assert engine.proactive_prune_tokens == 1000 and engine.proactive_prune_min_result_chars == 200
    assert engine._session_db is db and engine._session_id == "sess-1"
    status = engine.get_status()["tameru"]
    assert status["enabled"] and all(v for k, v in status["seams"].items() if k != "reasons")
    assert status["isolated"] is False


# ---- pruning: the Hermes gates decide, the ladder rewrites ---------------------------------------
def test_below_the_gate_the_input_object_comes_back_and_nothing_is_archived(home):
    db = FakeSessionDB()
    engine = build_through_agent_init(session_db=db)
    messages = catalog_chat()
    out, n = engine.prune_tool_results_only(messages, current_tokens=500)   # gate: 1000
    assert out is messages and n == 0 and db.commits == []
    assert engine._telemetry.snapshot()["events"] == {}


def test_above_the_gate_the_old_bulky_row_keeps_gold_and_drops_the_rest(home):
    db = FakeSessionDB()
    engine = build_through_agent_init(session_db=db)
    messages = catalog_chat()
    out, n = engine.prune_tool_results_only(messages, current_tokens=60_000)
    assert out is not messages and n > 0
    old, fresh = tool_rows(out)[0], tool_rows(out)[-1]
    assert is_tameru_rendered(old["content"]) and classify_render(old["content"]) == "v1"
    assert "SNS-061" in old["content"] and "SNS-000" not in old["content"]
    assert "[CC-Retrieve:" not in old["content"] and "[§" not in old["content"]   # no CCR / citation markers
    assert fresh["content"] == tool_rows(messages)[-1]["content"]   # the protected tail is untouched
    assert out[0]["content"] == messages[0]["content"]              # system row untouched


def test_archive_and_compact_receives_the_tameru_renders(home):
    db = FakeSessionDB()
    engine = build_through_agent_init(session_db=db)
    chat = Chat("why did the payment gateway time out?")
    chat.tool("terminal", {"command": "pytest -q"}, pytest_failure())
    chat.tool("terminal", {"command": "pytest -q tests/test_orders.py"}, pytest_failure(2))
    messages = chat.finish("what failed?")
    out, n = engine.prune_tool_results_only(messages, current_tokens=60_000)
    assert n >= 2
    (session_id, committed, kwargs), = db.commits
    assert session_id == "sess-1" and "model_config_patch" in kwargs
    rendered = [m for m in tool_rows(committed) if is_tameru_rendered(m["content"])]
    assert len(rendered) == n
    assert {parse_header(m["content"])["ref"] for m in rendered} <= set(
        parse_header(m["content"])["ref"] for m in tool_rows(out) if is_tameru_rendered(m["content"])
    )
    assert all(ref in engine._store for ref in (parse_header(m["content"])["ref"] for m in rendered))


def test_a_noop_prune_returns_the_input_object(home):
    engine = build_through_agent_init(session_db=FakeSessionDB())
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    out, n = engine.prune_tool_results_only(messages, current_tokens=60_000)
    assert out is messages and n == 0


def _japanese_log_chat() -> list[dict]:
    lines = [f"2026-08-19 03:{i % 60:02d}:00 INFO ok" for i in range(150)]
    lines.insert(14, "2026-08-19 03:14:07 エラー: 接続が拒否されました host=db-tokyo-3")
    chat = Chat("何が失敗しましたか？")
    chat.tool("terminal", {"command": "cat /var/log/app.log"}, hp.terminal_result("\n".join(lines)))
    return chat.finish("何が失敗しましたか？")


def test_a_failing_host_line_survives_in_a_japanese_log(home):
    """The failure line has no ASCII ``ERROR``: it must still survive. The engine rates collapsing 150 INFO
    lines ``high`` risk, so at the default ``max_risk`` the row lands on the brief rung (host kept, noise not)."""
    engine = build_through_agent_init(session_db=FakeSessionDB())
    out, n = engine.prune_tool_results_only(_japanese_log_chat(), current_tokens=60_000)
    old = tool_rows(out)[0]["content"]
    assert n > 0 and is_tameru_rendered(old) and "db-tokyo-3" in old
    assert parse_header(old)["rung"] in {"extract", "brief"}


def test_the_japanese_log_collapses_to_the_host_line_when_high_risk_is_allowed(hermes_home):
    config = CONFIG + "plugins:\n  entries:\n    tameru:\n      settings:\n        max_risk: high\n"
    install_plugin(hermes_home, config)
    try:
        engine = build_through_agent_init(session_db=FakeSessionDB())
        out, n = engine.prune_tool_results_only(_japanese_log_chat(), current_tokens=60_000)
    finally:
        forget_user_engine_modules()
    old = tool_rows(out)[0]["content"]
    assert n > 0 and parse_header(old)["rung"] == "extract"
    assert "db-tokyo-3" in old and old.count("INFO ok") < 5


def test_the_plugin_does_not_override_should_compress_semantics(home):
    """An explicit 0 is a real token reading, not an unset sentinel (Hermes' base class decides)."""
    engine = build_through_agent_init()
    engine.threshold_tokens = 100
    engine.last_prompt_tokens = engine.last_total_tokens = 500
    assert engine.should_compress(0) is False


def test_a_short_bulky_chat_is_not_empty_for_the_compress_preflight(home):
    engine = build_through_agent_init()
    chat = Chat(SKU_QUERY)
    chat.tool("terminal", {"command": "curl catalog"}, hp.terminal_result(catalog()))
    chat.tool("terminal", {"command": "echo a"}, hp.terminal_result("ack"))
    chat.tool("terminal", {"command": "echo b"}, hp.terminal_result("pong"))
    chat.user(SKU_QUERY)
    assert engine.has_content_to_compress(chat.messages) is True


# ---- compress: Hermes' own pass, the engine passes arguments through ----------------------------
def test_compress_force_runs_a_real_pass_and_demotes_with_tameru_renders(home, monkeypatch):
    fake_summary_llm(monkeypatch)
    engine = build_through_agent_init()
    chat = Chat("why did the payment gateway time out?")
    for i in range(6):
        chat.tool("terminal", {"command": f"tail -n {100 + i} app.log"}, hp.terminal_result(hp.server_log(__import__("random").Random(i), n_lines=200)[0]))
    messages = chat.finish("what failed?")
    out = engine.compress(messages, current_tokens=60_000, force=True)
    assert out is not messages and len(out) < len(messages)
    assert any("payment gateway timed out" in str(m.get("content")) for m in out)
    assert engine.compression_count == 1
    events = engine._telemetry.snapshot()["events"]
    assert events, "the seam ran under the compress pass scope"


@pytest.mark.parametrize("kwargs", [
    {"focus_topic": "the payment gateway", "force": True, "bypass_cooldown": True},
    {"force": False},
    {"focus_topic": None, "bypass_cooldown": True},
])
def test_compress_passes_focus_force_and_bypass_cooldown_through(home, monkeypatch, kwargs):
    from agent.context_compressor import ContextCompressor

    engine = build_through_agent_init()
    seen: dict = {}
    result = [{"role": "user", "content": "parent's list"}]

    def parent(self, messages, *args, **kw):
        seen.update(kw)
        return result

    monkeypatch.setattr(ContextCompressor, "compress", parent)
    out = engine.compress([{"role": "user", "content": "hi"}], current_tokens=123, **kwargs)
    assert out is result
    assert {k: seen[k] for k in kwargs} == kwargs and seen["current_tokens"] == 123


def test_compress_commits_before_the_summary_so_a_lossy_summary_keeps_the_recoverable_ref(home, monkeypatch):
    """The 1.3.0 post-check is gone: gold survives through the demoted rows and ``tameru_expand``, not a rejection."""
    from agent.context_compressor import ContextCompressor

    engine = build_through_agent_init(session_db=FakeSessionDB())
    messages = catalog_chat()
    pruned, n = engine.prune_tool_results_only(messages, current_tokens=60_000)
    assert n > 0
    ref = parse_header(tool_rows(pruned)[0]["content"])["ref"]

    lossy = [{"role": "user", "content": SKU_QUERY}, {"role": "assistant", "content": "Summary: reviewed."}]
    monkeypatch.setattr(ContextCompressor, "compress", lambda self, msgs, *a, **kw: lossy)
    assert engine.compress(pruned, force=True) is lossy       # nothing second-guesses the parent's list
    expanded = json.loads(engine.handle_tool_call("tameru_expand", {"ref": ref, "grep": "SNS-061"}))
    assert "SNS-061" in expanded["content"]
    assert re.search(r"titanium-torsion-rod", expanded["content"])
