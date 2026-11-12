"""Does a prune pay for the cache rewrite it causes? A computed answer from quantities the plugin measures, as an alternative to a fixed share.

A prune that changes message ``k`` makes the provider rewrite everything from ``k`` onward into its prompt cache. Of that text, the part that
was already cached (``rewritten``, the rows before the new assistant call) goes from a cache read, price ``R`` per token, to a cache write,
price ``W``; the new rows (the assistant call and its tool results) are written at ``W`` whether or not anything is pruned. If the prune
removes ``g`` tokens:

    extra cost now          = W * (rewritten - g) - R * rewritten   =   (W - R) * rewritten - W * g
    saving on every later request that would have carried the text   =   R * g

so over ``n`` more requests the prune pays for itself when

    g * (W + R * n)  >=  (W - R) * rewritten                                                    (early rule)

``n`` is how many requests are left before Hermes would compact anyway: headroom to its threshold divided by how fast the prompt grows.
When that is one or two requests the prune also avoids (or delays) a compaction, which costs about ``kappa`` uncached tokens per token it keeps
out of the prompt, so the credit ``kappa * g`` joins the left side:

    g * (W + kappa)  >=  (W - R) * rewritten                                                    (deadline rule)

``kappa`` comes from the compaction itself: a summariser call reads the whole prompt once at the uncached price and the prompt that comes back is
rewritten into the cache, so per token that stays out of the prompt the cost is

    kappa = (B * 1 + (W - R) * P) / (B - P)       with B the prompt at compaction (Hermes' threshold) and P what it shrinks to.

``accrual`` is the textbook rent-or-buy alternative: carry a running cost ``R * g`` for every check the prunable text sits in the prompt, and buy
(prune) when the carried cost covers the rewrite. It is here to be measured, not because it is expected to win.

Every function here is pure: the same numbers give the same answer. Nothing reads a clock or the session.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

RULES = ("share", "share_cached", "payback_early", "payback", "accrual")
DEADLINE_REQUESTS = 2             # "the threshold is one or two requests away"
PRIOR_SHRINK = 0.35               # until Hermes has compacted once in this chat: a compaction leaves about this share of its threshold


@dataclass(frozen=True)
class Pricing:
    """Provider cache multipliers against the uncached input price: a read costs ``read``, a write ``write``."""
    write: float = 1.25
    read: float = 0.10

    def __post_init__(self) -> None:
        if not (self.write > self.read > 0):
            raise ValueError("cache prices need write > read > 0")


def requests_left(headroom_tokens: int, growth_per_request: float) -> float | None:
    """Requests until the threshold at this growth, or ``None`` when growth is unknown or not positive."""
    if growth_per_request is None or growth_per_request <= 0:
        return None
    return max(0.0, headroom_tokens / growth_per_request)


def avoided_compaction_per_token(threshold_tokens: int, post_compaction_tokens: int | None, pricing: Pricing) -> float:
    """``kappa``: uncached-token equivalents a compaction costs per token it keeps out of the prompt."""
    before = max(1, int(threshold_tokens))
    after = post_compaction_tokens if isinstance(post_compaction_tokens, int) and 0 < post_compaction_tokens < before else int(PRIOR_SHRINK * before)
    after = min(max(1, after), before - 1) if before > 1 else 1
    return (before + (pricing.write - pricing.read) * after) / max(1, before - after)


def _at_least(value: float, floor: int) -> int:
    return max(1, int(floor), math.ceil(value - 1e-9))


def required_reclaim(rule: str, *, suffix: int, rewritten: int | None, share: float, minimum: int, pricing: Pricing = Pricing(),
                     headroom_tokens: int | None = None, growth_per_request: float | None = None,
                     kappa: float | None = None, carried: float = 0.0) -> int:
    """The least a prune must remove (tokens) to be worth its cache rewrite under ``rule``.

    ``suffix`` is everything from the first changed message to the end (what the fixed-share rule has always used); ``rewritten`` is the
    part of it the provider had cached, or ``None`` when that is unknown, in which case every rule that wants it falls back to ``suffix``.
    A rule that needs a growth rate it does not have falls back to the cached-share rule rather than guessing.
    """
    if rule not in RULES:
        raise ValueError(f"unknown decision rule {rule!r}")
    cached = suffix if rewritten is None else rewritten
    if rule == "share":
        return _at_least(suffix * share, minimum)
    if rule == "share_cached":
        return _at_least(cached * share, minimum)
    w, r = pricing.write, pricing.read
    if rule == "accrual":
        return _at_least(max(0.0, (w - r) * cached - carried) / w, minimum)
    n = None if headroom_tokens is None else requests_left(headroom_tokens, growth_per_request or 0)
    if n is None:
        return _at_least(cached * share, minimum)
    early = (w - r) * cached / (w + r * n)
    if rule == "payback" and n <= DEADLINE_REQUESTS and kappa is not None:
        return _at_least(min(early, (w - r) * cached / (w + kappa)), minimum)
    return _at_least(early, minimum)
