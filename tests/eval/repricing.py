"""Price a replayed session under any dated price profile, after the fact (P1c; replay only).

A matrix row written with ``price_log=True`` (``protocol3.measure``) carries ``row["pricing"]``: per sent request the characters
sent, the common prefix with the previous request (before any cache expiry), the virtual send time, Hermes' rough prompt tokens,
the user turn and the Anthropic breakpoint positions; per summary call its input and output characters; and the read-back terms
of ``effective``, ``effective_real`` and ``effective_finder``. Engine decisions do not depend on the price, so one run per arm
gives the cost under every profile:

    cost = r * cached-read chars + w * written chars + summary input chars (cold, 1x) + o * summary output chars

in "miss units" (uncached input = 1), each request scaled by the profile's long-context tier when it has one.

Parameters (all explicit):

* ``ttl_s``        None = no expiry (the primary). Otherwise a request whose idle gap since the previous one exceeds it rewrites
                   the whole prefix; with breakpoints, each cache entry lives ``ttl_s`` from its last write or read.
* ``idle``         human think time before each user turn (``protocol3.IDLE_PROFILES``), added to the replay's own clock for
                   pricing only (the engines ran without it).
* ``granularity``  ``exact`` (the replay's character-level prefix), ``unit`` (cached reads only in whole prefix units of
                   ``unit_tokens``; a request that only appends keeps its whole prefix, since units also form at request
                   boundaries) or ``breakpoints`` (a hit only at a breakpoint an earlier request wrote: Hermes' system-and-3
                   layout; the 20-block lookback limit is not modelled, so hits are an upper bound).
* ``o``            output price; a profile without one (generic OpenAI) uses 5.0, an assumption stated by the round-2 plan.

Prices come from ``docs/results/prices-2026-11-19.json`` (or ``REPLAY_PRICES``), a copy of the engine's ``src/tameru/prices.json``
whose sha256 (LF line endings) must equal ``PRICES_SHA256``. Characters stand in for tokens at 4 per token, as in the replay.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
PRICES_COPY = ROOT / "docs" / "results" / "prices-2026-11-19.json"
PRICES_SHA256 = "070dcfa3551e875c310334781f534346817c3d73008299d872a432c17543faa0"      # engine 7a7bb83, tests/test_prices.py
CHARS_PER_TOKEN = 4
ASSUMED_OUTPUT = 5.0               # round-2 plan section 4: a profile with no output price is priced at o = 5 in replay
DEEPSEEK_UNIT_TOKENS = 64          # assumed, unverified: DeepSeek does not state its prefix-unit size

# Registered weights for this host (docs/ANALYSIS-PLAN-ROUND2.md in the engine, section 4): equal, 1/5 each.
HERMES_WEIGHTS = {"anthropic-opus-5.5-5m": 0.2, "anthropic-sonnet-5.5-5m": 0.2, "anthropic-haiku-5.5-5m": 0.2,
                  "openai-generic-30m": 0.2, "deepseek-v4-pro": 0.2}


def prices_path() -> Path:
    return Path(os.environ["REPLAY_PRICES"]) if os.environ.get("REPLAY_PRICES") else PRICES_COPY


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_prices(path: Path | None = None) -> dict:
    """The price file, refused unless its sha256 is the pinned one (the engine's file at 7a7bb83)."""
    path = prices_path() if path is None else Path(path)
    if file_sha256(path) != PRICES_SHA256:
        raise ValueError(f"{path.name}: sha256 differs from the pinned engine prices.json; refresh the copy and the pin together")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclasses.dataclass(frozen=True)
class Pricing:
    name: str
    r: float
    w: float
    o: float = 0.0
    ttl_s: float | None = None
    tiers: tuple = ()                   # ((above_prompt_tokens, multiplier, applies_to_output), ...)
    granularity: str = "exact"          # exact | unit | breakpoints
    unit_tokens: int | None = None
    idle: str = "none"

    def tier(self, prompt_tokens: float) -> tuple[float, bool]:
        mult, out = 1, False
        for above, m, to_output in self.tiers:
            if prompt_tokens > above:
                mult, out = m, to_output
        return mult, out


ORIGINAL = Pricing("original-0.10/1.25", r=0.10, w=1.25)      # the replay's own price: no output, exact prefix, no expiry


def profile_pricing(name: str, prices: Mapping | None = None, *, ttl: Any = None, granularity: str = "provider",
                    idle: str = "none") -> Pricing:
    """``ttl``: None (no expiry), ``"profile"`` (the profile's own TTL; None where it has none) or seconds."""
    prices = load_prices() if prices is None else prices
    raw = prices["profiles"][name]
    rel = raw["relative"]
    o = ASSUMED_OUTPUT if rel.get("o") is None else float(rel["o"])
    ttl_s = raw.get("ttl_s") if ttl == "profile" else ttl
    tiers = tuple((int(t["above_prompt_tokens"]), float(t["multiplier"]), str(t.get("applies_to", "")).startswith("every price"))
                  for t in raw.get("tiers") or ())
    unit = None
    if granularity == "provider":
        granularity = {"deepseek": "unit", "anthropic": "breakpoints"}.get(raw["provider"], "exact")
    if granularity == "unit":
        unit = DEEPSEEK_UNIT_TOKENS
    return Pricing(name, r=float(rel["r"]), w=float(rel["w"]), o=o, ttl_s=None if ttl_s is None else float(ttl_s),
                   tiers=tiers, granularity=granularity, unit_tokens=unit, idle=idle)


def _idle(profile: str):
    if profile == "none":
        return None
    import protocol3
    return protocol3.IDLE_PROFILES[profile]


def _gap(prev: list, cur: list, idle) -> float:
    gap = (cur[2] or 0.0) - (prev[2] or 0.0)
    if idle is not None:
        gap += sum(float(idle(turn)) for turn in range(prev[4] + 1, cur[4] + 1) if turn > 1)
    return gap


def _hundredths(x: float) -> float:
    """``x * 100`` as the replay uses it: an exact integer when it is one (so the original price reproduces bit for bit)."""
    v = x * 100
    return round(v) if abs(v - round(v)) < 1e-9 else v


def price_row(row: Mapping, p: Pricing) -> dict:
    """Cost of one priced row under ``p``: the split and the three effective prices per answered turn (unrounded)."""
    led = row["pricing"]
    idle = _idle(p.idle)
    read = write = 0                     # tier-weighted characters
    read_raw = write_raw = 0
    expiries = tier_requests = 0
    entries: dict[int, float] = {}       # breakpoints: position -> last write/read time
    prev = None
    for req in led["requests"]:
        total, lcp, when, tokens, _turn, bps = req
        gap = _gap(prev, req, idle) if prev is not None else 0.0
        now = (when or 0.0) + (0.0 if idle is None else sum(float(idle(t)) for t in range(2, req[4] + 1)))
        expired = prev is not None and p.ttl_s is not None and gap > p.ttl_s
        if p.granularity == "breakpoints":
            entries = {pos: t for pos, t in entries.items()
                       if pos <= lcp and (p.ttl_s is None or now - t <= p.ttl_s)}
            hit = max(entries, default=0)
            if hit:
                entries[hit] = now
            for pos in bps:
                entries[pos] = now
        else:
            hit = 0 if (prev is None or expired) else lcp
            if p.granularity == "unit" and hit and hit != prev[0]:
                unit = p.unit_tokens * CHARS_PER_TOKEN
                hit = (hit // unit) * unit
        expiries += bool(expired)
        mult, _ = p.tier(tokens)
        tier_requests += mult != 1
        read_raw += hit
        write_raw += total - hit
        read += mult * hit
        write += mult * (total - hit)
        prev = req
    aux_in = 0
    output = 0.0
    for inp, outp in led["summaries"]:
        mult, to_output = p.tier(inp / CHARS_PER_TOKEN)
        aux_in += mult * inp
        output += (mult if to_output else 1) * outp
    main = (_hundredths(p.r) * read + _hundredths(p.w) * write) / 100
    base = main + aux_in + p.o * output
    answered = led["answered"]

    def extra(field: str) -> float:
        total = 0.0
        for k, r1, w1, r2, w2 in led["readback_terms"][field]:
            prompt = r2 or r1
            mult, _ = p.tier(prompt / CHARS_PER_TOKEN) if prompt else (1, False)
            term = k * (p.r * r1 + p.w * w1) + p.r * r2 + p.w * w2
            total += term if mult == 1 else mult * term
        return total

    return {
        "read": p.r * read / answered, "write": p.w * write / answered, "summary_input": aux_in / answered,
        "output": p.o * output / answered, "base": base / answered,
        "effective": (base + extra("effective")) / answered,
        "effective_real": (base + extra("effective_real")) / answered,
        "effective_finder": (base + extra("effective_finder")) / answered,
        "read_chars": read_raw, "written_chars": write_raw, "expiries": expiries, "tier_requests": tier_requests,
    }


def repriced_rows(rows, p: Pricing) -> list[dict]:
    """Rows in the ``stats`` shape (arm, scenario, window, seed) with the repriced fields; unpriced rows are skipped."""
    out = []
    for row in rows:
        if "pricing" not in row:
            continue
        priced = price_row(row, p)
        out.append({k: row[k] for k in ("arm", "scenario", "window", "seed", "compactions", "prefix_breaks", "kept",
                                         "recoverable", "lost", "needed", "requests")} | priced)
    return out


def original_fields(row: Mapping) -> dict:
    """The original row's four prices recomputed from its ledger at the replay's own price (rounded as the row is)."""
    priced = price_row(row, ORIGINAL)
    return {k: round(priced[k], 1) for k in ("base", "effective", "effective_real", "effective_finder")}
