"""Extra replay scenarios: session shapes the original 30-turn story does not have.

The original session (``scenario.build_scenario``) is one script with one shape: a few big results, one patch
and re-read, one compaction for stock Hermes. The scenarios below are different shapes of agent work, each built
with ``scenario_kit.Kit``:

* ``oncall``    40 turns, log heavy: ``kubectl`` / ``journalctl`` / ``docker logs``, a log that is tailed again and
                again while it grows, large JSON dumps; two or more full compactions;
* ``refactor``  35 turns of read -> patch -> re-read on large files, ``search_files`` / ``rg`` and test runs;
                nearly every old result is superseded by a later one;
* ``smallrows`` 40 turns of many results of 800-3,000 characters, where per-row overhead and Hermes' minimum
                result size for pruning matter;
* ``i18n``      CJK / Arabic / Cyrillic heavy outputs.

Every scenario has gold facts in the four probe categories (recall / artifact / continuation / decision), planted
in user text, assistant text and (mostly) tool output of different age, and error lines whose fingerprints the
metrics follow. Tool-planted needles are never repeated in assistant or user text, so a fact survives only if the
engine kept the tool output (or what it knows of it).
"""
from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:  # ``fixtures`` lives next to ``eval``
    sys.path.insert(0, str(_TESTS_DIR))

import payloads_extra as pe  # noqa: E402
from fixtures import hermes_payloads as hp  # noqa: E402
from scenario import Scenario  # noqa: E402
from scenario_kit import Kit, ScenarioSpec  # noqa: E402

# =================================================================================================
# oncall
# =================================================================================================
ONCALL_TURNS = 40

ONCALL_FINAL_QUESTION = (
    "Write the handoff note for the incident channel. I need: the incident id, which order failed first, when the "
    "first checkout-api container was killed, the connection pool size we found and the config key it lives under, "
    "the trace id of the slowest request, how many orders failed in total, which worker job crashed first, which "
    "helm revision we rolled back to, which file we patched for the leak and how many tests pass with the fix, "
    "where the memory profile was saved, how much of the error budget the incident burned, what we decided about "
    "rollback versus scaling, about redis and about the holds_v2 flag, the severity decision, and the next steps "
    "(who to ping and where, when to re-check, when the postmortem is due)."
)


