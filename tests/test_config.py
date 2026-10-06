"""``config.py``: settings precedence and parsing, Hermes config access, the ``compression.*`` mirror.

Most tests serve a fixed Hermes config through the ``hermes_config`` fixture. The loaders themselves
run against a fake ``hermes_cli.config`` (any mode), a temp ``HERMES_HOME/config.yaml`` (PyYAML
fallback) and, in real mode, Hermes' own loaders and ``_parse_compression_config``.
"""
from __future__ import annotations

import copy
import dataclasses
import inspect
import sys
import types

import pytest

from hermes_tameru_plugin import config as cfg
from hermes_tameru_plugin.config import (
    COMPRESSION_TABLE,
    TameruSettings,
    compression_kwargs,
    load_settings,
    read_hermes_config,
)

ENV = cfg.ENV_PREFIX


def plugin_config(section: str = "settings", **values) -> dict:
    return {"plugins": {"entries": {"tameru": {section: values}}}}


@pytest.fixture
def hermes_config(monkeypatch):
    """``serve(merged, raw)``: what ``read_hermes_config`` returns (nothing by default)."""
    def serve(merged=None, raw=None):
        monkeypatch.setattr(cfg, "read_hermes_config", lambda: (merged or {}, raw or {}))

    serve()
    return serve


@pytest.fixture(scope="module")
def compressor_cls():
    from agent.context_compressor import ContextCompressor

    return ContextCompressor


@pytest.fixture(scope="module")
def init_params(compressor_cls) -> set[str]:
    return set(inspect.signature(compressor_cls.__init__).parameters) - {"self"}


# ---- TameruSettings ------------------------------------------------------------------------------
def test_defaults_match_the_contract():
    assert dataclasses.asdict(TameruSettings()) == {
        "enabled": True, "min_tool_chars": 800, "max_extract_chars": 6000, "brief_chars": 1200,
        "max_risk": "medium", "min_savings": 0.30, "exempt_tools": (), "protect_patterns": (),
        "expand_tool": True, "store_max_entries": 512, "store_max_chars": 32_000_000,
        "pass_char_budget": 4_000_000, "retained_extract_budget_chars": 6000, "prune_tail": "tokens",
        "supersession": True, "ledger": True, "telemetry_log": "", "default_proactive_prune_tokens": 48_000,
        "default_proactive_prune_min_result_chars": 2000,
    }


def test_settings_are_frozen_and_hashable():
    settings = TameruSettings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.enabled = False
    assert hash(settings) == hash(TameruSettings())


# One valid env spelling per field and what it must parse to; asserts every field is settable.
ENV_SAMPLES = {
    "enabled": ("off", False),
    "min_tool_chars": ("100", 100),
    "max_extract_chars": ("5_000", 5000),
    "brief_chars": (" 900 ", 900),
    "max_risk": ("HIGH", "high"),
    "min_savings": ("0.5", 0.5),
    "exempt_tools": ("a, b", ("a", "b")),
    "protect_patterns": (r"\bID-\d+\b", (r"\bID-\d+\b",)),
    "expand_tool": ("no", False),
    "store_max_entries": ("10", 10),
    "store_max_chars": ("1000", 1000),
    "pass_char_budget": ("0", 0),
    "retained_extract_budget_chars": ("2000", 2000),
    "prune_tail": (" COUNT ", "count"),
    "supersession": ("0", False),
    "ledger": ("YES", True),
    "telemetry_log": ("/tmp/t.jsonl", "/tmp/t.jsonl"),
    "default_proactive_prune_tokens": ("0", 0),
    "default_proactive_prune_min_result_chars": ("500", 500),
}


def test_env_samples_cover_every_field():
    assert set(ENV_SAMPLES) == {f.name for f in dataclasses.fields(TameruSettings)}


@pytest.mark.parametrize("name", sorted(ENV_SAMPLES))
def test_every_field_is_settable_from_env(hermes_config, name):
    raw, expected = ENV_SAMPLES[name]
    settings, warnings = load_settings(env={ENV + name.upper(): raw})
    assert warnings == []
    assert settings == dataclasses.replace(TameruSettings(), **{name: expected})


@pytest.mark.parametrize("name", sorted(ENV_SAMPLES))
def test_every_field_is_settable_from_the_plugin_settings(hermes_config, name):
    _, expected = ENV_SAMPLES[name]
    value = list(expected) if isinstance(expected, tuple) else expected
    hermes_config(plugin_config(**{name: value}))
    settings, warnings = load_settings(env={})
    assert getattr(settings, name) == expected
    assert warnings == []


# ---- precedence ----------------------------------------------------------------------------------
def test_env_beats_settings_and_settings_beat_defaults(hermes_config):
    hermes_config(plugin_config(min_tool_chars=500, brief_chars=900))
    settings, warnings = load_settings(env={ENV + "MIN_TOOL_CHARS": "700"})
    assert (settings.min_tool_chars, settings.brief_chars, settings.max_extract_chars) == (700, 900, 6000)
    assert warnings == []


