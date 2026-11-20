"""``repricing``: post-hoc cost under any price profile from a row's pricing ledger. No replay, no Hermes."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

import repricing
import stats

ENGINE_PRICES = Path(os.environ.get("TAMERU_ENGINE_REPO", "").strip() or repricing.ROOT.parent / "Tameru") / "src" / "tameru" / "prices.json"


def _row(arm="a", scenario="s", seed=0, window=64_000):
    """Three requests: append-only, then an edit at 900 (a prune), then append-only again 400 s later; one summary call."""
    requests = [
        [1_000, 0, 0.0, 250, 1, [300, 600, 800, 1_000]],
        [1_500, 1_000, 20.0, 375, 1, [300, 1_100, 1_300, 1_500]],
        [1_400, 900, 40.0, 350, 2, [300, 1_000, 1_200, 1_400]],
        [1_600, 1_400, 440.0, 400, 3, [300, 1_200, 1_400, 1_600]],
    ]
    return {"arm": arm, "scenario": scenario, "window": window, "seed": seed, "compactions": 0, "prefix_breaks": 1,
            "kept": 1, "recoverable": 0, "lost": 0, "needed": 1, "requests": 4,
            "pricing": {"answered": 2, "summary_input_chars": 500, "summary_output_chars": 100,
                        "requests": requests, "summaries": [[500, 100]],
                        "readback_terms": {"effective": [[0, 0, 0, 0, 400]], "effective_real": [[0, 0, 0, 1375.0, 400]],
                                           "effective_finder": [[2, 1375.0, 300, 1375.0, 700]]}}}


def test_the_committed_price_copy_matches_the_pinned_engine_hash():
    assert repricing.file_sha256(repricing.PRICES_COPY) == repricing.PRICES_SHA256
    assert set(repricing.HERMES_WEIGHTS) <= set(repricing.load_prices()["profiles"])
    assert abs(sum(repricing.HERMES_WEIGHTS.values()) - 1) < 1e-12


@pytest.mark.skipif(not ENGINE_PRICES.is_file(), reason="engine checkout not beside the plugin (set TAMERU_ENGINE_REPO)")
def test_the_copy_is_the_engine_file():
    assert repricing.file_sha256(ENGINE_PRICES) == repricing.file_sha256(repricing.PRICES_COPY)


def test_replay_prices_env_is_read_and_a_changed_file_is_refused(tmp_path, monkeypatch):
    good = tmp_path / "p.json"
    good.write_bytes(repricing.PRICES_COPY.read_bytes())
    monkeypatch.setenv("REPLAY_PRICES", str(good))
    assert repricing.prices_path() == good and "profiles" in repricing.load_prices()
    good.write_text(good.read_text(encoding="utf-8").replace("0.022", "0.023"), encoding="utf-8")
    with pytest.raises(ValueError):
        repricing.load_prices()
    monkeypatch.delenv("REPLAY_PRICES")
    assert repricing.prices_path() == repricing.PRICES_COPY


def test_the_original_price_reproduces_the_replay_arithmetic():
    priced = repricing.price_row(_row(), repricing.ORIGINAL)
    read, written = 1_000 + 900 + 1_400, 1_000 + 500 + 500 + 200
    base = (10 * read + 125 * written) / 100 + 500
    assert priced["base"] == base / 2
    assert priced["effective"] == (base + 1.25 * 400) / 2
    assert priced["effective_real"] == (base + (0.10 * 1375.0 + 1.25 * 400)) / 2
    assert priced["effective_finder"] == (base + (2 * (0.10 * 1375.0 + 1.25 * 300) + 0.10 * 1375.0 + 1.25 * 700)) / 2
    assert priced["output"] == 0


def test_ttl_expiry_rewrites_the_prefix_only_after_a_long_gap():
    p = repricing.Pricing("t", r=0.1, w=1.25, ttl_s=300)
    priced = repricing.price_row(_row(), p)
    assert priced["expiries"] == 1 and priced["read_chars"] == 1_000 + 900
    idle = repricing.price_row(_row(), repricing.Pricing("t", r=0.1, w=1.25, ttl_s=300, idle="slow"))
    assert idle["expiries"] == 2 and idle["read_chars"] == 1_000      # 600 s before turns 2 and 3 as well


def test_prefix_units_round_an_edit_down_but_keep_an_append():
    p = repricing.Pricing("u", r=0.1, w=1.0, granularity="unit", unit_tokens=64)      # 256 characters
    priced = repricing.price_row(_row(), p)
    assert priced["read_chars"] == 1_000 + 768 + 1_400


def test_breakpoints_fall_back_to_the_last_written_breakpoint_below_the_edit():
    p = repricing.Pricing("b", r=0.05, w=1.25, granularity="breakpoints")
    priced = repricing.price_row(_row(), p)
    # request 2 hits 1,000 (written by request 1); request 3's edit at 900 falls back to 800 (request 1's breakpoint)
    assert priced["read_chars"] == 1_000 + 800 + 1_400
    short = repricing.price_row(_row(), repricing.Pricing("b", r=0.05, w=1.25, granularity="breakpoints", ttl_s=30))
    # with a 30 s entry lifetime request 1's entry at 800 (never read again) is gone at 40 s, and everything is gone at 440 s
    assert short["read_chars"] == 1_000 + 300 + 0


def test_a_long_context_tier_scales_every_price_above_its_threshold():
    p = repricing.Pricing("h", r=0.1, w=1.25, o=5.0, tiers=((300, 5.0, True),))
    priced = repricing.price_row(_row(), p)
    assert priced["tier_requests"] == 3
    assert priced["output"] == 5.0 * 100 / 2              # the summary prompt (125 tokens) is under the tier: output at 1x
    assert priced["summary_input"] == 500 / 2


def test_profiles_load_with_their_registered_rules():
    deepseek = repricing.profile_pricing("deepseek-v4-pro")
    assert deepseek.granularity == "unit" and deepseek.unit_tokens == 64 and deepseek.ttl_s is None
    haiku = repricing.profile_pricing("anthropic-haiku-5.5-5m", ttl="profile")
    assert haiku.granularity == "breakpoints" and haiku.ttl_s == 300 and haiku.tiers == ((100_000, 5.0, True),)
    assert repricing.profile_pricing("openai-generic-30m").o == repricing.ASSUMED_OUTPUT


def test_an_arm_against_itself_is_exactly_zero_under_every_profile_and_window():
    rows = [_row(arm=arm, scenario=s, seed=seed, window=w) for arm in ("a", "b") for s in ("s1", "s2")
            for seed in (0, 1) for w in (64_000, 128_000)]
    for row in rows:
        if row["arm"] == "b":                 # identical ledgers, as a rerun of the same arm gives
            row["pricing"] = copy.deepcopy(row["pricing"])
    prices = repricing.load_prices()
    for name in prices["profiles"]:
        for ttl in (None, "profile", 300, 3600):
            repriced = repricing.repriced_rows(rows, repricing.profile_pricing(name, prices, ttl=ttl))
            for window in (64_000, 128_000):
                for field in ("effective", "effective_real", "effective_finder"):
                    c = stats.compare(repriced, "a", "b", field=field, window=window, resamples=50)
                    assert c["cells"] == 4
                    assert c["cluster"]["mean"] == 0.0 and c["cluster"]["lo"] == 0.0 and c["cluster"]["hi"] == 0.0


ROWS = repricing.ROOT / "tests" / "eval" / "results" / "repriced-1.10.0-rows.json"


@pytest.mark.skipif(not ROWS.is_file(), reason="no committed repriced rows")
def test_committed_rows_reproduce_the_original_price_and_each_arm_against_itself_is_zero():
    import json

    import repriced_report

    rows = json.loads(ROWS.read_text(encoding="utf-8"))["rows"]
    for row in rows:
        assert repricing.original_fields(row) == {k: row[k] for k in ("base", "effective", "effective_real", "effective_finder")}
    prices = repricing.load_prices()
    variants = {"original": repricing.repriced_rows(rows, repricing.ORIGINAL)}
    for name in prices["profiles"]:
        for ttl in (None, "profile", 300, 3600):
            variants[f"{name}|{ttl}"] = repricing.repriced_rows(rows, repricing.profile_pricing(name, prices, ttl=ttl, idle="mixed"))
    assert repriced_report.self_check(variants) == len(variants) * 3 * len({r["window"] for r in rows}) * 3
