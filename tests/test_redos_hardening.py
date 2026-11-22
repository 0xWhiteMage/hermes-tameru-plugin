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
])
def test_unsafe_protect_patterns_warn_and_fall_back(monkeypatch, raw):
    config = {'plugins': {'entries': {'tameru': {'settings': {'protect_patterns': raw}}}}}
    monkeypatch.setattr(cfg, 'read_hermes_config', lambda: (config, config))
    settings, warnings = cfg.load_settings(env={})
    assert settings.protect_patterns == ()
    assert len(warnings) == 1
    assert 'plugins.entries.tameru.settings.protect_patterns' in warnings[0]
    assert warnings[0].endswith('; ignored')


def test_unsafe_protect_patterns_env_falls_back_to_valid_config(monkeypatch):
    config = {'plugins': {'entries': {'tameru': {'settings': {'protect_patterns': [r'\bERROR\b']}}}}}
    monkeypatch.setattr(cfg, 'read_hermes_config', lambda: (config, config))
    settings, warnings = cfg.load_settings(env={cfg.ENV_PREFIX + 'PROTECT_PATTERNS': '(a+)+$'})
    assert settings.protect_patterns == (r'\bERROR\b',)
    assert len(warnings) == 1


@pytest.mark.parametrize('raw, expected', [
    ([], ()), ('', ()), (' foo,bar,foo ', ('foo', 'bar')),
    ([r'\bID-\d{12}\b', r'(foo|bar)', r'error\s{1,8}\w{1,32}'],
     (r'\bID-\d{12}\b', r'(foo|bar)', r'error\s{1,8}\w{1,32}')),
    (['a' * 200], ('a' * 200,)),
    ([f'pattern{i}' for i in range(32)], tuple(f'pattern{i}' for i in range(32))),
    (','.join(f'pattern{i}' for i in range(32)) + ',',
     tuple(f'pattern{i}' for i in range(32))),
    ([' ', 'TODO', 'TODO', ' ' * 201], ('TODO',)),
    (['  ' + 'a' * 200 + '  '], ('a' * 200,)),
    (['TODO', r'\bERROR\b', '^Traceback'], ('TODO', r'\bERROR\b', '^Traceback')),
    ([r'\*\+\{1,\}', r'[*+]', r'\\1', r'\(\?\('],
     (r'\*\+\{1,\}', r'[*+]', r'\\1', r'\(\?\(')),
])
def test_valid_protect_patterns_remain_accepted(raw, expected):
    assert cfg._parse_patterns(raw) == expected