def test_legacy_config_section_is_a_per_key_fallback(hermes_config):
    entry = {"config": {"min_tool_chars": 111, "brief_chars": 222}, "settings": {"brief_chars": 333}}
    hermes_config({"plugins": {"entries": {"tameru": entry}}})
    settings, warnings = load_settings(env={})
    assert (settings.min_tool_chars, settings.brief_chars) == (111, 333)
    assert warnings == []


def test_legacy_config_alone_is_read(hermes_config):
    hermes_config(plugin_config("config", ledger=True))
    assert load_settings(env={})[0].ledger is True


def test_env_defaults_to_os_environ(hermes_config, monkeypatch):
    monkeypatch.setenv(ENV + "MAX_EXTRACT_CHARS", "1234")
    assert load_settings()[0].max_extract_chars == 1234


def test_empty_env_value_is_valid_for_strings_and_tuples(hermes_config):
    hermes_config(plugin_config(telemetry_log="/tmp/x", exempt_tools=["a"]))
    settings, warnings = load_settings(env={ENV + "TELEMETRY_LOG": "", ENV + "EXEMPT_TOOLS": ""})
    assert (settings.telemetry_log, settings.exempt_tools, warnings) == ("", (), [])


class FakeCtx:
    """A plugin context with ``get_config(key, default=None)`` like Hermes' ``PluginContext``."""

    def __init__(self, values=None, error=None):
        self.values, self.error = values or {}, error

    def get_config(self, key, default=None):
        if self.error is not None:
            raise self.error
        return self.values.get(key, default)


def test_ctx_get_config_sits_between_env_and_the_settings_file(hermes_config):
    hermes_config(plugin_config(min_tool_chars=500, brief_chars=900, max_extract_chars=7000))
    ctx = FakeCtx({"min_tool_chars": 600, "brief_chars": 650})
    settings, warnings = load_settings(ctx, env={ENV + "MIN_TOOL_CHARS": "700"})
    assert (settings.min_tool_chars, settings.brief_chars, settings.max_extract_chars) == (700, 650, 7000)
    assert warnings == []


def test_ctx_alone_can_supply_settings(hermes_config):
    settings, warnings = load_settings(FakeCtx({"ledger": True, "exempt_tools": ["x"]}), env={})
    assert (settings.ledger, settings.exempt_tools, warnings) == (True, ("x",), [])


@pytest.mark.parametrize("error", [LookupError("no such key"), KeyError("min_tool_chars")])
def test_ctx_get_config_lookup_error_is_tolerated(hermes_config, error):
    hermes_config(plugin_config(min_tool_chars=500))
    settings, warnings = load_settings(FakeCtx(error=error), env={})
    assert settings.min_tool_chars == 500
    assert warnings == []


def test_ctx_get_config_other_errors_warn_once_and_fall_back(hermes_config):
    hermes_config(plugin_config(min_tool_chars=500))
    settings, warnings = load_settings(FakeCtx(error=RuntimeError("boom")), env={})
    assert settings.min_tool_chars == 500
    assert len(warnings) == 1 and "ctx.get_config" in warnings[0] and "boom" in warnings[0]


@pytest.mark.parametrize("ctx", [object(), types.SimpleNamespace(get_config=None), None])
def test_ctx_without_get_config_is_ignored(hermes_config, ctx):
    hermes_config(plugin_config(min_tool_chars=500))
    settings, warnings = load_settings(ctx, env={})
    assert settings.min_tool_chars == 500
    assert warnings == []


def test_ctx_bad_value_warns_and_falls_through(hermes_config):
    hermes_config(plugin_config(min_tool_chars=500))
    settings, warnings = load_settings(FakeCtx({"min_tool_chars": "lots"}), env={})
    assert settings.min_tool_chars == 500
    assert len(warnings) == 1 and "ctx.get_config('min_tool_chars')" in warnings[0]


# ---- parsing -------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("1", True), ("true", True), ("Yes", True), ("ON", True), ("0", False), ("false", False),
    ("No", False), ("off", False), (" true ", True),
])
def test_bool_words_from_env(hermes_config, raw, expected):
    settings, warnings = load_settings(env={ENV + "LEDGER": raw})
    assert (settings.ledger, warnings) == (expected, [])


@pytest.mark.parametrize("value, expected", [(True, True), (False, False), (1, True), (0, False), ("yes", True)])
def test_bool_values_from_the_settings_file(hermes_config, value, expected):
    hermes_config(plugin_config(ledger=value))
    assert load_settings(env={})[0].ledger is expected


