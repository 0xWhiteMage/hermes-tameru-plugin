"""The six held-out replay scenarios (frozen before candidate-informed tuning).

* ``longform``     qualified-policy research: dense prose files where qualifiers, negations and
                   numbered clauses are the required facts.
* ``mixedlang``    mixed-language incident triage: English asks; logs and reports in Japanese,
                   German and English; ids and error codes cross languages.
* ``diagnostics``  structured findings: JSON/SARIF-like reports where a finding and its rule id,
                   file and line must stay associated; versions and hashes as gold.
* ``rereads``      supersession: the same files re-read with newer content later; gold from both
                   early and late versions.
* ``bursty``       alternating tiny and huge tool outputs; gold facts in both sizes.
* ``dense``        medium outputs each carrying several required literals: SHAs, URLs, config
                   keys, numeric thresholds, exact error strings.
"""
from __future__ import annotations

import json

from fixtures import hermes_payloads as hp
from scenario import Scenario
from scenario_kit import Kit, ScenarioSpec

# =================================================================================================
# longform — qualified policy research
# =================================================================================================
LONGFORM_TURNS = 26
LONGFORM_FINAL = (
    "Summarise the retention policy for the legal review: the default retention days, the two "
    "explicit exemptions, what is NOT covered, the review board quorum, the effective date, and "
    "which clause number authorises early deletion."
)

_CLAUSES = (
    ("3.1", "Records older than {days} days MAY be archived; archiving does NOT imply deletion."),
    ("3.2", "Deletion requests MUST NOT be processed for accounts under active litigation hold."),
    ("3.3", "The {board} review board requires a quorum of {quorum} members to approve early deletion."),
    ("4.1", "This policy does NOT cover encrypted backups, which follow the separate crypto schedule."),
    ("4.2", "Clause {auth} authorises early deletion when a signed officer order exists."),
)


def build_longform(seed: int = 0, turns: int = LONGFORM_TURNS) -> Scenario:
    kit = Kit("longform", seed)
    rng = kit.rng("policy")
    days = rng.choice([365, 730, 1095])
    quorum = rng.choice([3, 5])
    board = rng.choice(["Compliance", "Governance"])
    auth = rng.choice(["7.4", "7.6"])
    date = rng.choice(["2026-03-01", "2026-04-15"])
    exempts = rng.sample(["legal-hold accounts", "financial-year exports", "incident evidence"], 2)
    step, rnd, cat = kit.step, kit.rnd, hp.read_file_result

    kit.turn(
        "Read the retention policy draft and the legal annotations.",
        [rnd("Reading the policy draft and annotations.",
             step("read_file", {"path": "/srv/legal/retention-policy.md"}, cat(
                 f"# Data Retention Policy\nEffective {date}\n\n"
                 f"Default retention: {days} days.\n"
                 f"Exemptions: {exempts[0]}; {exempts[1]}.\n"
                 + "\n".join(t.format(days=days, board=board, quorum=quorum, auth=auth)
                              for _, t in _CLAUSES))),
             step("read_file", {"path": "/srv/legal/annotations.md"}, cat(
                 f"Counsel notes: clause {auth} is the operative early-deletion authority; "
                 f"the {board} board quorum of {quorum} was raised from 2 last cycle.")))],
        "Both files are loaded; the qualifiers matter — note what is and is not covered.",
    )
    kit.fact("L1", "recall", f"Default retention: {days} days", 1, "tool", True,
             call_key="read_file:/srv/legal/retention-policy.md")
    kit.fact("L2", "recall", f"quorum of {quorum} members", 1, "tool", True,
             call_key="read_file:/srv/legal/retention-policy.md")
    kit.fact("L3", "decision", f"Clause {auth} authorises early deletion", 1, "tool", True,
             call_key="read_file:/srv/legal/retention-policy.md")
    kit.fact("L4", "recall", f"Effective {date}", 1, "tool", True,
             call_key="read_file:/srv/legal/retention-policy.md")
    kit.fact("L5", "recall", "does NOT cover encrypted backups", 1, "tool", True,
             call_key="read_file:/srv/legal/retention-policy.md")

    for i in range(2, LONGFORM_TURNS):
        topic = ["cross-border", "vendor", "employee", "marketing", "analytics"][i % 5]
        doc = (f"# {topic.title()} data annex\n"
               f"Annex {i}: {topic} data follows the master policy of {days} days unless the "
               f"{board} board approves a shorter schedule by quorum of {quorum}.\n"
               f"Annex note {i}: this annex MAY reference clause {auth} for early deletion.\n"
               + "\n".join(f"Provision {i}.{j}: section text for {topic} item {j}.\n" for j in range(14)))
        kit.turn(
            f"Check the {topic} annex (round {i}).",
            [rnd(f"Annex {i} loaded.",
                  step("read_file", {"path": f"/srv/legal/annex-{i}-{topic}.md"}, cat(doc)))],
            f"Annex {i} reviewed; it defers to the master policy.",
        )
        if i in (4, 9, 15):
            kit.fact(f"L{i+2}", "recall", f"Annex note {i}: this annex MAY reference clause {auth}",
                     i, "tool", True, call_key=f"read_file:/srv/legal/annex-{i}-{topic}.md")
    return kit.finish(LONGFORM_FINAL, turns, natural=LONGFORM_TURNS, log_turn=LONGFORM_TURNS - 1)


