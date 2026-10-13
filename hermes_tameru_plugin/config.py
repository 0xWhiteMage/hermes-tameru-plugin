"""Plugin configuration: ``TameruSettings``, its loader, and the ``compression.*`` mirror.

``load_settings`` resolves each field from, highest first: the ``TAMERU_HERMES_<FIELD>`` environment
variable, ``ctx.get_config`` (when the host context offers it), ``plugins.entries.tameru.settings``
and the legacy ``plugins.entries.tameru.config`` of the merged Hermes config, then the default.
A value that does not parse is reported in the warnings and falls through to the next layer.

Hermes hands a plugin engine none of the user's ``compression.*`` section (G11), so
``compression_kwargs`` copies it into ``ContextCompressor.__init__`` kwargs through ``COMPRESSION_TABLE``,
which mirrors ``agent/agent_init.py::_parse_compression_config``.

Import-safe: stdlib only, nothing happens at import time, and Hermes is reached only through guarded
imports inside functions. Nothing here raises, and Hermes' cached config objects are never mutated.
"""
from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

PLUGIN_ID = "tameru"
ENV_PREFIX = "TAMERU_HERMES_"
RISK_LEVELS = ("low", "medium", "high")
PRUNE_TAILS = ("tokens", "count")

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class TameruSettings:
    """Every plugin knob; the defaults are what runs when nothing is configured."""

    enabled: bool = True                       # kill switch -> pure stock ContextCompressor behaviour
    min_tool_chars: int = 400                  # shorter results take the parent path
    max_extract_chars: int = 6000              # incl. header/meta/marker bytes
    brief_chars: int = 1200                    # smallest brief row
    brief_share: float = 0.0                   # brief start size as a share of the result (0 = brief_chars)
    max_risk: str = "high"                     # accepted engine compression_risk ceiling: low|medium|high
    min_savings: float = 0.10                  # extract must be <= (1-min_savings)*len(inner)
    exempt_tools: tuple[str, ...] = ()         # added to the built-in exempt set
    protect_patterns: tuple[str, ...] = ()     # regexes -> compress_context(pin_patterns=...)
    expand_tool: bool = True
    expand_grep_timeout: float = 2.0             # seconds a backtracking-capable tameru_expand grep may run (killed after)
    expand_grep_line_chars: int = 20_000         # chars of each line a tameru_expand grep scans
    store_max_entries: int = 512
    store_max_chars: int = 32_000_000
    pass_char_budget: int = 4_000_000          # inner chars Tameru may process per pass; beyond -> parent_line
    retained_extract_budget_chars: int = 6000  # bodies (extract/brief) the newest rendered rows may keep in all (the floor)
    retained_extract_budget_ratio: float = 0.10  # ...at least this many chars per token of Hermes' compaction threshold; 0 = fixed
    retained_error_budget_chars: int = 8000    # "errors" rows (header + one line per error template) kept in all
    small_window_threshold_tokens: int = 54_400  # below this compaction threshold (a 64K window) the retained budgets shrink in proportion; 0 = off
    errors_row_chars: int = 1600               # cap on one errors row's exemplar lines; 0 = rows fall to a bare header
    prune_tail: str = "tokens"                 # proactive-prune tail: "tokens" (Hermes' tail_token_budget) | "count" (protect_last_n)
    supersession: bool = True
    ledger: bool = True
    telemetry_log: str = ""                    # JSONL path; "" = off
    default_proactive_prune_tokens: int = 48_000          # only if compression.proactive_prune_tokens is not set
    default_proactive_prune_ratio: float = 0.75           # ...then at least this share of Hermes' compaction threshold
    default_proactive_prune_min_result_chars: int = 8000  # Hermes' own default; only if compression.proactive_prune_min_result_chars is not set
    reprune_target_ratio: float = 0.30         # ...and commits only if it leaves the prompt at or below this share of the threshold (a compaction leaves ~0.2); 0 = no such gate
    prune_regrowth_ratio: float = 0.20         # after a prune commit, the next may fire once the prompt regrew by this share of Hermes' compaction threshold (plugin-supplied trigger only); 0 = Hermes' full trigger-sized runway


# ---- value parsers: raw -> value, ValueError when the raw value is unusable -----------------------
def _parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    raise ValueError("expected a boolean (true/false, yes/no, on/off, 1/0)")


def _parse_int(raw: Any) -> int:
    """Strict integer, like Hermes' ``_parse_config_int``: no bool, no fractional float, numeric str ok."""
    if isinstance(raw, bool):
        raise ValueError("expected an integer")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            pass
    raise ValueError("expected an integer")