@pytest.mark.parametrize("value", [800.0, "800", " 800 "])
def test_integers_accept_integral_floats_and_numeric_strings(hermes_config, value):
    hermes_config(plugin_config(min_tool_chars=value))
    settings, warnings = load_settings(env={})
    assert (settings.min_tool_chars, warnings) == (800, [])


@pytest.mark.parametrize("value, expected", [(0, 0.0), ("0.45", 0.45), (0.9, 0.9)])
def test_min_savings_accepts_numbers_below_one(hermes_config, value, expected):
    hermes_config(plugin_config(min_savings=value))
    settings, warnings = load_settings(env={})
    assert (settings.min_savings, warnings) == (expected, [])


def test_tuples_from_env_split_on_commas_trim_and_deduplicate(hermes_config):
    settings, warnings = load_settings(env={ENV + "EXEMPT_TOOLS": " alpha, beta ,,gamma,alpha "})
    assert (settings.exempt_tools, warnings) == (("alpha", "beta", "gamma"), [])


def test_tuples_from_the_settings_file_take_lists_or_a_string(hermes_config):
    hermes_config(plugin_config(exempt_tools=[" x ", "y", "x", ""], protect_patterns="p1,p2"))
    settings, warnings = load_settings(env={})
    assert (settings.exempt_tools, settings.protect_patterns, warnings) == (("x", "y"), ("p1", "p2"), [])


# Each of these must leave the field at its default and say why.
BAD_ENV = [
    ("enabled", "maybe"), ("enabled", ""), ("min_tool_chars", "abc"), ("min_tool_chars", "-1"),
    ("min_tool_chars", "1.5"), ("max_extract_chars", "0"), ("brief_chars", "x"), ("min_savings", "1.0"),
    ("min_savings", "-0.1"), ("min_savings", "nan"), ("min_savings", "inf"), ("min_savings", "high"),
    ("max_risk", "extreme"), ("prune_tail", "weeks"), ("protect_patterns", "ok,(unclosed"), ("store_max_entries", "-5"),
    ("default_proactive_prune_tokens", "-1"),
]


@pytest.mark.parametrize("name, raw", BAD_ENV)
def test_bad_env_values_become_defaults_with_a_warning(hermes_config, name, raw):
    settings, warnings = load_settings(env={ENV + name.upper(): raw})
    assert settings == TameruSettings()
    assert len(warnings) == 1 and ENV + name.upper() in warnings[0]


BAD_FILE = [
    ("enabled", 2), ("enabled", "perhaps"), ("min_tool_chars", True), ("min_tool_chars", 12.5),
    ("min_tool_chars", [1]), ("min_savings", True), ("min_savings", 1), ("max_risk", 3), ("prune_tail", True),
    ("exempt_tools", 5), ("exempt_tools", ["a", 2]), ("protect_patterns", ["["]), ("telemetry_log", 5),
    ("telemetry_log", ["a"]),
]


@pytest.mark.parametrize("name, value", BAD_FILE)
def test_bad_settings_values_become_defaults_with_a_warning(hermes_config, name, value):
    hermes_config(plugin_config(**{name: value}))
    settings, warnings = load_settings(env={})
    assert settings == TameruSettings()
    assert len(warnings) == 1 and f"plugins.entries.tameru.settings.{name}" in warnings[0]


def test_an_invalid_value_falls_through_to_the_next_layer(hermes_config):
    hermes_config(plugin_config(min_tool_chars=500))
    settings, warnings = load_settings(env={ENV + "MIN_TOOL_CHARS": "lots"})
    assert settings.min_tool_chars == 500
    assert len(warnings) == 1 and ENV + "MIN_TOOL_CHARS" in warnings[0]


def test_one_bad_value_does_not_spoil_the_others(hermes_config):
    hermes_config(plugin_config(min_savings="lots", brief_chars=700))
    settings, warnings = load_settings(env={ENV + "LEDGER": "1"})
    assert (settings.min_savings, settings.brief_chars, settings.ledger) == (0.30, 700, True)
    assert len(warnings) == 1


def test_long_bad_values_are_truncated_in_warnings(hermes_config):
    hermes_config(plugin_config(max_risk="x" * 500))
    (warning,) = load_settings(env={})[1]
    assert len(warning) < 200


def test_unknown_settings_keys_warn_in_both_sections(hermes_config):
    entry = {"settings": {"min_tool_char": 5, "ledger": True}, "config": {"bogus": 1}}
    hermes_config({"plugins": {"entries": {"tameru": entry}}})
    settings, warnings = load_settings(env={})
    assert settings.ledger is True
    assert len(warnings) == 2
    assert any("settings.min_tool_char" in w for w in warnings) and any("config.bogus" in w for w in warnings)