# =================================================================================================
# mixedlang — mixed-language incident triage
# =================================================================================================
MIXEDLANG_TURNS = 26
MIXEDLANG_FINAL = (
    "Write the incident summary: which pods OOMKilled, the German deployer's rollback verdict, "
    "the Japanese alert code, the fix SHA, and which queue is backing up."
)


def build_mixedlang(seed: int = 0, turns: int = MIXEDLANG_TURNS) -> Scenario:
    kit = Kit("mixedlang", seed)
    rng = kit.rng("inc")
    pod = rng.choice(["payments-api", "checkout-web", "inventory-svc"])
    code = rng.choice(["JP-4501", "JP-4502"])
    sha = rng.choice(["9f31d2c", "ab77e01"])
    queue = rng.choice(["refund-queue", "notify-queue"])
    verdict = rng.choice(["Rollback erforderlich", "Rollback nicht erforderlich"])
    step, rnd = kit.step, kit.rnd

    for i in range(1, MIXEDLANG_TURNS):
        if i == 1:
            out = (f"[ja] アラート {code}: Pod {pod} が OOMKilled されました\n"
                   f"[ja] キュー {queue} の滞留が増加しています\n"
                   f"[en] pod {pod} restart count 7\n"
                   f"[en] ERROR {code} pod {pod} OOMKilled\n"
                   + "\n".join(f"[ja] 情報行 {j}: 通常メトリクス" for j in range(30)))
            kit.turn("Pull the alert feed for the payments outage.",
                     [rnd("Alert feed (Japanese).", step("terminal", {"command": "alerts --tail"},
                                                        hp.terminal_result(out)))],
                     "Japanese alerts loaded.")
            kit.fact("M1", "recall", f"アラート {code}", 1, "tool", True, call_key="terminal:alerts --tail")
            kit.fact("M2", "recall", f"Pod {pod} が OOMKilled", 1, "tool", True, call_key="terminal:alerts --tail")
            kit.fact("M3", "recall", f"キュー {queue} の滞留", 1, "tool", True, call_key="terminal:alerts --tail")
            kit.errors(1, [f"ERROR {code} pod {pod} OOMKilled"])
        elif i == 2:
            out = (f"[de] Deployer-Bericht: {verdict} für {pod}\n"
                   f"[de] Fix-Commit {sha} wartet auf Review\n"
                   + "\n".join(f"[de] Protokollzeile {j}: unauffällig" for j in range(30)))
            kit.turn("Check the deployer report.",
                     [rnd("Deployer report (German).", step("terminal", {"command": "deployer --report"},
                                                           hp.terminal_result(out)))],
                     "German report loaded.")
            kit.fact("M4", "recall", verdict, 2, "tool", True, call_key="terminal:deployer --report")
            kit.fact("M5", "recall", f"Fix-Commit {sha}", 2, "tool", True, call_key="terminal:deployer --report")
        else:
            svc = ["billing", "search", "auth", "notify"][i % 4]
            out = (f"[en] {svc} heartbeat ok\n[ja] {svc} 状態正常\n[de] {svc} Status nominal\n"
                   + "\n".join(f"[en] {svc} metric row {j}" for j in range(18)))
            kit.turn(f"Check {svc} status.",
                     [rnd(f"{svc} status.", step("terminal", {"command": f"status {svc}"},
                                                hp.terminal_result(out)))],
                     f"{svc} nominal.")
    return kit.finish(MIXEDLANG_FINAL, turns, natural=MIXEDLANG_TURNS, log_turn=MIXEDLANG_TURNS - 1)