def _parse_float(raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError("expected a number")
    try:
        value = float(raw.strip() if isinstance(raw, str) else raw)
    except (ValueError, OverflowError):
        raise ValueError("expected a number") from None
    if not math.isfinite(value):
        raise ValueError("expected a finite number")
    return value


def _min_int(minimum: int) -> Callable[[Any], int]:
    """Integer parser that rejects values below ``minimum``."""
    def parse(raw: Any) -> int:
        value = _parse_int(raw)
        if value < minimum:
            raise ValueError(f"expected an integer >= {minimum}")
        return value
    return parse


def _parse_savings(raw: Any) -> float:
    value = _parse_float(raw)
    if not 0.0 <= value < 1.0:
        raise ValueError("expected a number in [0, 1)")
    return value


def _parse_timeout(raw: Any) -> float:
    value = _parse_float(raw)
    if not 0.05 <= value <= 600:
        raise ValueError("expected seconds in [0.05, 600]")
    return value


def _parse_risk(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip().lower() in RISK_LEVELS:
        return raw.strip().lower()
    raise ValueError(f"expected one of {', '.join(RISK_LEVELS)}")


def _parse_prune_tail(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip().lower() in PRUNE_TAILS:
        return raw.strip().lower()
    raise ValueError(f"expected one of {', '.join(PRUNE_TAILS)}")


def _parse_str(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("expected a string")
    return raw.strip()


def _parse_str_tuple(raw: Any) -> tuple[str, ...]:
    """A list of strings, or one comma-separated string; blanks and repeats dropped, order kept."""
    if isinstance(raw, str):
        items: Any = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        raise ValueError("expected a list of strings or a comma-separated string")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("expected a list of strings")
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return tuple(out)


def _parse_patterns(raw: Any) -> tuple[str, ...]:
    """Like ``_parse_str_tuple``, and every entry must compile (an env value splits on commas)."""
    patterns = _parse_str_tuple(raw)
    for pattern in patterns:
        try:
            re.compile(pattern)
        except (re.error, OverflowError, RecursionError) as exc:
            raise ValueError(f"invalid regex {pattern!r}: {exc}") from None
    return patterns


_SETTING_PARSERS: dict[str, Callable[[Any], Any]] = {
    "enabled": _parse_bool,
    "min_tool_chars": _min_int(0),
    "max_extract_chars": _min_int(1),
    "brief_chars": _min_int(0),
    "max_risk": _parse_risk,
    "min_savings": _parse_savings,
    "brief_share": _parse_savings,
    "exempt_tools": _parse_str_tuple,
    "protect_patterns": _parse_patterns,
    "expand_tool": _parse_bool,
    "expand_grep_timeout": _parse_timeout,
    "expand_grep_line_chars": _min_int(1),
    "store_max_entries": _min_int(0),
    "store_max_chars": _min_int(0),
    "pass_char_budget": _min_int(0),
    "retained_extract_budget_chars": _min_int(0),
    "retained_extract_budget_ratio": _parse_savings,
    "retained_error_budget_chars": _min_int(0),
    "small_window_threshold_tokens": _min_int(0),
    "errors_row_chars": _min_int(0),
    "prune_tail": _parse_prune_tail,
    "supersession": _parse_bool,
    "ledger": _parse_bool,
    "telemetry_log": _parse_str,
    "default_proactive_prune_tokens": _min_int(0),
    "default_proactive_prune_ratio": _parse_savings,
    "default_proactive_prune_min_result_chars": _min_int(0),
    "prune_regrowth_ratio": _parse_savings,
    "reprune_target_ratio": _parse_savings,
}


# ---- Hermes config access ---------------------------------------------------------------------
def _dict_or_empty(read: Callable[[], Any]) -> dict:
    try:
        data = read()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml_config() -> dict:
    """``$HERMES_HOME/config.yaml`` through PyYAML; ``{}`` when either is missing or unreadable."""
    try:
        import yaml

        home = os.environ.get("HERMES_HOME", "").strip() or "~/.hermes"
        path = Path(os.path.expanduser(os.path.expandvars(home))) / "config.yaml"
        with open(path, encoding="utf-8-sig") as handle:
            data = yaml.safe_load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_hermes_config() -> tuple[dict, dict]:
    """``(merged, raw)``: Hermes' defaults-merged config and the user's file as written.

    Hermes' own cached objects are returned as they are: callers must treat them as read-only.
    Without ``hermes_cli`` both come from ``$HERMES_HOME/config.yaml`` (no defaults merged).
    ``({}, {})`` on any failure.
    """
    try:
        from hermes_cli.config import load_config_readonly, read_raw_config_readonly
    except Exception:
        data = _read_yaml_config()
        return data, data
    return _dict_or_empty(load_config_readonly), _dict_or_empty(read_raw_config_readonly)


# ---- load_settings ---------------------------------------------------------------------------
def _short(raw: Any) -> str:
    text = repr(raw)
    return text if len(text) <= 60 else text[:59] + "…"


def _file_layers(merged: Mapping[str, Any], warnings: list[str]) -> list[tuple[str, Mapping[str, Any]]]:
    """``(source label, mapping)`` for ``plugins.entries.tameru.settings`` then the legacy ``.config``.

    Warns about unknown keys and sections that are not mappings.
    """
    plugins = merged.get("plugins")
    entries = plugins.get("entries") if isinstance(plugins, Mapping) else None
    entry = entries.get(PLUGIN_ID) if isinstance(entries, Mapping) else None
    if not isinstance(entry, Mapping):
        return []
    layers: list[tuple[str, Mapping[str, Any]]] = []
    for section in ("settings", "config"):
        label = f"plugins.entries.{PLUGIN_ID}.{section}"
        value = entry.get(section)
        if isinstance(value, Mapping):
            layers.append((label, value))
            warnings.extend(f"{label}.{key}: unknown setting; ignored" for key in value if key not in _SETTING_PARSERS)
        elif value is not None:
            warnings.append(f"{label}: expected a mapping, got {type(value).__name__}; ignored")
    return layers


def _ctx_values(ctx: Any, warnings: list[str]) -> dict[str, Any]:
    """Every setting ``ctx.get_config`` knows; a ``LookupError`` is a miss, any other error ends the lookups."""
    get_config = getattr(ctx, "get_config", None)
    if not callable(get_config):
        return {}
    values: dict[str, Any] = {}
    for name in _SETTING_PARSERS:
        try:
            value = get_config(name)
        except LookupError:
            continue
        except Exception as exc:
            warnings.append(f"ctx.get_config failed ({exc!r}); ignored")
            break
        if value is not None:
            values[name] = value
    return values


def _load_settings(ctx: Any, env: Mapping[str, str], warnings: list[str]) -> TameruSettings:
    merged = read_hermes_config()[0]
    ctx_values = _ctx_values(ctx, warnings) if ctx is not None else {}
    file_layers = _file_layers(merged, warnings)
    chosen: dict[str, Any] = {}
    for name, parse in _SETTING_PARSERS.items():
        env_name = ENV_PREFIX + name.upper()
        candidates: list[tuple[str, Any]] = [(env_name, env[env_name])] if env_name in env else []
        if name in ctx_values:
            candidates.append((f"ctx.get_config({name!r})", ctx_values[name]))
        candidates += [
            (f"{label}.{name}", mapping[name]) for label, mapping in file_layers if mapping.get(name) is not None
        ]
        for label, raw in candidates:
            try:
                chosen[name] = parse(raw)
            except ValueError as exc:
                warnings.append(f"{label}={_short(raw)}: {exc}; ignored")
            else:
                break
    return TameruSettings(**chosen)


def load_settings(
    ctx: Any = None, env: Mapping[str, str] | None = None,
) -> tuple[TameruSettings, list[str]]:
    """Resolve the plugin settings and the warnings about what was ignored; never raises.

    ``env`` defaults to ``os.environ``; ``ctx`` is the plugin context handed to ``register`` (optional).
    """
    warnings: list[str] = []
    try:
        return _load_settings(ctx, os.environ if env is None else env, warnings), warnings
    except Exception as exc:
        warnings.append(f"settings could not be loaded ({exc!r}); using defaults")
        return TameruSettings(), warnings


# ---- compression.* mirror ----------------------------------------------------------------------
def _at_least(minimum: int) -> Callable[[Any], int]:
    """Integer parser that clamps to ``minimum``, like Hermes' ``max(minimum, ...)``."""
    def parse(raw: Any) -> int:
        return max(minimum, _parse_int(raw))
    return parse


def _positive_or_none(raw: Any) -> int | None:
    """``threshold_tokens``: a positive integer, or ``None`` (null, zero, negatives), via Hermes' ``int(raw)``.

    A fractional float truncates, a bool counts as 0/1 and garbage means no cap, as in Hermes.
    """
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value > 0 else None


def _hermes_flag(raw: Any) -> bool:
    """Hermes' legacy truthiness for ``compression`` flags (``agent_init._cfg_flag``)."""
    return str(raw).lower() in {"true", "1", "yes"}


def _lowered(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("expected a string")
    return raw.strip().lower()


def _model_thresholds(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise ValueError("expected a mapping")
    return {
        str(key): float(value) for key, value in raw.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


class CompressionKey(NamedTuple):
    """One ``compression.<config_key>`` entry and the ``ContextCompressor.__init__`` kwarg it feeds."""

    config_key: str
    kwarg: str
    coerce: Callable[[Any], Any]


COMPRESSION_TABLE: tuple[CompressionKey, ...] = (
    CompressionKey("threshold", "threshold_percent", _parse_float),
    CompressionKey("protect_first_n", "protect_first_n", _at_least(0)),
    CompressionKey("protect_last_n", "protect_last_n", _parse_int),
    CompressionKey("target_ratio", "summary_target_ratio", _parse_float),
    CompressionKey("abort_on_summary_failure", "abort_on_summary_failure", _hermes_flag),
    CompressionKey("threshold_tokens", "threshold_tokens_cap", _positive_or_none),
    CompressionKey("proactive_prune_tokens", "proactive_prune_tokens", _at_least(0)),
    CompressionKey("proactive_prune_min_result_chars", "proactive_prune_min_result_chars", _parse_int),
    CompressionKey("proactive_prune_min_reclaim_tokens", "proactive_prune_min_reclaim_tokens", _at_least(0)),
    CompressionKey("min_tail_user_messages", "min_tail_user_messages", _at_least(1)),
    CompressionKey("tail_mode", "tail_mode", _lowered),
    CompressionKey("model_thresholds", "model_thresholds", _model_thresholds),
)

# compression.* keys whose plugin default applies while the user has not set them (read from the raw file).
_PRUNE_DEFAULTS = {
    "proactive_prune_tokens": "default_proactive_prune_tokens",
    "proactive_prune_min_result_chars": "default_proactive_prune_min_result_chars",
}


def _compression_section(config: Any) -> Mapping[str, Any]:
    section = config.get("compression") if isinstance(config, Mapping) else None
    return section if isinstance(section, Mapping) else {}


def prune_default_applies(raw: Mapping[str, Any] | None, settings: TameruSettings) -> bool:
    """True when the plugin, not the user's ``compression.proactive_prune_tokens``, sets the prune trigger."""
    return bool(settings.enabled) and "proactive_prune_tokens" not in _compression_section(raw)


def _threshold(threshold_tokens: Any) -> int:
    try:
        return int(threshold_tokens or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def window_scale(settings: TameruSettings, threshold: int) -> float:
    """1.0, or ``threshold / small_window_threshold_tokens`` for a compaction threshold below that reference.

    The fixed budgets (retained bodies, retained error rows) were sized on a 64K window (compaction threshold
    54,400 tokens). On a 32K window the same chars are twice the share of the context: the post-compaction
    context came out bigger, so the next compaction came sooner (five compactions where stock Hermes, which
    keeps nothing, made two). Below the reference they shrink in proportion; from 64K up nothing changes.
    """
    ref = settings.small_window_threshold_tokens
    if ref <= 0 or threshold <= 0 or threshold >= ref:
        return 1.0
    return threshold / ref


def scaled_prune_tokens(settings: TameruSettings, threshold_tokens: Any) -> int:
    """The plugin's proactive-prune trigger for a compaction threshold of ``threshold_tokens``.

    ``max(default_proactive_prune_tokens, default_proactive_prune_ratio * threshold)``: a fixed trigger
    pruned a large window long before it filled (a 200K window lost facts stock Hermes keeps), so on big
    windows the prune starts near the compaction threshold instead; small windows keep the fixed value.
    """
    floor = settings.default_proactive_prune_tokens
    try:
        threshold = int(threshold_tokens or 0)
    except (TypeError, ValueError, OverflowError):
        threshold = 0
    if floor <= 0 or threshold <= 0 or settings.default_proactive_prune_ratio <= 0:
        return floor
    return max(floor, round(settings.default_proactive_prune_ratio * threshold))


def prune_trigger_follows_window(settings: TameruSettings, threshold_tokens: Any) -> bool:
    """True when ``default_proactive_prune_ratio * threshold`` (not the fixed floor) sets the plugin's trigger.

    That is a window big enough for a prune to leave room to regrow before the compaction (above ~85K tokens with
    Hermes' default threshold). On a smaller one the trigger sits at the threshold and a compaction follows
    within a request or two, so the regrowth rules below do not apply.
    """
    try:
        threshold = int(threshold_tokens or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    floor, ratio = settings.default_proactive_prune_tokens, settings.default_proactive_prune_ratio
    return floor > 0 and ratio > 0 and threshold > 0 and round(ratio * threshold) > floor


def prune_regrowth_tokens(settings: TameruSettings, threshold_tokens: Any) -> int:
    """Tokens the prompt must regrow after a prune commit before the plugin's next prune may fire (0 = Hermes' runway).

    Hermes demands a full trigger-sized regrowth (``max(reclaimed, proactive_prune_tokens)``) so a commit's cache
    break is never paid twice cheaply. On a large window that runway (0.75 of the compaction threshold) is longer
    than the stretch to the compaction a prune is meant to replace, so the session regrew raw rows for the
    rest of its life while stock Hermes' next compaction threw them away: oncall 200K cost +23.7%, default
    128K +4.0%. A commit rewrites the prefix from its first changed row: it pays back once about half of the
    prompt is reclaimed and ~10 requests remain, which a regrowth of ``prune_regrowth_ratio * threshold`` reaches.
    """
    try:
        threshold = int(threshold_tokens or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    if threshold <= 0 or settings.prune_regrowth_ratio <= 0:
        return 0
    return round(settings.prune_regrowth_ratio * threshold)


def scaled_retained_budget(settings: TameruSettings, threshold_tokens: Any) -> int:
    """Chars of extract/brief bodies the retained rows may keep for a compaction threshold of ``threshold_tokens``.

    ``max(retained_extract_budget_chars, round(retained_extract_budget_ratio * threshold))``: a fixed 6,000
    chars is what a 64K window wants (a bigger budget lost facts there), but on 128K to 256K windows, where the
    session reaches the prune trigger with room to spare, it threw away facts a larger budget keeps for
    ~1-2% of the cost. Small windows keep the fixed value.
    """
    threshold = _threshold(threshold_tokens)
    floor = round(settings.retained_extract_budget_chars * window_scale(settings, threshold))
    if threshold <= 0 or settings.retained_extract_budget_ratio <= 0:
        return floor
    return max(floor, round(settings.retained_extract_budget_ratio * threshold))


def scaled_error_budget(settings: TameruSettings, threshold_tokens: Any) -> int:
    """Chars of ``errors`` rows the retained context may keep, scaled down on a window below the 64K reference."""
    return round(settings.retained_error_budget_chars * window_scale(settings, _threshold(threshold_tokens)))


def compression_kwargs(
    merged: Mapping[str, Any] | None,
    raw: Mapping[str, Any] | None,
    settings: TameruSettings,
    init_params: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """``ContextCompressor.__init__`` kwargs from the user's ``compression.*`` section, plus warnings.

    Values come from ``merged`` (Hermes defaults included, so an unconfigured Hermes yields Hermes'
    own values). ``proactive_prune_tokens`` and ``proactive_prune_min_result_chars`` take the
    ``settings.default_*`` values when ``raw`` (the user's file) does not set them, unless the plugin
    is disabled: that stays pure stock. Unusable values and kwargs missing from ``init_params`` are
    dropped with a warning. Never raises.
    """
    warnings: list[str] = []
    try:
        section, raw_section = _compression_section(merged), _compression_section(raw)
        kwargs: dict[str, Any] = {}
        for entry in COMPRESSION_TABLE:
            default_field = _PRUNE_DEFAULTS.get(entry.config_key)
            if default_field and settings.enabled and entry.config_key not in raw_section:
                value = getattr(settings, default_field)
            elif entry.config_key in section:
                try:
                    value = entry.coerce(section[entry.config_key])
                except (TypeError, ValueError, OverflowError) as exc:
                    warnings.append(
                        f"compression.{entry.config_key}={_short(section[entry.config_key])}: {exc}; ignored"
                    )
                    continue
            else:
                continue
            if entry.kwarg in init_params:
                kwargs[entry.kwarg] = value
            else:
                warnings.append(
                    f"compression.{entry.config_key}: ContextCompressor has no {entry.kwarg!r} parameter; ignored"
                )
        return kwargs, warnings
    except Exception as exc:
        return {}, [*warnings, f"compression.* could not be read ({exc!r}); using ContextCompressor defaults"]