def test_a_settings_section_that_is_not_a_mapping_warns(hermes_config):
    hermes_config({"plugins": {"entries": {"tameru": {"settings": "oops", "config": ["x"]}}}})
    settings, warnings = load_settings(env={})
    assert settings == TameruSettings()
    assert len(warnings) == 2


def test_null_values_in_the_settings_file_mean_unset(hermes_config):
    hermes_config(plugin_config(min_tool_chars=None, telemetry_log=None))
    assert load_settings(env={}) == (TameruSettings(), [])


@pytest.mark.parametrize("merged", [
    None, {}, {"plugins": None}, {"plugins": []}, {"plugins": {"entries": []}},
    {"plugins": {"entries": {"tameru": None}}}, {"plugins": {"entries": {"other": {"settings": {"ledger": True}}}}},
])
def test_unrelated_or_malformed_plugin_sections_are_ignored(hermes_config, merged):
    hermes_config(merged)
    assert load_settings(env={}) == (TameruSettings(), [])


def test_load_settings_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("config exploded")

    monkeypatch.setattr(cfg, "read_hermes_config", broken)
    settings, warnings = load_settings(FakeCtx({"ledger": True}), env={})
    assert settings == TameruSettings()
    assert len(warnings) == 1 and "config exploded" in warnings[0]


# ---- read_hermes_config --------------------------------------------------------------------------
def install_fake_hermes_cli(monkeypatch, load, read_raw):
    package, module = types.ModuleType("hermes_cli"), types.ModuleType("hermes_cli.config")
    package.__path__ = []
    module.load_config_readonly, module.read_raw_config_readonly = load, read_raw
    package.config = module
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", module)


def test_read_hermes_config_returns_the_readonly_loaders_objects_untouched(monkeypatch):
    merged = {"compression": {"threshold": 0.5}, **plugin_config(ledger=True)}
    raw = plugin_config(ledger=True)
    snapshot = copy.deepcopy((merged, raw))
    install_fake_hermes_cli(monkeypatch, lambda: merged, lambda: raw)
    got_merged, got_raw = read_hermes_config()
    assert got_merged is merged and got_raw is raw
    assert load_settings(env={})[0].ledger is True
    compression_kwargs(got_merged, got_raw, TameruSettings(), {"threshold_percent"})
    assert (merged, raw) == snapshot, "Hermes' cached config objects must never be mutated"


def _boom():
    raise RuntimeError("boom")


@pytest.mark.parametrize("load, read_raw, expected", [
    (_boom, _boom, ({}, {})),
    (_boom, lambda: {"a": 1}, ({}, {"a": 1})),
    (lambda: {"a": 1}, _boom, ({"a": 1}, {})),
    (lambda: None, lambda: ["x"], ({}, {})),
])
def test_read_hermes_config_degrades_per_loader(monkeypatch, load, read_raw, expected):
    install_fake_hermes_cli(monkeypatch, load, read_raw)
    assert read_hermes_config() == expected


@pytest.fixture
def no_hermes_cli(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli", None)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", None)


YAML_CONFIG = """\
compression:
  threshold: 0.7
plugins:
  entries:
    tameru:
      settings:
        min_tool_chars: 321
"""


def test_without_hermes_cli_config_yaml_is_read_directly(no_hermes_cli, hermes_home):
    pytest.importorskip("yaml")
    (hermes_home / "config.yaml").write_text(YAML_CONFIG, encoding="utf-8")
    merged, raw = read_hermes_config()
    assert merged == raw == {
        "compression": {"threshold": 0.7}, "plugins": {"entries": {"tameru": {"settings": {"min_tool_chars": 321}}}},
    }
    assert load_settings(env={})[0].min_tool_chars == 321


@pytest.mark.parametrize("text", [None, "", "just a string", "- a\n- b\n", "key: [unclosed\n"])
def test_without_hermes_cli_an_unusable_config_yaml_is_empty(no_hermes_cli, hermes_home, text):
    if text is not None:
        (hermes_home / "config.yaml").write_text(text, encoding="utf-8")
    assert read_hermes_config() == ({}, {})