# =================================================================================================
# diagnostics — structured finding reports
# =================================================================================================
DIAG_TURNS = 24
DIAG_FINAL = (
    "Tally the findings: the rule ids and their file:line pairs, the scanner version, the report "
    "hash, and which rule has no autofix."
)


def build_diagnostics(seed: int = 0, turns: int = DIAG_TURNS) -> Scenario:
    kit = Kit("diagnostics", seed)
    rng = kit.rng("sarif")
    version = rng.choice(["4.2.1", "4.3.0"])
    digest = rng.choice(["b7f2a91c", "55d04ea2"])
    rules = [
        (rng.choice(["PY201", "PY202"]), rng.choice(["orders/service.py", "cart/items.py"]), rng.choice([88, 142])),
        (rng.choice(["SEC117", "SEC118"]), rng.choice(["auth/tokens.py", "billing/charge.py"]), rng.choice([31, 64])),
        (rng.choice(["PERF9", "PERF11"]), rng.choice(["search/index.py", "catalog/query.py"]), rng.choice([210, 377])),
    ]
    step, rnd = kit.step, kit.rnd
    err_lines = [f"ERROR rule {rules[0][0]} at {rules[0][1]}:{rules[0][2]}",
                 f"ERROR rule {rules[2][0]} at {rules[2][1]}:{rules[2][2]}"]
    report = {
        "version": version, "reportHash": digest,
        "errors": err_lines,
        "runs": [{"tool": {"driver": {"rules": [
            {"id": rid, "file": path, "line": line,
             "location": f"{path}:{line}", "autofix": rid != rules[2][0]}
            for rid, path, line in rules]}}}],
    }
    kit.turn(
        "Run the static analysis and pull the JSON report.",
        [rnd("Report ready.", step("terminal", {"command": "scanner --json"},
                                   hp.terminal_result(json.dumps(report, indent=2))))],
        "Structured report loaded.",
    )
    kit.fact("D1", "artifact", digest, 1, "tool", True, call_key="terminal:scanner --json")
    kit.fact("D2", "artifact", version, 1, "tool", True, call_key="terminal:scanner --json")
    for n, (rid, path, line) in enumerate(rules):
        kit.fact(f"D{n+3}", "artifact", rid, 1, "tool", True, call_key="terminal:scanner --json")
        kit.fact(f"F{n+1}", "artifact", path, 1, "tool", True, call_key="terminal:scanner --json")
        kit.fact(f"N{n+1}", "artifact", f"{path}:{line}", 1, "tool", True,
                 call_key="terminal:scanner --json")
    kit.errors(1, err_lines)
    for i in range(2, DIAG_TURNS):
        mod = ["payments", "shipping", "inventory", "notify"][i % 4]
        extra = [{"id": f"MOD{i}", "file": f"{mod}/core.py", "line": 10 + i,
                  "location": f"{mod}/core.py:{10 + i}", "autofix": True}]
        rep = {"reportHash": f"modhash-{i:03d}", "runs": [
            {"tool": {"driver": {"rules": extra}}}]}
        kit.turn(f"Scan the {mod} module.",
                 [rnd(f"{mod} scan.", step("terminal", {"command": f"scanner --json {mod}"},
                                          hp.terminal_result(json.dumps(rep, indent=2))))],
                 f"{mod} findings loaded.")
        if i in (5, 11, 17):
            kit.fact(f"DG{i}", "artifact", f"MOD{i}", i, "tool", True,
                     call_key=f"terminal:scanner --json {mod}")
    return kit.finish(DIAG_FINAL, turns, natural=DIAG_TURNS, log_turn=DIAG_TURNS - 1)


# =================================================================================================
# rereads — supersession of the same paths
# =================================================================================================
REREADS_TURNS = 24
REREADS_FINAL = (
    "Give the deploy checklist: the ORIGINAL freeze date, the FINAL freeze date, the original owner, "
    "the current owner, the first checklist item, and the last one."
)


