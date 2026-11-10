"""A signed estimate of what Tameru's cleanups saved or cost in this chat (``net_estimate: on``, off by default).

The unit is uncached-input-token equivalents, priced with the usual provider cache rates (a cache read costs 10% of an
uncached token, a cache write 125%). For every cleanup Tameru recorded:

* saving   = tokens it removed x replies sent since x 0.10   (the removed text no longer rides each cached prefix)
* cost     = tokens from its first change to the end of the prompt x 1.15   (that suffix is written to the cache again
             instead of read: 1.25 - 0.10)

A cleanup with no recorded suffix (a full compaction pass) adds its saving and an unknown cost, and the result says so.
It is an estimate: output tokens, the recovery tool's own schema and the provider's real cache lifetime are not in it, and
the figure is not a bill. Numbers only, never text.
"""
from __future__ import annotations

from typing import Any

READ_RATE = 0.10
WRITE_RATE = 1.25
MAX_READS = 200            # a long chat does not credit one cleanup with more replies than this


def estimate(events, responses_now: int) -> dict[str, Any] | None:
    """``events``: (removed_tokens, suffix_tokens or None, replies_at_commit). None when nothing was recorded."""
    saving = cost = 0.0
    unknown = 0
    count = 0
    for removed, suffix, at in events:
        if not isinstance(removed, int) or removed <= 0:
            continue
        count += 1
        reads = max(0, min(MAX_READS, int(responses_now) - int(at)))
        saving += removed * reads * READ_RATE
        if isinstance(suffix, int) and suffix >= 0:
            cost += suffix * (WRITE_RATE - READ_RATE)
        else:
            unknown += 1
    if not count:
        return None
    net = saving - cost
    return {"net": round(net), "saving": round(saving), "cost": round(cost), "cleanups": count,
            "unknown_cost": unknown, "sign": "saving" if net > 0 else ("cost" if net < 0 else "even")}


def describe(result: dict[str, Any] | None) -> str:
    if result is None:
        return "no cleanups yet"
    word = {"saving": "saved about", "cost": "cost about", "even": "about even,"}[result["sign"]]
    amount = abs(result["net"])
    text = f"{word} {amount:,} uncached-input tokens ({result['cleanups']} cleanups, estimate)"
    if result["unknown_cost"]:
        text += f"; {result['unknown_cost']} without a measured rewrite cost, so this leans toward saving"
    return text