def test_without_hermes_cli_or_yaml_nothing_is_read(no_hermes_cli, hermes_home, monkeypatch):
    (hermes_home / "config.yaml").write_text(YAML_CONFIG, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert read_hermes_config() == ({}, {})
    assert load_settings(env={}) == (TameruSettings(), [])


@pytest.mark.parametrize("missing", [False, True])
def test_nothing_raises_with_an_empty_hermes_home(hermes_home, monkeypatch, init_params, missing):
    if missing:
        monkeypatch.setenv("HERMES_HOME", str(hermes_home / "does-not-exist"))
    settings, warnings = load_settings(env={})
    assert (settings, warnings) == (TameruSettings(), [])
    merged, raw = read_hermes_config()
    assert isinstance(merged, dict) and isinstance(raw, dict)
    kwargs, warnings = compression_kwargs(merged, raw, settings, init_params)
    assert warnings == []
    assert kwargs["proactive_prune_tokens"] == 48_000 and kwargs["proactive_prune_min_result_chars"] == 2000


@pytest.mark.real_hermes
def test_real_loaders_separate_merged_defaults_from_the_raw_user_file(hermes_home, init_params):
    (hermes_home / "config.yaml").write_text(YAML_CONFIG, encoding="utf-8")
    merged, raw = read_hermes_config()
    assert merged["compression"]["threshold"] == raw["compression"]["threshold"] == 0.7
    assert merged["compression"]["proactive_prune_tokens"] == 0, "merged always carries Hermes' defaults"
    assert "proactive_prune_tokens" not in raw["compression"]
    settings, warnings = load_settings(env={})
    assert (settings.min_tool_chars, warnings) == (321, [])
    kwargs, _ = compression_kwargs(merged, raw, settings, init_params)
    assert kwargs["threshold_percent"] == 0.7 and kwargs["proactive_prune_tokens"] == 48_000

    (hermes_home / "config.yaml").write_text("compression:\n  proactive_prune_tokens: 0\n", encoding="utf-8")
    merged, raw = read_hermes_config()
    assert compression_kwargs(merged, raw, settings, init_params)[0]["proactive_prune_tokens"] == 0


# ---- compression_kwargs: the mapping table --------------------------------------------------------
EXPECTED_MAP = {
    "threshold": "threshold_percent",
    "protect_first_n": "protect_first_n",
    "protect_last_n": "protect_last_n",
    "target_ratio": "summary_target_ratio",
    "abort_on_summary_failure": "abort_on_summary_failure",
    "threshold_tokens": "threshold_tokens_cap",
    "proactive_prune_tokens": "proactive_prune_tokens",
    "proactive_prune_min_result_chars": "proactive_prune_min_result_chars",
    "proactive_prune_min_reclaim_tokens": "proactive_prune_min_reclaim_tokens",
    "min_tail_user_messages": "min_tail_user_messages",
    "tail_mode": "tail_mode",
    "model_thresholds": "model_thresholds",
}


def test_the_table_maps_the_documented_keys():
    assert {e.config_key: e.kwarg for e in COMPRESSION_TABLE} == EXPECTED_MAP
    assert len(COMPRESSION_TABLE) == len(EXPECTED_MAP)


def test_every_mapped_kwarg_is_a_context_compressor_init_parameter(init_params, compressor_cls):
    """Real mode: the real signature (no ``**kwargs``); stub mode: the strict stub's."""
    assert not any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(compressor_cls.__init__).parameters.values()
    )
    assert {e.kwarg for e in COMPRESSION_TABLE} <= init_params


def test_mapped_kwargs_construct_a_compressor(compressor_cls, init_params):
    section = {
        "threshold": 0.6, "protect_first_n": 2, "protect_last_n": 10, "target_ratio": 0.3,
        "abort_on_summary_failure": True, "threshold_tokens": 90_000, "proactive_prune_tokens": 1000,
        "proactive_prune_min_result_chars": 400, "proactive_prune_min_reclaim_tokens": 100,
        "min_tail_user_messages": 2, "tail_mode": "legacy", "model_thresholds": {"gpt": 0.9},
    }
    kwargs, warnings = compression_kwargs({"compression": section}, {"compression": section}, TameruSettings(), init_params)
    assert warnings == [] and len(kwargs) == len(EXPECTED_MAP)
    compressor = compressor_cls(model="pending", **kwargs)
    assert compressor.protect_last_n == 10 and compressor.tail_mode == "legacy"


FULL_SECTION = {
    "threshold": 0.65, "protect_first_n": 2, "protect_last_n": 12, "target_ratio": 0.25,
    "abort_on_summary_failure": True, "threshold_tokens": 120_000, "proactive_prune_tokens": 30_000,
    "proactive_prune_min_result_chars": 3000, "proactive_prune_min_reclaim_tokens": 2048,
    "min_tail_user_messages": 3, "tail_mode": "legacy", "model_thresholds": {"gpt-5": 0.8, "claude": 0.6},
}


def kwargs_for(section, raw=None, params=None):
    """``compression_kwargs`` over ``section``; ``raw`` defaults to the same section (everything explicit)."""
    merged = {"compression": section}
    return compression_kwargs(merged, merged if raw is None else raw, TameruSettings(), params or set(EXPECTED_MAP.values()))


def test_a_full_section_maps_every_key():
    kwargs, warnings = kwargs_for(FULL_SECTION)
    assert warnings == []
    assert kwargs == {
        "threshold_percent": 0.65, "protect_first_n": 2, "protect_last_n": 12, "summary_target_ratio": 0.25,
        "abort_on_summary_failure": True, "threshold_tokens_cap": 120_000, "proactive_prune_tokens": 30_000,
        "proactive_prune_min_result_chars": 3000, "proactive_prune_min_reclaim_tokens": 2048,
        "min_tail_user_messages": 3, "tail_mode": "legacy", "model_thresholds": {"gpt-5": 0.8, "claude": 0.6},
    }
    assert list(kwargs) == [e.kwarg for e in COMPRESSION_TABLE], "table order, deterministic"