def build_rereads(seed: int = 0, turns: int = REREADS_TURNS) -> Scenario:
    kit = Kit("rereads", seed)
    rng = kit.rng("deploy")
    freeze_a, freeze_b = rng.sample(["2026-05-01", "2026-05-08", "2026-05-15"], 2)
    owner_a, owner_b = rng.sample(["priya", "mateo", "suki"], 2)
    item_a = rng.choice(["backup verify", "canary 2%"])
    item_b = rng.choice(["dns warm", "rollback drill"])
    step, rnd, cat = kit.step, kit.rnd, hp.read_file_result
    path = "/srv/ops/deploy-checklist.md"

    kit.turn("Read the deploy checklist.",
             [rnd("v1 loaded.", step("read_file", {"path": path}, cat(
                 f"# Deploy Checklist v1\nFreeze: {freeze_a}\nOwner: {owner_a}\n1. {item_a}\n"
                 + "\n".join(f"{j}. filler item {j}" for j in range(2, 20)))))],
             "Checklist v1 noted.")
    kit.fact("V1", "recall", f"Freeze: {freeze_a}", 1, "tool", True, call_key=f"read_file:{path}")
    kit.fact("V2", "recall", f"Owner: {owner_a}", 1, "tool", True, call_key=f"read_file:{path}")
    kit.fact("V3", "recall", f"1. {item_a}", 1, "tool", True, call_key=f"read_file:{path}")

    for i in range(2, REREADS_TURNS - 1):
        kit.turn(f"Continue the ops review (page {i}).",
                 [rnd(f"Ops page {i}.", step("terminal", {"command": f"ops notes --page {i}"},
                                            hp.terminal_result(
                                                "\n".join(f"ops note {i}.{j}" for j in range(20)))))],
                 f"Page {i} noted.")

    kit.turn("Re-read the checklist — it was updated.",
             [rnd("v2 loaded.", step("read_file", {"path": path}, cat(
                 f"# Deploy Checklist v2\nFreeze: {freeze_b}\nOwner: {owner_b}\n"
                 f"{len(_CLAUSES) + 18}. {item_b}\n" +
                 "\n".join(f"{j}. filler item {j}" for j in range(2, 20))), nth=2))],
             "Checklist v2 noted — freeze and owner changed.")
    kit.fact("V4", "recall", f"Freeze: {freeze_b}", REREADS_TURNS - 1, "tool", True,
             call_key=f"read_file:{path}#2")
    kit.fact("V5", "recall", f"Owner: {owner_b}", REREADS_TURNS - 1, "tool", True,
             call_key=f"read_file:{path}#2")
    kit.fact("V6", "recall", item_b, REREADS_TURNS - 1, "tool", True, call_key=f"read_file:{path}#2")
    return kit.finish(REREADS_FINAL, turns, natural=REREADS_TURNS, log_turn=REREADS_TURNS - 2)


# =================================================================================================
# bursty — tiny + huge outputs alternating
# =================================================================================================
BURSTY_TURNS = 26
BURSTY_FINAL = (
    "Report the burst audit: the tiny-output token counts, the big log's OOM line, the seed value, "
    "and the checksum."
)


