"""``live_calibration``: the conversion, pricing, key handling, retry and budget rules, against a fake provider (no network).

The live comparison itself runs only with ``TAMERU_LIVE=1`` (see the bottom of this file); everything else here is offline.
"""
from __future__ import annotations

import json
import os

import live_calibration as lc
import pytest

SECRET = "sk-ant-test-0123456789-not-a-real-key"


def _messages():
    return [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "fix the failing test"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "terminal", "arguments": "{\"command\": \"pytest\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "FAILED tests/test_a.py::test_x - AssertionError"},
        {"role": "assistant", "content": "I will read the file."},
        {"role": "user", "content": "and then?"},
    ]


# ---- conversion -------------------------------------------------------------------------------------------------------
def test_roles_alternate_start_with_user_and_end_with_user():
    system, out = lc.to_anthropic(_messages(), run_id="r1", tool_block_chars=0)
    roles = [m["role"] for m in out]
    assert roles[0] == "user" and roles[-1] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert system[0]["text"].startswith("[run r1]\n")


def test_tool_calls_and_results_become_text_the_model_is_billed_for():
    _system, out = lc.to_anthropic(_messages(), run_id="r1", tool_block_chars=0)
    text = "\n".join(block["text"] for m in out for block in m["content"])
    assert "[tool call] terminal(" in text and "[tool result: terminal]" in text and "AssertionError" in text


def test_at_most_four_cache_breakpoints_and_the_system_prompt_has_one():
    system, out = lc.to_anthropic(_messages() * 3, run_id="r1", tool_block_chars=100)
    marks = sum(1 for m in out for b in m["content"] if "cache_control" in b) + sum(1 for b in system if "cache_control" in b)
    assert marks <= lc.MAX_BREAKPOINTS and "cache_control" in system[0]


def test_a_conversation_that_starts_with_the_assistant_gets_a_user_first():
    _system, out = lc.to_anthropic([{"role": "assistant", "content": "hello"}], run_id="r", tool_block_chars=0)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]


@pytest.mark.parametrize("chars", [0, 1, 500, 39_993])
def test_the_tool_block_stand_in_has_exactly_the_requested_length(chars):
    assert len(lc.filler(chars)) == chars


def test_two_runs_never_share_a_cache_prefix():
    a, _ = lc.to_anthropic(_messages(), run_id="aaaa", tool_block_chars=50)
    b, _ = lc.to_anthropic(_messages(), run_id="bbbb", tool_block_chars=50)
    assert a[0]["text"][:12] != b[0]["text"][:12]


# ---- pricing ----------------------------------------------------------------------------------------------------------
def test_price_uses_the_cache_multipliers():
    p = lc.price({"input_tokens": 100, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 10_000, "output_tokens": 10})
    assert p["units"] == pytest.approx(100 + 1.25 * 1000 + 0.10 * 10_000)
    assert p["usd"] == pytest.approx((p["units"] * 1.0 + 10 * 5.0) / 1e6)


def test_price_tolerates_missing_usage_fields():
    assert lc.price({})["units"] == 0 and lc.price({"input_tokens": None})["usd"] == 0


# ---- the key ----------------------------------------------------------------------------------------------------------
def test_the_key_comes_from_the_environment_or_a_file(tmp_path):
    assert lc.load_key({"ANTHROPIC_API_KEY": f"  {SECRET} "}) == SECRET
    key_file = tmp_path / "key.txt"
    key_file.write_text(SECRET + "\nsecond line ignored\n", encoding="utf-8")
    assert lc.load_key({"TAMERU_LIVE_KEY_FILE": str(key_file)}) == SECRET


def test_a_missing_key_says_so_without_inventing_one():
    with pytest.raises(RuntimeError, match="no API key"):
        lc.load_key({})


def test_an_empty_key_file_is_a_missing_key(tmp_path):
    empty = tmp_path / "k.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no API key"):
        lc.load_key({"TAMERU_LIVE_KEY_FILE": str(empty)})