def test_values_are_coerced_the_way_hermes_does():
    section = {
        "threshold": "0.55", "protect_first_n": "-3", "protect_last_n": "7", "target_ratio": 1,
        "abort_on_summary_failure": "Yes", "threshold_tokens": "50000", "proactive_prune_tokens": -10,
        "proactive_prune_min_result_chars": "150", "proactive_prune_min_reclaim_tokens": -1,
        "min_tail_user_messages": 0, "tail_mode": "  LEGACY ",
        "model_thresholds": {"a": 1, "b": 0.5, "c": True, "d": "0.9", 7: 0.3, "e": None},
    }
    kwargs, warnings = kwargs_for(section)
    assert warnings == []
    assert kwargs == {
        "threshold_percent": 0.55, "protect_first_n": 0, "protect_last_n": 7, "summary_target_ratio": 1.0,
        "abort_on_summary_failure": True, "threshold_tokens_cap": 50_000, "proactive_prune_tokens": 0,
        "proactive_prune_min_result_chars": 150, "proactive_prune_min_reclaim_tokens": 0,
        "min_tail_user_messages": 1, "tail_mode": "legacy",
        "model_thresholds": {"a": 1.0, "b": 0.5, "7": 0.3},
    }


@pytest.mark.parametrize("flag, expected", [
    (True, True), (False, False), ("true", True), ("1", True), ("YES", True), ("false", False), (0, False), (None, False),
])
def test_abort_on_summary_failure_uses_hermes_flag_truthiness(flag, expected):
    kwargs, _ = kwargs_for({"abort_on_summary_failure": flag})
    assert kwargs["abort_on_summary_failure"] is expected


@pytest.mark.parametrize("raw, expected", [(None, None), (0, None), (-5, None), ("0", None), (75_000, 75_000), ("75000", 75_000)])
def test_threshold_tokens_is_a_positive_int_or_none(raw, expected):
    kwargs, warnings = kwargs_for({"threshold_tokens": raw})
    assert kwargs["threshold_tokens_cap"] == expected
    assert warnings == []


@pytest.mark.parametrize("raw, expected", [(5.7, 5), (True, 1), ("lots", None), ([1], None), (float("inf"), None)])
def test_threshold_tokens_coerces_like_hermes_int(raw, expected):
    kwargs, warnings = kwargs_for({"threshold_tokens": raw})
    assert kwargs["threshold_tokens_cap"] == expected
    assert warnings == []


UNUSABLE = {
    "threshold": "abc", "protect_first_n": True, "protect_last_n": 1.5, "target_ratio": [0.2],
    "proactive_prune_tokens": "x", "proactive_prune_min_result_chars": None,
    "proactive_prune_min_reclaim_tokens": {}, "min_tail_user_messages": "many", "tail_mode": 5,
    "model_thresholds": [1, 2],
}


@pytest.mark.parametrize("key, value", sorted(UNUSABLE.items()))
def test_an_unusable_value_is_dropped_with_a_warning(key, value):
    section = {**FULL_SECTION, key: value}
    kwargs, warnings = kwargs_for(section)
    assert EXPECTED_MAP[key] not in kwargs
    assert len(warnings) == 1 and f"compression.{key}" in warnings[0]
    assert len(kwargs) == len(EXPECTED_MAP) - 1, "the other keys are unaffected"


def test_an_overflowing_model_threshold_is_dropped_with_a_warning():
    kwargs, warnings = kwargs_for({"threshold": 0.6, "model_thresholds": {"m": 10**400}})
    assert kwargs["threshold_percent"] == 0.6 and "model_thresholds" not in kwargs
    assert len(warnings) == 1 and "compression.model_thresholds" in warnings[0]


def test_kwargs_missing_from_init_params_are_dropped_with_a_warning():
    section = {"threshold": 0.7, "tail_mode": "legacy", "proactive_prune_tokens": 5}
    params = {"threshold_percent", "proactive_prune_tokens", "proactive_prune_min_result_chars"}
    kwargs, warnings = kwargs_for(section, params=params)
    assert kwargs == {"threshold_percent": 0.7, "proactive_prune_tokens": 5, "proactive_prune_min_result_chars": 2000}
    assert len(warnings) == 1 and "compression.tail_mode" in warnings[0] and "'tail_mode'" in warnings[0]


