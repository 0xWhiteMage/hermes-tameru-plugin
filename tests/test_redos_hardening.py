"""Bound untrusted spill detection and configured protection regexes."""
from __future__ import annotations

import subprocess
import sys

import pytest

from conftest import REPO_ROOT
from hermes_tameru_plugin import config as cfg
from hermes_tameru_plugin.hermes_compat import is_persisted_block


def test_unterminated_untrusted_notice_finishes_fast():
    code = """import importlib.util
import sys
spec = importlib.util.spec_from_file_location('compat', sys.argv[1])
compat = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = compat
spec.loader.exec_module(compat)
for tail in ('a' * 100_000, ' ' * 100_000, 'a' * 100_000 + '\\n'):
    assert not compat.is_persisted_block('<untrusted_tool_result>\\n' + tail)
"""
    result = subprocess.run(
        [sys.executable, '-I', '-c', code, str(REPO_ROOT / 'hermes_tameru_plugin/hermes_compat.py')],
        capture_output=True, text=True, timeout=2,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('notice_lines', range(5))
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_persisted_wrapper_notice_variants(notice_lines, newline):
    notice = ('notice with trailing blanks  ' + newline) * notice_lines
    head = '<UNTRUSTED_TOOL_RESULT source="web_extract">  ' + newline + notice + newline
    spill = '<persisted-output>original saved</persisted-output>'
    assert is_persisted_block(head + spill)
    assert is_persisted_block(head + head + spill)
    assert not is_persisted_block(head + 'ordinary content')


@pytest.mark.parametrize('raw', [
    ['(a+)+$'], ['(?:a|aa)+$'], ['a' * 201],
    [f'pattern{i}' for i in range(33)],
    ','.join(f'pattern{i}' for i in range(33)),
    [r'(a)?(?(1)a*|b)*c'], ['.*.*.*.*x'],
    [r'(a)\1'], [r'(?P<name>a)(?P=name)'],
    [r'(?=(a)\1)'], [r'(?>a*x)'], [r'a{1,}x'], [r'a+?x'],
    [r'a*+x'], [r'a*x'], [r'a+x'], [r'(?:(a)?(?(1)b|c))'],
    [r'.{0,4294967294}x'], [r'.{0,999}.{0,999}.{0,999}x'],
    [r'.{0,16}.{0,16}x'], [r'.{0,33}x'], [r'.?' * 9 + 'x'],
    [r'(?:.{0,11}a|.{0,11}b)x'],
    [r'(?:(?=a)a)?' * 8 + 'b'], [r'(?:aa)?' * 8 + 'b'],
])
def test_unsafe_protect_patterns_warn_and_fall_back(monkeypatch, raw):
    config = {'plugins': {'entries': {'tameru': {'settings': {'protect_patterns': raw}}}}}
    monkeypatch.setattr(cfg, 'read_hermes_config', lambda: (config, config))
    settings, warnings = cfg.load_settings(env={})
    assert settings.protect_patterns == ()
    assert len(warnings) == 1
    assert 'plugins.entries.tameru.settings.protect_patterns' in warnings[0]
    assert warnings[0].endswith('; ignored')


def test_unsafe_protect_patterns_env_drops_only_invalid_entry(monkeypatch):
    config = {'plugins': {'entries': {'tameru': {'settings': {'protect_patterns': [r'\bERROR\b']}}}}}
    monkeypatch.setattr(cfg, 'read_hermes_config', lambda: (config, config))
    settings, warnings = cfg.load_settings(env={cfg.ENV_PREFIX + 'PROTECT_PATTERNS': 'TODO,(a+)+$'})
    assert settings.protect_patterns == ('TODO',)
    assert len(warnings) == 1
    assert '(a+)+$' in warnings[0]


def test_mixed_protect_patterns_keep_valid_entries(monkeypatch):
    patterns = ['TODO', '(unclosed', r'\bERROR\b', r'.{0,4294967294}x', '^Traceback']
    config = {'plugins': {'entries': {'tameru': {'settings': {'protect_patterns': patterns}}}}}
    monkeypatch.setattr(cfg, 'read_hermes_config', lambda: (config, config))
    settings, warnings = cfg.load_settings(env={})
    assert settings.protect_patterns == ('TODO', r'\bERROR\b', '^Traceback')
    assert len(warnings) == 2
    assert '(unclosed' in warnings[0]
    assert '.{0,4294967294}x' in warnings[1]


@pytest.mark.parametrize('raw, expected', [
    ([], ()), ('', ()), (' foo,bar,foo ', ('foo', 'bar')),
    ([r'\bID-\d{12}\b', r'(foo|bar)', r'error\s{1,8}\w{1,8}'],
     (r'\bID-\d{12}\b', r'(foo|bar)', r'error\s{1,8}\w{1,8}')),
    ([r'[A-Z]{2,8}-\d{1,6}', r'\d{12}', r'.{0,32}x', r'.?' * 7 + 'x'],
     (r'[A-Z]{2,8}-\d{1,6}', r'\d{12}', r'.{0,32}x', r'.?' * 7 + 'x')),
    (['a' * 200], ('a' * 200,)),
    ([f'pattern{i}' for i in range(32)], tuple(f'pattern{i}' for i in range(32))),
    (','.join(f'pattern{i}' for i in range(32)) + ',',
     tuple(f'pattern{i}' for i in range(32))),
    ([' ', 'TODO', 'TODO', ' ' * 201], ('TODO',)),
    (['  ' + 'a' * 200 + '  '], ('a' * 200,)),
    (['TODO', r'\bERROR\b', '^Traceback'], ('TODO', r'\bERROR\b', '^Traceback')),
    (['TODO', r'\bERROR\b', '^Traceback', r'\d{12}', r'[A-Z]{2,8}-\d{1,6}'],
     ('TODO', r'\bERROR\b', '^Traceback', r'\d{12}', r'[A-Z]{2,8}-\d{1,6}')),
    ([r'\*\+\{1,\}', r'[*+]', r'\\1', r'\(\?\('],
     (r'\*\+\{1,\}', r'[*+]', r'\\1', r'\(\?\(')),
])
def test_valid_protect_patterns_remain_accepted(raw, expected):
    assert cfg._parse_patterns(raw) == expected


def test_protect_patterns_total_budget_keeps_earlier_entries():
    patterns = ['a?' * 7 + chr(0x100 + i) for i in range(32)]
    warnings = []
    assert cfg._parse_patterns(patterns, warnings.append) == tuple(patterns[:2])
    assert len(warnings) == 30
    for pattern, warning in zip(patterns[2:], warnings):
        assert pattern in warning


def test_reviewer_max_cost_set_is_rejected():
    warnings = []
    with pytest.raises(ValueError, match='no valid protect patterns'):
        cfg._parse_patterns(['a?' * 8 + chr(0x100 + i) for i in range(32)], warnings.append)
    assert len(warnings) == 32


@pytest.mark.parametrize('raw, expected, merge_warnings', [
    (r'TODO,\d{1,12},[a,b],(?:foo,bar),a\,b,',
     ('TODO', r'\d{1,12}', '[a,b]', '(?:foo,bar)', r'a\,b'), 2),
    (r'[(){} ,],foo,bar', ('[(){} ,]', 'foo', 'bar'), 1),
    (r'[]a,b],TODO', ('[]a,b]', 'TODO'), 1),
    ('TODO,,TODO,', ('TODO',), 0),
    ('ERROR{,TODO', ('ERROR{', 'TODO'), 0),
    ('a(?# [),TODO', ('a(?# [)', 'TODO'), 0),
    ('a{1,2,b', ('a{1', '2', 'b'), 0),
    (r'\d{1,8},[A-Z]{2,8}-\d{1,6},(?:a|b)',
     (r'\d{1,8}', r'[A-Z]{2,8}-\d{1,6}', '(?:a|b)'), 0),
    (r'a{2},a{,3},TODO', (r'a{2}', r'a{,3}', 'TODO'), 0),
    (r'a(?#\) [),TODO', (r'a(?#\) [)', 'TODO'), 0),
    ('(?x)^a$ # [,TODO', ('(?x)^a$ # [', 'TODO'), 0),
    (r'\{1,2}', (r'\{1', '2}'), 0),
])
def test_env_patterns_split_by_compilation(monkeypatch, raw, expected, merge_warnings):
    monkeypatch.setattr(cfg, 'read_hermes_config', lambda: ({}, {}))
    settings, warnings = cfg.load_settings(env={cfg.ENV_PREFIX + 'PROTECT_PATTERNS': raw})
    assert settings.protect_patterns == expected
    assert len(warnings) == merge_warnings
    for warning in warnings:
        assert 'merged comma-containing regex' in warning
        assert any(repr(pattern) in warning for pattern in expected)


def test_unbounded_quantifier_commas_remain_inside_rejected_entries():
    warnings = []
    assert cfg._parse_patterns('a{1,},a{,},TODO', warnings.append) == ('TODO',)
    assert len(warnings) == 2
    assert 'a{1,}' in warnings[0]
    assert 'a{,}' in warnings[1]


def test_all_invalid_env_patterns_fall_back_to_yaml(monkeypatch):
    config = {'plugins': {'entries': {'tameru': {'settings': {'protect_patterns': ['TODO']}}}}}
    monkeypatch.setattr(cfg, 'read_hermes_config', lambda: (config, config))
    settings, warnings = cfg.load_settings(env={cfg.ENV_PREFIX + 'PROTECT_PATTERNS': '(unclosed'})
    assert settings.protect_patterns == ('TODO',)
    assert len(warnings) == 1
    assert '(unclosed' in warnings[0]
