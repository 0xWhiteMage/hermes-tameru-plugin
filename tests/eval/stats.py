"""Paired statistics for replay matrices: the intervals the docs quote, computed by code that is committed and tested.

A matrix row is one replayed session (``protocol3.measure``): an arm, a scenario, a window and a seed, with the cost and the
fact counts. Two arms are compared on the *same* scenario, window and seed, so the difference is paired by construction and
filler jitter cancels. This module turns matched rows into relative differences and puts a percentile bootstrap around the
mean. Everything is deterministic: the resampling uses ``random.Random(seed)`` and nothing else.

Two intervals, because the cells are not independent:

* ``bootstrap_ci``          resamples the individual paired differences (treats every cell-seed as independent; optimistic);
* ``cluster_bootstrap_ci``  resamples whole scenarios, then the differences inside them (the honest one when the number of
                            shapes is small, and it is: six or seven shapes compact at a given window).

``a_a_difference`` is the sanity test: an arm compared with itself must give exactly zero and an interval of [0, 0].
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Iterable, Mapping, Sequence

KEY = ("scenario", "window", "seed")


def _index(rows: Iterable[Mapping], arm: str, key: Sequence[str] = KEY) -> dict[tuple, Mapping]:
    return {tuple(row[k] for k in key): row for row in rows if row["arm"] == arm}


def paired(rows: Iterable[Mapping], base_arm: str, arm: str, field: str = "effective", key: Sequence[str] = KEY) -> list[dict]:
    """Matched rows of two arms with the relative change of ``field`` (positive = ``arm`` costs more than ``base_arm``)."""
    rows = list(rows)
    base, other = _index(rows, base_arm, key), _index(rows, arm, key)
    out = []
    for cell in sorted(set(base) & set(other)):
        a, b = base[cell][field], other[cell][field]
        if not a:
            continue
        out.append({"cell": dict(zip(key, cell)), "base": a, "value": b, "rel": (b - a) / a})
    return out


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    position = q * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def bootstrap_ci(values: Sequence[float], *, alpha: float = 0.05, resamples: int = 10_000, seed: int = 0) -> dict:
    """Percentile bootstrap interval of the mean of independent values."""
    values = list(values)
    if not values:
        return {"n": 0, "mean": 0.0, "lo": 0.0, "hi": 0.0}
    rng = random.Random(seed)
    n = len(values)
    means = sorted(mean([values[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples))
    return {"n": n, "mean": mean(values), "lo": _percentile(means, alpha / 2), "hi": _percentile(means, 1 - alpha / 2)}


def cluster_bootstrap_ci(clusters: Mapping[str, Sequence[float]], *, alpha: float = 0.05, resamples: int = 10_000, seed: int = 0) -> dict:
    """Interval of the mean when whole clusters (scenarios) are the unit of resampling.

    Each resample draws clusters with replacement, then draws values with replacement inside every drawn cluster, and takes the
    mean of the cluster means. The point estimate is the mean of the cluster means as well, so every shape counts once.
    """
    groups = [list(v) for _, v in sorted(clusters.items()) if len(v)]
    if not groups:
        return {"clusters": 0, "n": 0, "mean": 0.0, "lo": 0.0, "hi": 0.0}
    rng = random.Random(seed)
    k = len(groups)
    estimate = mean([mean(g) for g in groups])
    draws = []
    for _ in range(resamples):
        picked = [groups[rng.randrange(k)] for _ in range(k)]
        draws.append(mean([mean([g[rng.randrange(len(g))] for _ in range(len(g))]) for g in picked]))
    draws.sort()
    return {"clusters": k, "n": sum(len(g) for g in groups), "mean": estimate,
            "lo": _percentile(draws, alpha / 2), "hi": _percentile(draws, 1 - alpha / 2)}


def compare(rows: Iterable[Mapping], base_arm: str, arm: str, *, field: str = "effective", window: int | None = None,
            resamples: int = 10_000, seed: int = 0) -> dict:
    """One comparison: the mean relative change of ``field`` with a naive and a cluster-bootstrap interval.

    ``window`` restricts to one context window. The cluster interval treats each scenario as one cluster, so it is the one to
    read; the naive one is kept to show how much the dependence matters.
    """
    pairs = [p for p in paired(rows, base_arm, arm, field) if window is None or p["cell"]["window"] == window]
    by_scenario: dict[str, list[float]] = defaultdict(list)
    for p in pairs:
        by_scenario[p["cell"]["scenario"]].append(p["rel"])
    naive = bootstrap_ci([p["rel"] for p in pairs], resamples=resamples, seed=seed)
    clustered = cluster_bootstrap_ci(by_scenario, resamples=resamples, seed=seed)
    return {"base": base_arm, "arm": arm, "field": field, "window": window, "cells": len(pairs),
            "naive": naive, "cluster": clustered,
            "excludes_zero": clustered["clusters"] > 1 and (clustered["lo"] > 0 or clustered["hi"] < 0)}


def a_a_difference(rows: Iterable[Mapping], arm: str, field: str = "effective") -> dict:
    """An arm against itself: every relative change must be exactly zero."""
    return compare(rows, arm, arm, field=field, resamples=200)


def per_cell(rows: Iterable[Mapping], base_arm: str, arm: str, field: str = "effective", *, resamples: int = 2_000, seed: int = 0) -> list[dict]:
    """Per scenario and window: the mean relative change over seeds with a bootstrap interval (seeds are the only replicates)."""
    cells: dict[tuple, list[float]] = defaultdict(list)
    for p in paired(rows, base_arm, arm, field):
        cells[(p["cell"]["scenario"], p["cell"]["window"])].append(p["rel"])
    out = []
    for (scenario, window), values in sorted(cells.items()):
        ci = bootstrap_ci(values, resamples=resamples, seed=seed)
        out.append({"scenario": scenario, "window": window, "n": ci["n"], "mean": ci["mean"], "lo": ci["lo"], "hi": ci["hi"],
                    "excludes_zero": ci["n"] > 1 and (ci["lo"] > 0 or ci["hi"] < 0)})
    return out