def test_keys_absent_from_the_config_are_left_to_the_compressor():
    kwargs, warnings = compression_kwargs({"compression": {"threshold": 0.7}}, {}, TameruSettings(), set(EXPECTED_MAP.values()))
    assert kwargs == {"threshold_percent": 0.7, "proactive_prune_tokens": 48_000, "proactive_prune_min_result_chars": 2000}
    assert warnings == []


# ---- compression_kwargs: explicit-set detection ---------------------------------------------------
HERMES_DEFAULTS = {"proactive_prune_tokens": 0, "proactive_prune_min_result_chars": 8000, "threshold": 0.5}


def test_unset_proactive_keys_take_the_plugin_defaults_whatever_merged_holds():
    """Hermes' merged config always has the keys (with its own defaults); only raw says if the user set them."""
    kwargs, _ = compression_kwargs({"compression": dict(HERMES_DEFAULTS)}, {}, TameruSettings(), set(EXPECTED_MAP.values()))
    assert kwargs["proactive_prune_tokens"] == 48_000
    assert kwargs["proactive_prune_min_result_chars"] == 2000
    assert kwargs["threshold_percent"] == 0.5, "other keys follow merged"


@pytest.mark.parametrize("raw", [None, {}, {"compression": None}, {"compression": {"threshold": 0.7}}, {"other": {}}])
def test_a_raw_config_without_the_keys_means_not_explicit(raw):
    kwargs, _ = compression_kwargs({"compression": dict(HERMES_DEFAULTS)}, raw, TameruSettings(), set(EXPECTED_MAP.values()))
    assert kwargs["proactive_prune_tokens"] == 48_000


def test_each_proactive_key_is_detected_on_its_own():
    merged = {"compression": {"proactive_prune_tokens": 0, "proactive_prune_min_result_chars": 9000}}
    params = set(EXPECTED_MAP.values())
    kwargs, _ = compression_kwargs(merged, {"compression": {"proactive_prune_min_result_chars": 9000}}, TameruSettings(), params)
    assert (kwargs["proactive_prune_tokens"], kwargs["proactive_prune_min_result_chars"]) == (48_000, 9000)
    kwargs, _ = compression_kwargs(merged, {"compression": {"proactive_prune_tokens": 0}}, TameruSettings(), params)
    assert (kwargs["proactive_prune_tokens"], kwargs["proactive_prune_min_result_chars"]) == (0, 2000)


def test_an_explicit_zero_is_respected_as_the_users_opt_out():
    merged = {"compression": {"proactive_prune_tokens": 0}}
    kwargs, _ = compression_kwargs(merged, merged, TameruSettings(), {"proactive_prune_tokens"})
    assert kwargs == {"proactive_prune_tokens": 0}


def test_the_plugin_defaults_come_from_the_settings():
    settings = TameruSettings(default_proactive_prune_tokens=1000, default_proactive_prune_min_result_chars=500)
    kwargs, _ = compression_kwargs({}, {}, settings, set(EXPECTED_MAP.values()))
    assert kwargs == {"proactive_prune_tokens": 1000, "proactive_prune_min_result_chars": 500}


def test_a_disabled_plugin_leaves_proactive_pruning_to_hermes():
    merged = {"compression": dict(HERMES_DEFAULTS)}
    kwargs, _ = compression_kwargs(merged, {}, TameruSettings(enabled=False), set(EXPECTED_MAP.values()))
    assert kwargs["proactive_prune_tokens"] == 0 and kwargs["proactive_prune_min_result_chars"] == 8000
    assert compression_kwargs({}, {}, TameruSettings(enabled=False), set(EXPECTED_MAP.values())) == ({}, [])


def test_a_bad_explicit_proactive_value_is_dropped_not_defaulted():
    merged = {"compression": {"proactive_prune_tokens": "lots"}}
    params = {"proactive_prune_tokens", "proactive_prune_min_result_chars"}
    kwargs, warnings = compression_kwargs(merged, merged, TameruSettings(), params)
    assert kwargs == {"proactive_prune_min_result_chars": 2000}
    assert len(warnings) == 1 and "compression.proactive_prune_tokens" in warnings[0]


# ---- compression_kwargs never raises -------------------------------------------------------------
GARBAGE = [
    None, "text", 5, [], ["compression"], {"compression": None}, {"compression": "x"}, {"compression": []},
    {"compression": {"threshold": object(), "tail_mode": object(), "model_thresholds": object()}},
]


@pytest.mark.parametrize("merged", GARBAGE)
@pytest.mark.parametrize("raw", GARBAGE)
def test_garbage_configs_never_raise(merged, raw):
    kwargs, warnings = compression_kwargs(merged, raw, TameruSettings(), set(EXPECTED_MAP.values()))
    assert isinstance(kwargs, dict) and isinstance(warnings, list)