# ---- the client -------------------------------------------------------------------------------------------------------
def _ok(usage=None, text="ok"):
    return 200, {"usage": usage or {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
                 "content": [{"type": "text", "text": text}]}


def test_the_client_retries_a_rate_limit_then_succeeds():
    calls = []
    responses = [(429, {"error": {"type": "rate_limit_error"}}), (529, {}), _ok()]

    def transport(url, headers, body):
        calls.append(headers["x-api-key"])
        return responses.pop(0)

    sleeps = []
    client = lc.Client(SECRET, transport, sleep=sleeps.append)
    assert client.send([{"type": "text", "text": "s"}], [{"role": "user", "content": []}], 16)["content"][0]["text"] == "ok"
    assert len(calls) == 3 and len(sleeps) == 2


def test_a_provider_error_does_not_echo_the_key_or_the_request():
    def transport(url, headers, body):
        return 401, {"error": {"type": "authentication_error", "message": f"bad key {SECRET}"}}

    client = lc.Client(SECRET, transport, sleep=lambda _s: None)
    with pytest.raises(RuntimeError) as caught:
        client.send([{"type": "text", "text": "s"}], [{"role": "user", "content": []}], 16)
    assert SECRET not in str(caught.value) and "401" in str(caught.value) and "authentication_error" in str(caught.value)


# ---- one arm ----------------------------------------------------------------------------------------------------------
def _recorded(requests=3, needles=("DB-EU-7", "9f3c")):
    return {"arm": "stock", "scenario": "s", "window": 64_000, "seed": 0, "replay_cost": 100.0, "needles": list(needles),
            "question": "which endpoint?",
            "requests": [{"n": i + 1, "messages": _messages(), "tool_block_chars": 200} for i in range(requests)]}


def test_run_arm_sends_every_request_and_scores_the_closing_answer():
    sent = []

    def transport(url, headers, body):
        payload = json.loads(body)
        sent.append(payload["max_tokens"])
        return _ok(text="The endpoint is DB-EU-7." if len(sent) == 3 else "ok")

    result = lc.run_arm(_recorded(), lc.Client(SECRET, transport), max_usd=5.0)
    assert sent == [lc.FILLER_MAX_TOKENS, lc.FILLER_MAX_TOKENS, lc.PROBE_MAX_TOKENS]
    assert result["requests"] == 3 and result["answered"] == 1 and result["needed"] == 2 and result["missing"] == ["9f3c"]
    assert result["totals"]["fresh"] == 30


def test_the_dollar_cap_stops_a_run_before_the_next_request():
    big = {"input_tokens": 1_000_000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}   # $1 each

    def transport(url, headers, body):
        return _ok(usage=big)

    with pytest.raises(lc.BudgetExceeded):
        lc.run_arm(_recorded(requests=5), lc.Client(SECRET, transport), max_usd=2.0)


def test_the_cap_counts_what_earlier_arms_already_spent():
    def transport(url, headers, body):
        return _ok()

    with pytest.raises(lc.BudgetExceeded):
        lc.run_arm(_recorded(), lc.Client(SECRET, transport), max_usd=1.0, spent_before=1.0)


def test_plan_sends_nothing_and_bounds_the_cost():
    p = lc.plan(_recorded(requests=10))
    assert p["requests"] == 10 and p["usd_upper_bound"] > 0 and p["approx_input_tokens"] > 0


def test_saved_results_hold_no_key_and_no_request_text(tmp_path):
    result = lc.run_arm(_recorded(), lc.Client(SECRET, lambda u, h, b: _ok(text="DB-EU-7")), max_usd=5.0)
    out = tmp_path / "r.json"
    lc.save([result], out)
    blob = out.read_text(encoding="utf-8")
    assert SECRET not in blob and "FAILED tests" not in blob and "fix the failing test" not in blob


def test_summarise_shows_the_ratio_to_the_first_arm():
    base = lc.run_arm(_recorded(), lc.Client(SECRET, lambda u, h, b: _ok()), max_usd=5.0)
    other = dict(base, arm="plugin", replay_cost=50.0, totals=dict(base["totals"], units=base["totals"]["units"] / 2))
    table = lc.summarise([base, other])
    assert "0.500" in table and table.splitlines()[0].startswith("arm")


# ---- the real replay feeds it -----------------------------------------------------------------------------------------
def test_a_recorded_replay_ends_with_the_closing_question_and_names_the_needed_facts():
    recorded = lc.capture("stock_defaults", "default", 64_000, 0, structural=False)
    assert recorded["requests"] and recorded["needles"] and recorded["replay_cost"] > 0
    last = recorded["requests"][-1]["messages"][-1]
    assert last["role"] == "user" and recorded["question"][:30] in lc._text(last["content"])
    system, out = lc.to_anthropic(recorded["requests"][-1]["messages"], run_id="r", tool_block_chars=recorded["requests"][-1]["tool_block_chars"])
    assert out[0]["role"] == "user" and out[-1]["role"] == "user"


def test_the_plugin_arm_records_fewer_characters_than_stock_on_a_long_session():
    stock = lc.capture("stock_defaults", "default", 64_000, 0, structural=False)
    plugin = lc.capture("tameru_lazy", "default", 64_000, 0, structural=False)
    chars = lambda rec: sum(sum(len(lc._text(m.get("content"))) for m in r["messages"]) for r in rec["requests"])  # noqa: E731
    assert chars(plugin) < chars(stock)


# ---- the live run (opt-in) --------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.environ.get("TAMERU_LIVE") != "1", reason="set TAMERU_LIVE=1 (and a key, see live_calibration.py) to spend money")
def test_live_comparison():
    arms = os.environ.get("TAMERU_LIVE_ARMS", "stock_defaults,tameru_lazy").split(",")
    scenarios = os.environ.get("TAMERU_LIVE_SCENARIOS", "default").split(",")
    window = int(os.environ.get("TAMERU_LIVE_WINDOW", "64000"))
    seed = int(os.environ.get("TAMERU_LIVE_SEED", "0"))
    max_usd = float(os.environ.get("TAMERU_LIVE_MAX_USD", "8"))
    out_path = os.environ["TAMERU_LIVE_OUT"]
    client = lc.Client(lc.load_key())
    results, spent = [], 0.0
    try:
        for scenario in scenarios:
            for arm in arms:
                recorded = lc.capture(arm, scenario, window, seed)
                print(lc.plan(recorded), flush=True)
                result = lc.run_arm(recorded, client, max_usd=max_usd, spent_before=spent, log=lambda m: print(m, flush=True))
                spent += result["totals"]["usd"]
                results.append(result)
                lc.save(results, out_path)
    except lc.BudgetExceeded as exc:
        print(f"BUDGET: {exc}", flush=True)
    finally:
        if results:
            print(lc.summarise(results), flush=True)
            print(f"total ${spent:.2f}", flush=True)
    assert results