def build_bursty(seed: int = 0, turns: int = BURSTY_TURNS) -> Scenario:
    kit = Kit("bursty", seed)
    rng = kit.rng("burst")
    seedv = rng.choice(["18446744073709551", "424242"])
    checksum = rng.choice(["sha256:9be3f1", "sha256:77aa02"])
    oom = rng.choice(["java.lang.OutOfMemoryError: GC overhead", "OOM in worker-7"])
    step, rnd = kit.step, kit.rnd
    for i in range(1, BURSTY_TURNS):
        tiny = f"tiny-{i} count={rng.randint(2, 9)}"
        huge = (f"chunk {i} of trace\n" + "\n".join(
            f"trace {i}.{j} " + "x" * 60 for j in range(400)))
        if i == 3:
            huge = f"seed={seedv}\nchecksum {checksum}\nERROR {oom}\n" + huge
        kit.turn(f"Run burst probe {i}.",
                 [rnd(f"Probe {i} (tiny then huge).",
                      step("terminal", {"command": f"probe --tiny {i}"}, hp.terminal_result(tiny)),
                      step("terminal", {"command": f"probe --trace {i}"}, hp.terminal_result(huge)))],
                 f"Probe {i} done.")
        if i == 3:
            kit.fact("B1", "recall", f"seed={seedv}", 3, "tool", True,
                     call_key="terminal:probe --trace 3")
            kit.fact("B2", "recall", f"checksum {checksum}", 3, "tool", True,
                     call_key="terminal:probe --trace 3")
            kit.errors(3, [f"ERROR {oom}"])
            kit.fact("B3", "recall", oom, 3, "tool", True, call_key="terminal:probe --trace 3")
        if i == 7:
            kit.fact("B4", "recall", tiny, 7, "tool", True, call_key="terminal:probe --tiny 7")
    return kit.finish(BURSTY_FINAL, turns, natural=BURSTY_TURNS, log_turn=BURSTY_TURNS - 1)


# =================================================================================================
# dense — many literals per row
# =================================================================================================
DENSE_TURNS = 24
DENSE_FINAL = (
    "Consolidate the service matrix: each service's SHA, base URL, config key, and its numeric "
    "limit or exact error string."
)

_SERVICES = ("auth", "billing", "catalog", "cart", "checkout", "inventory", "ledger",
             "orders", "payments", "search", "shipping", "tax")


def build_dense(seed: int = 0, turns: int = DENSE_TURNS) -> Scenario:
    kit = Kit("dense", seed)
    rng = kit.rng("matrix")
    step, rnd = kit.step, kit.rnd
    facts = []
    for i in range(1, DENSE_TURNS):
        svc = _SERVICES[(i - 1) % len(_SERVICES)]
        sha = f"{rng.randrange(16**7):07x}"
        url = f"https://{svc}.internal:8{100 + i}"
        key = f"{svc}.rate.limit"
        limit = rng.choice([250, 500, 1000]) + i
        err = rng.choice(["ERR_CONN_RESET", "ERR_UPSTREAM_503", "ERR_TLS_HANDSHAKE"])
        out = (f"service={svc}\nsha={sha}\nurl={url}\nconfig_key={key}\n"
               f"limit={limit}\nlast_error={err}\nERROR {err} service={svc}\n"
               + "\n".join(f"field{j}=v{j}" for j in range(12)))
        kit.turn(f"Inspect {svc} config.",
                 [rnd(f"{svc} config.", step("terminal", {"command": f"inspect {svc}"},
                                            hp.terminal_result(out)))],
                 f"{svc} inspected.")
        k = f"terminal:inspect {svc}"
        kit.fact(f"S{i}a", "artifact", f"sha={sha}", i, "tool", True, call_key=k)
        kit.fact(f"S{i}b", "artifact", f"url={url}", i, "tool", True, call_key=k)
        if i in (2, 6, 10, 14, 18, 22):
            kit.fact(f"S{i}c", "artifact", f"limit={limit}", i, "tool", True, call_key=k)
            kit.fact(f"S{i}d", "artifact", f"last_error={err}", i, "tool", True, call_key=k)
            kit.errors(i, [f"ERROR {err} service={svc}"])
        facts.append((svc, sha, url, key))
    return kit.finish(DENSE_FINAL, turns, natural=DENSE_TURNS, log_turn=DENSE_TURNS - 1)


HOLDOUT_SPECS = (
    ScenarioSpec("longform", build_longform, LONGFORM_TURNS, 4,
                 "qualified policy prose; negations and clause numbers as gold"),
    ScenarioSpec("mixedlang", build_mixedlang, MIXEDLANG_TURNS, 4,
                 "English asks, Japanese/German/English sources"),
    ScenarioSpec("diagnostics", build_diagnostics, DIAG_TURNS, 4,
                 "structured finding reports; record associations as gold"),
    ScenarioSpec("rereads", build_rereads, REREADS_TURNS, 4,
                 "same-path supersession; early and late facts"),
    ScenarioSpec("bursty", build_bursty, BURSTY_TURNS, 4,
                 "tiny and huge outputs alternating"),
    ScenarioSpec("dense", build_dense, DENSE_TURNS, 4,
                 "many protected literals per row"),
)