def test_garbage_settings_or_init_params_never_raise():
    assert compression_kwargs({"compression": {}}, {}, None, set(EXPECTED_MAP.values()))[0] == {}
    kwargs, _ = compression_kwargs({"compression": {"threshold": 0.7}}, {}, TameruSettings(), set())
    assert kwargs == {}


# ---- real Hermes parity ---------------------------------------------------------------------------
HERMES_ATTR = {
    "threshold_percent": "threshold", "protect_first_n": "protect_first", "protect_last_n": "protect_last",
    "summary_target_ratio": "target_ratio", "abort_on_summary_failure": "abort_on_summary_failure",
    "threshold_tokens_cap": "threshold_tokens", "proactive_prune_tokens": "proactive_prune_tokens",
    "proactive_prune_min_result_chars": "proactive_prune_min_chars",
    "proactive_prune_min_reclaim_tokens": "proactive_prune_min_reclaim", "min_tail_user_messages": "min_tail_users",
    "tail_mode": "tail_mode", "model_thresholds": "model_thresholds",
}

PARITY_OVERRIDES = [
    {},
    {"threshold": 0.8, "protect_last_n": 40, "target_ratio": 0.35, "tail_mode": "legacy"},
    {"threshold": "0.65", "protect_last_n": "9", "target_ratio": "0.3", "protect_first_n": "2"},
    {"protect_first_n": -4, "min_tail_user_messages": 0, "proactive_prune_tokens": -10, "proactive_prune_min_reclaim_tokens": -1},
    {"threshold_tokens": 0}, {"threshold_tokens": -3}, {"threshold_tokens": "90000"}, {"threshold_tokens": None},
    {"threshold_tokens": 5.7}, {"threshold_tokens": True}, {"threshold_tokens": "lots"},
    {"proactive_prune_tokens": 48_000, "proactive_prune_min_result_chars": 2000, "proactive_prune_min_reclaim_tokens": 0},
    {"proactive_prune_min_result_chars": "1500", "proactive_prune_tokens": "20000"},
    {"tail_mode": "  LeGaCy  "}, {"tail_mode": "bogus"},
    {"model_thresholds": {"gpt-5": 0.8, "claude": 1, "flag": True, "text": "0.9", "none": None}},
    {"abort_on_summary_failure": True}, {"abort_on_summary_failure": "true"}, {"abort_on_summary_failure": "yes"},
    {"abort_on_summary_failure": "1"}, {"abort_on_summary_failure": "false"}, {"abort_on_summary_failure": 0},
]


@pytest.mark.real_hermes
@pytest.mark.parametrize("override", PARITY_OVERRIDES, ids=[str(i) for i in range(len(PARITY_OVERRIDES))])
def test_the_mapping_mirrors_hermes_parse_compression_config(override, init_params):
    from agent.agent_init import _parse_compression_config
    from hermes_cli.config import DEFAULT_CONFIG

    section = {**copy.deepcopy(DEFAULT_CONFIG["compression"]), **override}
    parsed = _parse_compression_config(
        types.SimpleNamespace(model="plain-model", provider="", api_mode="chat_completions"), {"compression": section},
    )
    kwargs, warnings = compression_kwargs({"compression": section}, {"compression": section}, TameruSettings(), init_params)
    assert warnings == []
    assert set(kwargs) == set(HERMES_ATTR)
    for kwarg, attr in HERMES_ATTR.items():
        assert kwargs[kwarg] == getattr(parsed, attr), (kwarg, override)


@pytest.mark.real_hermes
def test_hermes_default_config_equals_the_compressor_defaults(init_params, compressor_cls):
    """An unconfigured Hermes yields exactly the stock constructor values (nothing shifts when mirrored)."""
    from hermes_cli.config import DEFAULT_CONFIG

    section = {"compression": DEFAULT_CONFIG["compression"]}
    kwargs, warnings = compression_kwargs(section, section, TameruSettings(), init_params)
    signature = inspect.signature(compressor_cls.__init__).parameters
    assert warnings == []
    for kwarg, value in kwargs.items():
        assert value == ({} if kwarg == "model_thresholds" else signature[kwarg].default), kwarg


@pytest.mark.real_hermes
def test_the_table_covers_every_config_driven_kwarg_hermes_passes_to_the_compressor():
    """Drift guard: the ``ContextCompressor(...)`` call in ``_build_context_engine`` names the kwargs we mirror."""
    import re

    from agent import agent_init

    source = inspect.getsource(agent_init._build_context_engine)
    call = source[source.index("ContextCompressor("):]
    forwarded = set(re.findall(r"\b([a-z_]+)=", call[: call.index("custom_providers=")]))
    ours = {e.kwarg for e in COMPRESSION_TABLE}
    config_driven = forwarded - {"model", "summary_model_override", "quiet_mode", "base_url", "api_key",
                                 "config_context_length", "provider", "api_mode", "max_tokens"}
    assert config_driven == ours