def build_oncall(seed: int = 0, turns: int = ONCALL_TURNS) -> Scenario:
    """A night of on-call: pods and events, API logs, a worker log tailed six times, journald, pgbouncer and redis
    container logs, Prometheus / orders-API / events JSON, a rollback, a code fix and a runbook edit."""
    kit = Kit("oncall", seed)
    step, rnd, tool = kit.step, kit.rnd, hp.terminal_result
    roster = pe.PodRoster(kit.rng("roster"))

    # ---- payloads ------------------------------------------------------------------------------
    first_failure = ("WARN", "shop.checkout", "checkout failed order=ORD-4417093 status=504 reason=pool_timeout")
    app = pe.make_log(
        kit.rng("applog"), pe.APP_LOG, n_lines=500, start_sec=2 * 3600 + 11 * 60 + 50, burst=(0.12, 0.9),
        n_errors=26, plants={60: first_failure},
    )
    first_crash = ("FATAL", "shop.worker.main", "worker-3 exiting: too many consecutive failures (k=17) job=6e1f40b2")
    worker = pe.make_log(  # the worker host's app.log, tailed again and again while it grows (450 lines each time)
        kit.rng("worker"), pe.WORKER_LOG, n_lines=1350, start_sec=2 * 3600 + 5 * 60, burst=(0.0445, 0.348),
        burst_rate=0.25, n_errors=70, plants={60: first_crash},
    )
    window = [(0, 450), (180, 630), (360, 810), (540, 990), (720, 1170), (900, 1350)]
    journal1 = pe.make_log(kit.rng("journal1"), pe.JOURNAL_LOG, n_lines=300, start_sec=2 * 3600 + 22 * 60,
                           burst=(0.1, 0.8), n_errors=18, n_notable=10)
    journal2 = pe.make_log(kit.rng("journal2"), pe.JOURNAL_LOG, n_lines=300, start_sec=3 * 3600, n_errors=0)
    pgb = pe.make_log(kit.rng("pgbouncer"), pe.PGBOUNCER_LOG, n_lines=400, start_sec=2 * 3600 + 14 * 60,
                      burst=(0.1, 0.7), n_errors=24)
    redis = pe.make_log(kit.rng("redis"), pe.REDIS_LOG, n_lines=300, start_sec=2 * 3600 + 20 * 60,
                        burst=(0.2, 0.8), n_notable=14)
    previous, previous_errors = pe.python_traceback_log(kit.rng("previous"), n_requests=160, start_sec=2 * 3600 + 5 * 60)
    repo_old = pe.repository_source(kit.rng("repo"), patched=False)
    repo_new = pe.repository_source(kit.rng("repo"), patched=True)
    runbook_old = pe.runbook_markdown(kit.rng("runbook"))
    pool_old = "- Slow inventory service:"
    pool_new = (
        "- Connection pool exhaustion: `QueuePool limit of size N overflow 0 reached`, pods OOM killed while requests queue.\n"
        "- Slow inventory service:"
    )
    assert runbook_old.count(pool_old) == 1
    runbook_new = runbook_old.replace(pool_old, pool_new, 1)

    # ---- T1 ------------------------------------------------------------------------------------
    t1 = kit.turn(
        "PagerDuty fired at 02:14 UTC: checkout-api 5xx rate above 5% for five minutes, incident INC-20250314-0417. "
        "I'm on call for it. Start with the pods and the recent events in namespace shop on prod-eu-1.",
        [
            rnd("Listing the pods first.",
                step("terminal", {"command": "kubectl -n shop get pods -o wide"}, tool(roster.get_pods("incident", 20)))),
            rnd("And the latest events.",
                step("terminal", {"command": "kubectl -n shop get events --sort-by=.lastTimestamp | tail -n 38"},
                     tool(pe.kubectl_get_events(kit.rng("events"))))),
        ],
        "Two checkout-api pods are in CrashLoopBackOff, one was OOMKilled and the rest have restarted. The events show "
        "failing liveness probes and OOM kills on node ip-10-0-3-17.",
    )
    kit.fact("R1", "recall", "INC-20250314-0417", t1, "user", True)
    # ---- T2 ------------------------------------------------------------------------------------
    describe_cmd = "kubectl -n shop describe pod checkout-api-7d9f8c6b5d-x2k4q"
    t2 = kit.turn(
        "Describe the pod that keeps crash looping.",
        [rnd("Describing the first crash looping pod.",
             step("terminal", {"command": describe_cmd}, tool(pe.kubectl_describe_pod(kit.rng("describe"), finished="02:11:38"))))],
        "Its last container instance was OOMKilled (exit code 137) against a 1536Mi limit, and the liveness probe has been failing since.",
    )
    kit.fact("R3", "recall", "02:11:38 +0000", t2, "tool", True, "terminal:" + describe_cmd)
    # ---- T3 ------------------------------------------------------------------------------------
    logs_cmd = "kubectl -n shop logs deploy/checkout-api --since=20m --tail=500"
    text, errs = app.window(0, 500)
    t3 = kit.turn(
        "Pull the checkout-api logs for the last 20 minutes.",
        [rnd("Fetching the API logs.", step("terminal", {"command": logs_cmd}, tool(text)))],
        "The log is full of pool timeouts: checkout requests wait for a database connection, fail with 504 and the "
        "queue grows until uvicorn starts shedding load. Redis reads time out too.",
    )
    kit.errors(t3, errs)
    kit.fact("R2", "recall", "ORD-4417093", t3, "tool", True, "terminal:" + logs_cmd)
    # ---- T4 ------------------------------------------------------------------------------------
    kit.turn(
        "What stands out so far?",
        [],
        "Hypothesis H1: the connection pool of checkout-api is exhausted (QueuePool limit of size 38 reached), requests pile "
        "up waiting for a connection and the pods run out of memory. I want the pool config and the worker host next.",
    )
    # ---- T5 ------------------------------------------------------------------------------------
    ls_cmd = "kubectl -n shop exec checkout-api-7d9f8c6b5d-x2k4q -- ls -la /dumps"
    t5 = kit.turn(
        "Before we touch anything: did the pod leave a memory profile?",
        [
            rnd("Looking in the dumps volume of the pod.",
                step("terminal", {"command": ls_cmd},
                     tool(pe.kubectl_exec_ls_dumps(name="memray-checkout-api-20250314T021138Z.bin")))),
            rnd("And the live memory use.",
                step("terminal", {"command": "kubectl -n shop top pods"}, tool(roster.top_pods(kit.rng("top1"), "incident")))),
        ],
        "There is a memray capture and a tracemalloc summary in /dumps, and every checkout-api pod sits near its memory limit.",
    )
    kit.fact("A1", "artifact", "memray-checkout-api-20250314T021138Z.bin", t5, "tool", True, "terminal:" + ls_cmd)
    # ---- T6 (worker log, tail #1) -----------------------------------------------------------------
    tail_cmd = "tail -n 450 /var/log/shop/app.log"
    text, errs = worker.window(*window[0])
    t6 = kit.turn(
        "Check the worker host too: tail the app log on worker-07.",
        [rnd("Tailing the worker log.", step("terminal", {"command": tail_cmd}, tool(text), nth=1))],
        "The workers hit the same pool limit while reconciling holds, then retry, and one of them exits after too many failures.",
    )
    kit.errors(t6, errs)
    kit.fact("R7", "recall", "job=6e1f40b2", t6, "tool", True, "terminal:" + tail_cmd + "#1")
    # ---- T7 (journald) ----------------------------------------------------------------------------
    journal_cmd = "journalctl -u 'shop-worker@*' --since '02:10' --no-pager | tail -n 300"
    text, errs = journal1.window(0, 300)
    t7 = kit.turn(
        "Anything in journald for the worker units?",
        [rnd("Reading the journal of the worker units.", step("terminal", {"command": journal_cmd}, tool(text)))],
        "journald shows the kernel OOM killer taking worker processes and systemd restarting the units in a loop.",
    )
    kit.errors(t7, errs)
    # ---- T8 (config) ------------------------------------------------------------------------------
    cm_cmd = "kubectl -n shop get configmap checkout-api-config -o yaml"
    t8 = kit.turn(
        "What is the connection pool configured as?",
        [rnd("Reading the config map of the API.",
             step("terminal", {"command": cm_cmd},
                  tool(pe.configmap_yaml(kit.rng("configmap"), pool_line="db.pool.maximum-size=38"))))],
        "The pool is small and has no overflow, with a 30 second wait; the holds feature flag is on.",
    )
    kit.fact("R4", "recall", "db.pool.maximum-size=38", t8, "tool", True, "terminal:" + cm_cmd)
    # ---- T9 (Prometheus JSON) ---------------------------------------------------------------------
    prom_cmd = (
        "curl -s 'http://prometheus.monitoring.svc:9090/api/v1/query_range?query=http_request_duration_seconds"
        "{job=\"checkout-api\"}&start=2025-03-14T02:00:00Z&end=2025-03-14T03:00:00Z&step=15s'"
    )
    t9 = kit.turn(
        "Pull p99 latency and the slowest requests from Prometheus for 02:00 to 03:00.",
        [rnd("Querying Prometheus.",
             step("terminal", {"command": prom_cmd},
                  tool(pe.prometheus_range_json(kit.rng("prom1"), trace_id="7c1f09ab42d85e63")), nth=1))],
        "p99 jumped from about 0.2s to over a second at 02:10 and stayed there; one request took more than four seconds.",
    )
    kit.fact("R5", "recall", "7c1f09ab42d85e63", t9, "tool", True, "terminal:" + prom_cmd + "#1")
    # ---- T10 (the decision) ------------------------------------------------------------------------
    t10 = kit.turn(
        "Two options: A is to raise the pool to 80, B is to roll back to the previous release v2025.03.13-2. Go with B, "
        "and do not restart redis-cart while the sale is running. Once it is stable, ping @dba-oncall in #db-oncall "
        "about the pgbouncer max_client_conn limit.",
        [rnd("Looking at the release history before rolling back.",
             step("terminal", {"command": "helm -n shop history checkout-api"}, tool(pe.helm_history(kit.rng("helm")))))],
        "Decision D-9: roll back instead of raising the pool, because a bigger pool would only hide the leak. Revision 41 "
        "is v2025.03.13-2; I will leave redis-cart alone.",
    )
    kit.fact("D1", "decision", "Decision D-9", t10, "assistant", True)
    kit.fact("D2", "decision", "do not restart redis-cart", t10, "user", True)
    kit.fact("C1", "continuation", "#db-oncall", t10, "user", True)
    # ---- T11 (rollback) ----------------------------------------------------------------------------
    rollback_cmd = "helm -n shop rollback checkout-api 41 --wait --timeout 5m"
    t11 = kit.turn(
        "Do it.",
        [
            rnd("Rolling back to revision 41.",
                step("terminal", {"command": rollback_cmd}, tool("Rollback was a success! Happy Helming!\n"))),
            rnd("Waiting for the rollout.",
                step("terminal", {"command": "kubectl -n shop rollout status deploy/checkout-api --timeout=300s"},
                     tool("\n".join(
                         f'Waiting for deployment "checkout-api" rollout to finish: {i} out of 6 new replicas have been updated...'
                         for i in range(1, 6)) + '\ndeployment "checkout-api" successfully rolled out\n'))),
        ],
        "The rollback finished and the rollout is complete.",
    )
    kit.fact("A2", "artifact", "checkout-api 41", t11, "assistant", True)
    # ---- T12 (pods) ----------------------------------------------------------------------------------
    kit.turn(
        "Show me the pods again.",
        [rnd("Listing the pods.",
             step("terminal", {"command": "kubectl -n shop get pods -o wide"}, tool(roster.get_pods("rolling", 38)), nth=2))],
        "The old pods are terminating and the new ones are coming up.",
    )
    # ---- T13 (worker log, tail #2) ---------------------------------------------------------------------
    text, errs = worker.window(*window[1])
    t13 = kit.turn(
        "Tail the app log on worker-07 again.",
        [rnd("Tailing the worker log again.", step("terminal", {"command": tail_cmd}, tool(text), nth=2))],
        "The error rate on the workers is falling since the rollback.",
    )
    kit.errors(t13, errs)
    # ---- T14 (orders JSON) -----------------------------------------------------------------------------
    orders_cmd = "curl -s 'http://orders-admin.shop.svc:8080/api/v2/orders?status=failed&since=2025-03-14T02:14:00Z&limit=220'"
    t14 = kit.turn(
        "How many orders failed? Pull the failed orders since 02:14.",
        [rnd("Querying the orders admin API.",
             step("terminal", {"command": orders_cmd},
                  tool(pe.orders_api_json(kit.rng("orders"), total_note="total failed orders: 1873 (02:14-02:58 UTC)"))))],
        "The API returned the first page of failed orders, mostly payment and pool timeouts; the summary at the end has the total.",
    )
    kit.fact("R6", "recall", "total failed orders: 1873", t14, "tool", True, "terminal:" + orders_cmd)
    # ---- T15 (pgbouncer) -------------------------------------------------------------------------------
    pgb_cmd = "docker logs --tail 400 pgbouncer"
    text, errs = pgb.window(0, 400)
    t15 = kit.turn(
        "pgbouncer runs in a container on this host: any client rejections?",
        [rnd("Reading the pgbouncer container log.", step("terminal", {"command": pgb_cmd}, tool(text)))],
        "pgbouncer rejected clients with 'no more connections allowed' while the API held its connections.",
    )
    kit.errors(t15, errs)
    # ---- T16 (feature flag) ------------------------------------------------------------------------------
    t16 = kit.turn(
        "Keep the holds_v2 flag off until the fix is reviewed. Show me the flags.",
        [rnd("Reading the feature flag config map.",
             step("terminal", {"command": "kubectl -n shop get configmap feature-flags -o yaml"},
                  tool("apiVersion: v1\ndata:\n  flags.yaml: |\n    holds_v2: true\n    new_checkout_flow: false\n"
                       "    price_quotes_cache: true\n    stripe_retries_v2: false\n    search_rerank: true\n"
                       "kind: ConfigMap\nmetadata:\n  name: feature-flags\n  namespace: shop\n")))],
        "holds_v2 is still on in the config map; I have not changed it.",
    )
    kit.fact("D3", "decision", "Keep the holds_v2 flag off", t16, "user", True)
    # ---- T17 (pods) ----------------------------------------------------------------------------------------
    kit.turn(
        "Pods again.",
        [rnd("Listing the pods.",
             step("terminal", {"command": "kubectl -n shop get pods -o wide"}, tool(roster.get_pods("recovering", 44)), nth=3))],
        "Five of six new pods are ready.",
    )
    # ---- T18 (worker log, tail #3) -------------------------------------------------------------------------
    text, errs = worker.window(*window[2])
    t18 = kit.turn(
        "Tail the worker log again.",
        [rnd("Tailing the worker log.", step("terminal", {"command": tail_cmd}, tool(text), nth=3))],
        "Only a few stragglers are left in the worker log.",
    )
    kit.errors(t18, errs)
    # ---- T19 (Prometheus again) -------------------------------------------------------------------------------
    kit.turn(
        "Re-pull the latency graph so we can see the recovery.",
        [rnd("Querying Prometheus again.",
             step("terminal", {"command": prom_cmd},
                  tool(pe.prometheus_range_json(kit.rng("prom2"), trace_id=None)), nth=2))],
        "p99 is back under 0.3 seconds since the rollout finished.",
    )
    # ---- T20 (events JSON) ------------------------------------------------------------------------------------
    kit.turn(
        "Dump the warning events as JSON for the postmortem.",
        [rnd("Exporting the warning events.",
             step("terminal", {"command": "kubectl -n shop get events --field-selector type=Warning -o json"},
                  tool(pe.kubectl_events_json(kit.rng("eventsjson")))))],
        "Exported 36 warning events: probe failures, back-offs and OOM kills, all before the rollout.",
    )
    # ---- T21 (previous container) -------------------------------------------------------------------------------
    prev_cmd = "kubectl -n shop logs checkout-api-7d9f8c6b5d-x2k4q --previous --tail=400"
    t21 = kit.turn(
        "Get the logs of the container that crashed, for the postmortem.",
        [rnd("Fetching the previous container's log.", step("terminal", {"command": prev_cmd}, tool(previous)))],
        "The crashed container logged the pool timeout tracebacks and was killed while shedding load.",
    )
    kit.errors(t21, previous_errors)
    # ---- T22 (redis) -------------------------------------------------------------------------------------------------
    redis_cmd = "docker logs --since 15m redis-cart"
    text, errs = redis.window(0, 300)
    t22 = kit.turn(
        "Check the redis-cart log as well.",
        [rnd("Reading the redis container log.", step("terminal", {"command": redis_cmd}, tool(text)))],
        "redis closed some clients for output buffer overruns during the peak and had trouble forking to save; nothing since the rollback.",
    )
    kit.errors(t22, errs)
    # ---- T23 (pods) ----------------------------------------------------------------------------------------------------
    kit.turn(
        "Pods.",
        [rnd("Listing the pods.",
             step("terminal", {"command": "kubectl -n shop get pods -o wide"}, tool(roster.get_pods("stable", 58)), nth=4))],
        "All six pods are ready and have not restarted.",
    )
    # ---- T24 (worker log, tail #4) -------------------------------------------------------------------------------------
    text, errs = worker.window(*window[3])
    t24 = kit.turn(
        "Tail the worker log once more.",
        [rnd("Tailing the worker log.", step("terminal", {"command": tail_cmd}, tool(text), nth=4))],
        "The worker log is clean.",
    )
    kit.errors(t24, errs)
    # ---- T25 (severity, next check) --------------------------------------------------------------------------------------
    t25 = kit.turn(
        "Keep this at SEV-2, no escalation. And write down when we look at it next.",
        [],
        "Decision D-11: the incident stays SEV-2. TODO(next): re-check the 5xx rate at 03:30 UTC and close the page if it stays under 1%.",
    )
    kit.fact("D4", "decision", "Decision D-11", t25, "assistant", True)
    kit.fact("C2", "continuation", "re-check the 5xx rate at 03:30 UTC", t25, "assistant", True)
    # ---- T26 (git) ----------------------------------------------------------------------------------------------------------
    kit.turn(
        "Who changed the holds query recently? I suspect the release that went out at 01:52.",
        [
            rnd("Looking at the history of the repository module.",
                step("terminal", {"command": "git -C /srv/shop log --oneline -15 -- src/shop/orders/repository.py"},
                     tool(pe.git_log_oneline(kit.rng("gitlog"), 15, plant={2: "a41c9e7 holds: add find_open_holds with state filter (#2291)"})))),
            rnd("And the stat of the suspect commit.",
                step("terminal", {"command": "git -C /srv/shop show a41c9e7 --stat"},
                     tool("commit a41c9e7d03b6f5a1c2e8d94b7f60a3e15c8d2b90\nAuthor: Mika Tanaka <dev@shop.example>\n"
                          "Date:   Thu Mar 13 16:41:07 2025 +0000\n\n    holds: add find_open_holds with state filter (#2291)\n\n"
                          " src/shop/orders/api.py        |  9 ++++++---\n src/shop/orders/repository.py | 31 +++++++++++++++++++++++++++++++\n"
                          " tests/test_repository.py      | 22 ++++++++++++++++++++++\n 3 files changed, 59 insertions(+), 3 deletions(-)\n"))),
        ],
        "The release added find_open_holds yesterday afternoon, and the new holds_v2 path calls it on every checkout.",
    )
    # ---- T27 (read) ----------------------------------------------------------------------------------------------------------
    kit.turn(
        "Show me find_open_holds.",
        [rnd("Reading the repository module.",
             step("read_file", {"path": "src/shop/orders/repository.py"}, hp.read_file_result(repo_old), nth=1))],
        "find_open_holds takes a connection from the pool and only closes it on the happy path; any error in the query leaks it.",
    )
    # ---- T28 (patch + re-read) ------------------------------------------------------------------------------------------------
    t28 = kit.turn(
        "Fix the leak on a branch, but do not deploy anything yet.",
        [
            rnd("Making the connection a context manager so it always goes back to the pool.",
                step("patch", {"path": "src/shop/orders/repository.py", "old_string": pe.FIND_OPEN_HOLDS_LEAKY,
                               "new_string": pe.FIND_OPEN_HOLDS_FIXED},
                     hp.patch_result(pe.simple_diff("src/shop/orders/repository.py", pe.FIND_OPEN_HOLDS_LEAKY, pe.FIND_OPEN_HOLDS_FIXED),
                                     ["src/shop/orders/repository.py"]))),
            rnd("Re-reading the module to confirm.",
                step("read_file", {"path": "src/shop/orders/repository.py"}, hp.read_file_result(repo_new), nth=2)),
        ],
        "The connection is now taken in a with-block, so it returns to the pool on every path. Nothing is deployed.",
    )
    kit.fact("A3", "artifact", "src/shop/orders/repository.py", t28, "assistant", True)
    # ---- T29 (tests) -----------------------------------------------------------------------------------------------------------
    pytest_cmd = "cd /srv/shop && pytest -q tests/"
    t29 = kit.turn(
        "Run the repo tests.",
        [rnd("Running the suite.",
             step("terminal", {"command": pytest_cmd}, tool(pe.pytest_run(kit.rng("pytest"), passed=164, seconds=11.3)[0])))],
        "The whole suite is green with the fix.",
    )
    kit.fact("R8", "recall", "164 passed in 11.30s", t29, "tool", True, "terminal:" + pytest_cmd)
    # ---- T30 (pods + top) ---------------------------------------------------------------------------------------------------------
    kit.turn(
        "Pods and memory.",
        [
            rnd("Listing the pods.",
                step("terminal", {"command": "kubectl -n shop get pods -o wide"}, tool(roster.get_pods("stable", 64)), nth=5)),
            rnd("And memory.",
                step("terminal", {"command": "kubectl -n shop top pods"}, tool(roster.top_pods(kit.rng("top2"), "stable")), nth=2)),
        ],
        "The pods are healthy and memory is well under the limit.",
    )
    # ---- T31 (worker log, tail #5) --------------------------------------------------------------------------------------------------
    text, errs = worker.window(*window[4])
    t31 = kit.turn(
        "Tail the worker log.",
        [rnd("Tailing the worker log.", step("terminal", {"command": tail_cmd}, tool(text), nth=5))],
        "Still clean.",
    )
    kit.errors(t31, errs)
    # ---- T32 (jq) ------------------------------------------------------------------------------------------------------------------------
    kit.turn(
        "Give me the pod inventory as JSON with restarts and last termination reasons.",
        [rnd("Querying the pods as JSON.",
             step("terminal", {"command": "kubectl -n shop get pods -o json | jq '[.items[] | {name: .metadata.name, node: .spec.nodeName, restarts: .status.containerStatuses[0].restartCount, last: .status.containerStatuses[0].lastState}]'"},
                  tool(roster.jq_pods(kit.rng("jqpods")))))],
        "Every checkout-api pod of the old ReplicaSet restarted between three and nine times before the rollback.",
    )
    # ---- T33 (postmortem due, error budget) -------------------------------------------------------------------------------------------------
    slo_cmd = "curl -s http://slo.monitoring.svc:8080/api/v1/slos/checkout-availability"
    t33 = kit.turn(
        "Reminder for the notes: the postmortem is due Monday 17 March. How much of the error budget did this burn?",
        [rnd("Asking the SLO service.",
             step("terminal", {"command": slo_cmd}, tool(pe.slo_json(kit.rng("slo"), note="this incident burned 4.69% of the monthly error budget"))))],
        "The SLO service has the numbers; the budget is fine for the rest of the month.",
    )
    kit.fact("C3", "continuation", "due Monday 17 March", t33, "user", True)
    kit.fact("R9", "recall", "burned 4.69% of the monthly error budget", t33, "tool", True, "terminal:" + slo_cmd)
    # ---- T34 (runbook) ---------------------------------------------------------------------------------------------------------------------------
    t34 = kit.turn(
        "Add the pool exhaustion case to the runbook.",
        [
            rnd("Reading the runbook.",
                step("read_file", {"path": "runbooks/checkout-5xx.md"}, hp.read_file_result(runbook_old), nth=1)),
            rnd("Adding the section under known causes.",
                step("patch", {"path": "runbooks/checkout-5xx.md", "old_string": pool_old, "new_string": pool_new},
                     hp.patch_result(pe.simple_diff("runbooks/checkout-5xx.md", pool_old, pool_new), ["runbooks/checkout-5xx.md"]))),
            rnd("Re-reading it.",
                step("read_file", {"path": "runbooks/checkout-5xx.md"}, hp.read_file_result(runbook_new), nth=2)),
        ],
        "The runbook has a pool exhaustion entry now.",
    )
    kit.fact("A4", "artifact", "runbooks/checkout-5xx.md", t34, "assistant", False)
    # ---- T35 (journal #2) --------------------------------------------------------------------------------------------------------------------------
    text, errs = journal2.window(0, 300)
    t35 = kit.turn(
        "Last look at journald for worker 3 since 03:00.",
        [rnd("Reading the journal of worker 3.",
             step("terminal", {"command": "journalctl -u shop-worker@3 --since 03:00 --no-pager | tail -n 300"}, tool(text)))],
        "Worker 3 has been up since the rollback with no errors.",
    )
    kit.errors(t35, errs)
    # ---- T36 (worker log, tail #6) --------------------------------------------------------------------------------------------------------------------
    text, errs = worker.window(*window[5])
    t36 = kit.turn(
        "And the worker log one last time.",
        [rnd("Tailing the worker log.", step("terminal", {"command": tail_cmd}, tool(text), nth=6))],
        "Clean.",
    )
    kit.errors(t36, errs)
    # ---- T37 (pods) --------------------------------------------------------------------------------------------------------------------------------------
    kit.turn(
        "Pods.",
        [rnd("Listing the pods.",
             step("terminal", {"command": "kubectl -n shop get pods -o wide"}, tool(roster.get_pods("stable", 80)), nth=6))],
        "All healthy.",
    )
    # ---- T38 ----------------------------------------------------------------------------------------------------------------------------------------------
    kit.turn("I think it is stable.", [], "Agreed: no restarts, latency normal, the worker and journal logs are clean.")
    # ---- T39 (autoscaling state) ----------------------------------------------------------------------------------------------------------------------------
    kit.turn(
        "Is the autoscaler behaving?",
        [
            rnd("Checking the HPA.",
                step("terminal", {"command": "kubectl -n shop get hpa"},
                     tool("NAME           REFERENCE                 TARGETS   MINPODS   MAXPODS   REPLICAS   AGE\n"
                          "checkout-api   Deployment/checkout-api   31%/70%   6         12        6          212d\n"
                          "orders-worker  Deployment/orders-worker  22%/70%   3         6         3          212d\n"))),
            rnd("And the disruption budgets.",
                step("terminal", {"command": "kubectl -n shop get pdb"},
                     tool("NAME           MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE\n"
                          "checkout-api   4               N/A               2                     212d\n"
                          "cart-redis     2               N/A               1                     212d\n"))),
        ],
        "The HPA is at 31% of its CPU target with six replicas, and both budgets allow disruptions again.",
    )
    return kit.finish(ONCALL_FINAL_QUESTION, turns, natural=ONCALL_TURNS, log_turn=t3)


EXTRA_SPECS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        "oncall", build_oncall, ONCALL_TURNS, 4,
        "40 turns, log heavy: kubectl / journalctl / docker logs, one log tailed six times while it grows, large JSON dumps",
    ),
)
