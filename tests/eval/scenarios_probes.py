"""``directives``: a replay scenario that probes what a summary must not lose besides plain facts.

Four probe types, each planted so only the surviving text can answer it:

* ``I*``  an instruction the user gave (a standing rule);
* ``R*``  a rejected hypothesis (what was ruled out, so the model does not retry it);
* ``S*``  superseded state: the same command printed a value twice, the later value is the gold one;
* the filler between them is long log output, so a 32K / 48K window compacts several times.

The scenario is not in ``scenario_registry`` (the saved baselines cover the registered scenarios only);
``tests/eval/test_probe_scenario.py`` registers it for one run. Fact ids start with the probe letter.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import payloads_extra as pe  # noqa: E402
from fixtures import hermes_payloads as hp  # noqa: E402
from scenario import Scenario  # noqa: E402
from scenario_kit import Kit, ScenarioSpec, call_key  # noqa: E402

DIRECTIVES_TURNS = 24

FINAL_QUESTION = (
    "Before I hand this over: which standing rules did I give you, which theories did we rule out and why, and what "
    "are the current settings (worker pool size and request timeout)?"
)

_CONF_CMD = "cat config/settings.toml"


def _conf(pool: int, timeout: int) -> str:
    return (
        "[server]\nbind = \"0.0.0.0:8080\"\n\n[workers]\n"
        f"worker_pool_size = {pool}\nrequest_timeout_seconds = {timeout}\n\n[logging]\nlevel = \"info\"\n"
    )


def build_directives(seed: int = 0, turns: int = DIRECTIVES_TURNS) -> Scenario:
    kit = Kit("directives", seed)
    step, rnd, tool = kit.step, kit.rnd, hp.terminal_result
    logs = [
        pe.make_log(kit.rng(f"log{i}"), pe.APP_LOG, n_lines=320, start_sec=3600 * (i + 1), burst=(0.2, 0.8))
        for i in range(14)
    ]

    def read_log(n: int, user: str, final: str):
        text, errs = logs[n].window(0, 320)
        t = kit.turn(user, [rnd("Reading the log.", step("terminal", {"command": f"tail -n 320 /var/log/app-{n}.log"}, tool(text)))], final)
        kit.errors(t, errs)
        return t

    t1 = kit.turn(
        "Two standing rules for this whole session: never edit anything under migrations/, and always run the tests with --no-cov.",
        [], "Understood: I will not touch migrations/ and I will run the tests with --no-cov.",
    )
    kit.fact("I1", "decision", "never edit anything under migrations/", t1, "user", True)
    kit.fact("I2", "decision", "always run the tests with --no-cov", t1, "user", True)
    t2 = kit.turn(
        "Show me the current settings.",
        [rnd("Reading the settings.", step("terminal", {"command": _CONF_CMD}, tool(_conf(8, 30)), nth=1))],
        "Pool of 8 workers, 30 second request timeout.",
    )
    read_log(0, "Check the first app log.", "Nothing unusual yet.")
    t4 = kit.turn(
        "Could stale DNS answers explain the timeouts?", [],
        "Hypothesis H1 is ruled out: DNS caching is not the cause, the resolver log shows no stale answers for the upstream host.",
    )
    kit.fact("R1", "decision", "DNS caching is not the cause", t4, "assistant", True)
    read_log(1, "Next log.", "Same pattern, a burst of slow requests in the middle.")
    t6 = kit.turn(
        "Do not go back to the TLS theory: we rejected it on Tuesday because the certificate chain was valid end to end.", [],
        "Noted, TLS stays off the list.",
    )
    kit.fact("R2", "decision", "rejected it on Tuesday because the certificate chain was valid", t6, "user", True)
    read_log(2, "Another log.", "Slow requests cluster around the same minute.")
    read_log(3, "And the fourth.", "Consistent with the others.")
    t9 = kit.turn(
        "Raise the pool and the timeout, then show me the settings again.",
        [rnd("Applying the change and reading the file.", step("terminal", {"command": _CONF_CMD}, tool(_conf(32, 90)), nth=2))],
        "Done: the pool is 32 workers and the timeout is 90 seconds.",
    )
    kit.fact("S1", "artifact", "worker_pool_size = 32", t9, "tool", True, call_key("terminal", {"command": _CONF_CMD}, 2))
    kit.fact("S2", "artifact", "request_timeout_seconds = 90", t9, "tool", True, call_key("terminal", {"command": _CONF_CMD}, 2))
    read_log(4, "Look at the log after the change.", "Fewer slow requests.")
    read_log(5, "One more.", "Still better.")
    read_log(6, "And another.", "Stable.")
    read_log(7, "Last one.", "Stable, no new errors of the earlier kind.")
    for n in range(8, 14):
        read_log(n, f"Log {n}.", "Nothing new.")
    for i in range(10, 14):
        kit.turn(f"Anything else to flag (check {i - 9})?", [], "Nothing further.")
    assert t2 and t1 < t4  # keep the planted turns referenced
    return kit.finish(FINAL_QUESTION, turns, natural=DIRECTIVES_TURNS, log_turn=3)


DIRECTIVES_SPEC = ScenarioSpec(
    "directives", build_directives, DIRECTIVES_TURNS, 4,
    "24 turns: standing rules, rejected theories and superseded settings among long logs",
)
