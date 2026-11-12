"""Four blind (held-out) replay scenarios, written without looking at any product code or result.

* ``blind_migration``  schema migration across many services: medium SQL / migration-log / config-diff outputs;
                       early user rules that must still be honoured late.
* ``blind_perf``       performance investigation: big profiler, flame-graph and benchmark outputs; repeated runs
                       whose numbers change; the conclusion needs two numbers from far apart.
* ``blind_audit``      dependency and security audit: long npm-audit / pip-audit listings, lockfile diffs, a few
                       critical advisories buried in noise, user constraints ("never upgrade X").
* ``blind_support``    customer-support triage: log excerpts, bilingual chat transcripts, JSON ticket exports,
                       a handful of ids / hosts / error codes needed at the end.

Nothing registers at import time; call ``register_blind()`` to add the specs to the scenario registry.
"""
from __future__ import annotations

from dataclasses import dataclass

from fixtures import hermes_payloads as hp
from scenario import Scenario
from scenario_kit import Kit, ScenarioSpec, call_key

_SVC = ("billing", "orders", "inventory", "ledger", "catalog", "cart", "shipping", "notify",
        "search", "auth", "payments", "tax")
_TBL = ("entries", "items", "accounts", "events", "audit_log", "sessions", "prices", "carts",
        "shipments", "refunds")
_HEX = "0123456789abcdef"


def _hx(r, n: int) -> str:
    return "".join(r.choice(_HEX) for _ in range(n))


# -- line generators: (rng, index) -> one routine line ---------------------------------------------
def _sql(r, i):
    s, t, a = r.choice(_SVC), r.choice(_TBL), r.randrange(1, 9_000_000)
    return r.choice((
        f"ALTER TABLE {s}.{t} ADD COLUMN c{i % 97} BIGINT NULL; -- step {i}",
        f"CREATE INDEX CONCURRENTLY idx_{t}_{i % 211} ON {s}.{t} (c{i % 53}, created_at);",
        f"UPDATE {s}.{t} SET c{i % 97} = id WHERE id BETWEEN {a} AND {a + 4999};",
        f"-- verify {s}.{t}: SELECT count(*) FROM {s}.{t} WHERE c{i % 97} IS NULL;",
    ))


def _mig(r, i):
    s, t = r.choice(_SVC), r.choice(_TBL)
    return (f"2026-03-{10 + i % 15:02d} 02:{i % 60:02d}:{r.randrange(60):02d} INFO migrate[{s}] "
            f"applied chunk {i} ({t}) rows={r.randrange(1000, 5000)} in {r.randrange(40, 900)}ms lag={r.randrange(0, 4)}s")


def _cfg(r, i):
    s = r.choice(_SVC)
    k = r.choice(("pool.size", "timeout.ms", "retry.max", "batch.rows", "cache.ttl", "queue.depth"))
    return r.choice((f"- {s}.{k}: {r.randrange(5, 400)}", f"+ {s}.{k}: {r.randrange(5, 400)}",
                     f"  {s}.{k}: {r.randrange(5, 400)}  # unchanged ({i})"))


def _prof(r, i):
    fn = r.choice(("Serializer.write", "Pool.acquire", "Json.parse", "Cache.get", "Router.match", "Tls.read",
                   "Tax.lookup", "Cart.merge", "Index.seek", "Gc.safepoint"))
    return (f"{r.random() * 3:5.2f}%  {r.random() * 90:8.1f}ms  com.shop.{fn}{i % 40}  "
            f"({fn.split('.')[0]}.java:{r.randrange(10, 900)})")


def _bench(r, i):
    n = r.choice(("checkout", "search", "cart_merge", "price_quote", "login", "inventory_hold"))
    return (f"warm-sample {i}: {n} ops/s={r.randrange(800, 4000)} p50={r.randrange(5, 40)}ms "
            f"p95={r.randrange(30, 120)}ms rss={r.randrange(300, 900)}MB")


def _flame(r, i):
    a, b, c = (r.choice(("handler", "filter", "dispatch", "render", "query", "encode", "flush", "decode"))
               for _ in range(3))
    return f"main;server.run;{a}{i % 13};{b}{i % 7};{c}{i % 29} {r.randrange(1, 400)}"


def _npm(r, i):
    p = r.choice(("lodash", "minimist", "semver", "tar", "ws", "glob-parent", "braces", "qs", "yargs",
                  "postcss", "ejs", "nanoid")) + f"-{i % 31}"
    sev = r.choice(("low", "low", "moderate", "moderate", "high"))
    return (f"{p}  <{r.randrange(1, 9)}.{r.randrange(10)}.{r.randrange(20)}  {sev}  "
            f"Regular expression denial of service  https://github.com/advisories/GHSA-{_hx(r, 4)}-{_hx(r, 4)}-{_hx(r, 4)}")


def _lock(r, i):
    p = r.choice(_SVC) + f"-lib-{i % 50}"
    v = f"{r.randrange(1, 9)}.{r.randrange(15)}.{r.randrange(30)}"
    return r.choice((f'-      "version": "{v}",', f'+      "version": "{v}",',
                     f'       "resolved": "https://registry.npmjs.org/{p}/-/{p}-{v}.tgz",',
                     f'       "integrity": "sha512-{_hx(r, 40)}==",'))


def _pip(r, i):
    p = r.choice(("requests", "urllib3", "jinja2", "pyyaml", "pillow", "cryptography", "flask", "numpy",
                  "sqlalchemy", "werkzeug")) + f"-x{i % 17}"
    return (f"{p}  {r.randrange(1, 4)}.{r.randrange(20)}.{r.randrange(9)}  PYSEC-2025-{r.randrange(100, 999)}  "
            f"{r.randrange(2, 5)}.{r.randrange(20)}.{r.randrange(9)}  low-impact parsing issue")


_ES = ("Hola, sigo sin poder pagar el pedido, me aparece un error.", "Gracias por la ayuda, espero su respuesta.",
       "Ya probé con otra tarjeta y sigue igual.", "¿Pueden revisar mi cuenta, por favor?",
       "El problema empezó esta mañana, no antes.")
_EN = ("Hi, the checkout page keeps spinning for me.", "Thanks, I will try again in a few minutes.",
       "I cleared my cache and it did not help.", "Can someone check my account please?",
       "The issue started this morning, not before.")
_AGENT = ("Agent: Thanks for reaching out, I am looking into it.", "Agent: Could you share a screenshot?",
          "Agent: Noted, I have attached the logs to the ticket.", "Agente: Gracias, lo reviso ahora mismo.")


def _chat(r, i):
    who = r.choice(("Cust", "Cust", "Cliente"))
    pool = _ES if who == "Cliente" else _EN
    if r.random() < 0.25:
        return f"[{i % 60:02d}:{r.randrange(60):02d}] {r.choice(_AGENT)}"
    return f"[{i % 60:02d}:{r.randrange(60):02d}] {who}: {r.choice(pool)}"


def _log(r, i):
    h = f"app-{r.choice(('eu', 'us', 'ap'))}-{r.randrange(1, 30)}.prod.example.internal"
    return (f"2026-04-{1 + i % 27:02d}T{i % 24:02d}:{r.randrange(60):02d}:{r.randrange(60):02d}Z {h} "
            f"INFO http {r.choice(('GET', 'POST'))} /api/v{r.randrange(1, 4)}/{r.choice(_TBL)} "
            f"{r.choice((200, 200, 200, 204, 301))} {r.randrange(3, 900)}ms req-{_hx(r, 8)}")


def _json(r, i):
    return ('{"ticket": "TCK-%d", "status": "%s", "priority": "%s", "account": "acct_%s", "channel": "%s", '
            '"subject": "%s", "updated": "2026-04-%02dT%02d:%02d:00Z"}' % (
                r.randrange(10000, 99999), r.choice(("open", "pending", "solved", "solved")),
                r.choice(("P3", "P3", "P2", "P4")), _hx(r, 8), r.choice(("chat", "email", "phone")),
                r.choice(("Refund status", "Cannot log in", "Invoice copy", "Slow app", "Address change")),
                1 + i % 27, i % 24, r.randrange(60)))


_KINDS = {"sql": _sql, "mig": _mig, "cfg": _cfg, "prof": _prof, "bench": _bench, "flame": _flame,
          "npm": _npm, "lock": _lock, "pip": _pip, "chat": _chat, "log": _log, "json": _json}


def _compose(r, kind: str, size: int, plants: tuple[str, ...]) -> str:
    gen = _KINDS[kind]
    budget = size - sum(len(p) + 1 for p in plants)
    lines: list[str] = []
    total = i = 0
    while total < budget:
        line = gen(r, i)
        lines.append(line)
        total += len(line) + 1
        i += 1
    for p in plants:
        lines.insert(r.randrange(len(lines) + 1), p)
    return "\n".join(lines)


@dataclass(frozen=True)
class _T:
    user: str
    cmd: str
    kind: str
    plants: tuple[str, ...] = ()
    final: str = ""
    errs: tuple[str, ...] = ()
    size: int | None = None


def _run(name: str, seed: int, turns: int, natural: int, plan: dict[int, _T], filler, facts, final_q: str,
         min_size: int = 22000, max_size: int = 36000) -> Scenario:
    kit = Kit(name, seed)
    specs: list[_T] = []
    for i in range(1, natural):
        t = plan.get(i) or filler(i, kit.rng(f"filler{i}"))
        specs.append(t)
        r = kit.rng(f"t{i}")
        size = t.size or r.randint(min_size, max_size)
        text = _compose(r, t.kind, size, tuple(t.plants) + tuple(t.errs))
        kit.turn(t.user,
                 [kit.rnd(f"Running: {t.cmd}", kit.step("terminal", {"command": t.cmd}, hp.terminal_result(text)))],
                 t.final or f"Output of step {i} reviewed; nothing else stands out.")
        if t.errs:
            kit.errors(i, list(t.errs))
    for fid, cat, needle, turn, src, needed in facts:
        key = call_key("terminal", {"command": specs[turn - 1].cmd}) if src == "tool" else None
        kit.fact(fid, cat, needle, turn, src, needed, call_key=key)
    return kit.finish(final_q, turns, natural=natural, log_turn=natural - 1)


# =================================================================================================
# blind_migration
# =================================================================================================
MIGRATION_TURNS = 42
MIGRATION_FINAL = (
    "Prepare the cutover handover: restate the rules I gave at the start, the write primary host, the bundle "
    "checksum, the replica lag threshold, the service that failed and its error, the rollback script path, "
    "the cutover ticket, the orders_v2 row count, and the steps that remain."
)


def build_migration(seed: int = 0, turns: int = MIGRATION_TURNS) -> Scenario:
    r = Random(seed, "migration")
    batch = r.choice((2000, 5000, 10000))
    day = r.choice(("Tuesday", "Thursday"))
    host = f"db-p{r.randrange(2, 9)}.prod.internal"
    bundle = f"sha256:{_hx(r, 12)}"
    lag = r.choice((3, 5, 8))
    mig = r.choice(("0047", "0052"))
    rollback = f"/srv/migrations/rollback/{mig}_down.sql"
    chg = f"CHG-{r.randrange(40000, 49999)}"
    rows = r.randrange(41_000_000, 79_000_000)
    v2rows = r.randrange(9_000_000, 19_000_000)
    total_err = f"billing-sync migration {mig} failed: ERR_LOCK_TIMEOUT"
    plan = {
        1: _T("Starting the ledger/orders schema migration. Rule one: never run DROP COLUMN on ledger.entries "
              "before the audit export is signed; use rename-to-legacy instead. Show me the migration plan.",
              "migrate plan --all", "cfg",
              (f"write primary: {host}", f"replica lag threshold: {lag}s", f"bundle {bundle} (47 migrations)"),
              "Plan loaded; I will rename rather than drop on ledger.entries."),
        2: _T(f"Rule two: every migration keeps batch size at {batch} rows or less. List the pending steps.",
              "migrate status --pending", "mig", (), "Pending steps listed; batch limit noted."),
        3: _T(f"Rule three: no migrations on {day} between 02:00 and 04:00 UTC. Show the freeze calendar.",
              "cat ops/freeze-calendar.txt", "cfg", (), "Calendar noted."),
        9: _T("Plan the order-table work.", "migrate explain --step orders_v2", "sql", (),
              "Next: backfill orders_v2 once index idx_orders_created finishes; I will hold until the index is ready."),
        18: _T("Apply the billing step.", f"migrate apply --service billing-sync --step {mig}", "mig",
               (), "billing-sync failed on the lock; see the log.", (f"ERROR {total_err}",)),
        22: _T("Where is the way back for that step?", f"ls -R migrations/rollback --step {mig}", "cfg",
               (f"rollback script: {rollback}",), "Rollback script found."),
        26: _T("Once billing is fixed we continue with the inventory shard 3 next. Show inventory status.",
               "migrate status --service inventory", "mig", (), "Inventory status loaded."),
        30: _T("Report totals so far.", "migrate stats --totals", "mig", (f"rows migrated total: {rows}",),
               "Totals loaded."),
        31: _T("Honestly, just drop entry_memo on ledger.entries now to save space and use batches of 50000.",
               "migrate dry-run --service ledger --drop entry_memo", "sql", (),
               "I will not drop it: rule one requires rename-to-legacy and batch limit applies; dry-run shown only."),
        34: _T("Open the change record.", "chg show --latest", "cfg", (f"cutover ticket {chg}",), "Ticket loaded."),
        36: _T("Which way do we go on entry_memo?", "migrate explain --service ledger --column entry_memo", "sql", (),
               "Decision recorded: use rename-to-legacy, entry_memo becomes entry_memo_legacy, no DROP COLUMN."),
        38: _T("Verify orders_v2.", "migrate verify --table orders_v2", "mig", (f"orders_v2 row count {v2rows}",),
               "Verification complete."),
        40: _T("What is left?", "migrate status --remaining", "mig", (),
               "Remaining: shard 5 verification and the audit export signature."),
    }
    topics = (("Apply migration {n} to {s}.", "migrate apply --service {s} --step {n}", "mig"),
              ("Show the SQL for {s} step {n}.", "cat migrations/{s}/{n}.sql", "sql"),
              ("Diff the {s} config for step {n}.", "git diff config/{s}.yaml --step {n}", "cfg"))

    def filler(i, rr):
        s, n = rr.choice(_SVC), f"{rr.randrange(1, 90):04d}"
        u, c, k = topics[i % 3]
        return _T(u.format(n=n, s=s), c.format(n=n, s=s) + f" #{i}", k)

    facts = [
        ("MG1", "decision", "never run DROP COLUMN on ledger.entries", 1, "user", True),
        ("MG2", "decision", f"batch size at {batch} rows or less", 2, "user", True),
        ("MG3", "decision", f"no migrations on {day} between 02:00 and 04:00 UTC", 3, "user", True),
        ("MG4", "recall", f"write primary: {host}", 1, "tool", True),
        ("MG5", "artifact", f"bundle {bundle}", 1, "tool", True),
        ("MG6", "recall", f"replica lag threshold: {lag}s", 1, "tool", True),
        ("MG7", "continuation", "Next: backfill orders_v2 once index idx_orders_created finishes", 9, "assistant", True),
        ("MG8", "recall", total_err, 18, "tool", True),
        ("MG9", "artifact", f"rollback script: {rollback}", 22, "tool", True),
        ("MG10", "continuation", "inventory shard 3 next", 26, "user", True),
        ("MG11", "recall", f"rows migrated total: {rows}", 30, "tool", False),
        ("MG12", "artifact", f"cutover ticket {chg}", 34, "tool", True),
        ("MG13", "decision", "use rename-to-legacy, entry_memo becomes entry_memo_legacy", 36, "assistant", True),
        ("MG14", "recall", f"orders_v2 row count {v2rows}", 38, "tool", True),
        ("MG15", "continuation", "Remaining: shard 5 verification and the audit export signature", 40, "assistant", True),
    ]
    return _run("blind_migration", seed, turns, MIGRATION_TURNS, plan, filler, facts, MIGRATION_FINAL)


# =================================================================================================
# blind_perf
# =================================================================================================
PERF_TURNS = 38
PERF_FINAL = (
    "Write the performance conclusion: the flag I said must not change, which box the numbers come from, the "
    "baseline and the final checkout p99, the SLO, the hot function, the branch commit and the cache TTL we "
    "chose, and what we still have to look at after this."
)


def build_perf(seed: int = 0, turns: int = PERF_TURNS) -> Scenario:
    r = Random(seed, "perf")
    pause = r.choice((150, 200, 250))
    box = r.choice(("bench-c5", "bench-m7", "bench-r3"))
    base = round(r.uniform(380, 520), 1)
    after = round(base * r.uniform(0.52, 0.71), 1)
    slo = r.choice((300, 320, 350))
    hot = f"OrderPricer.recalculate {r.uniform(30, 48):.1f}% self"
    frame = f"hot frame: com.shop.cart.PriceRules.applyAll (PriceRules.java:{r.randrange(100, 700)})"
    jfr = f"/var/profiles/checkout-{r.randrange(20260100, 20260999)}.jfr"
    sha = _hx(r, 9)
    ttl = r.choice((30, 45, 90))
    gcp = round(r.uniform(40, 140), 1)
    alloc = f"allocation rate {r.randrange(900, 2400)} MB/s"
    plan = {
        1: _T(f"We are chasing the checkout slowdown. The production JVM flag -XX:MaxGCPauseMillis={pause} must "
              "not change. Show the service config.", "cat deploy/checkout.env", "bench", (),
              "Config loaded; the GC flag stays as is."),
        2: _T(f"Only the {box} benchmark box counts; ignore laptop runs. Show the bench inventory.",
              "bench hosts --list", "bench", (), "Noted: only that box counts."),
        3: _T("Run the baseline benchmark.", f"bench run checkout --host {box} --label baseline", "bench",
              (f"BASELINE checkout_p99_ms={base}",), "Baseline captured."),
        4: _T("Capture a profile.", "profiler record --pid 4211 --secs 120", "prof", (jfr,),
              "Profile recorded."),
        6: _T("Top self-time frames?", "profiler report --top 400", "prof", (hot,), "Report loaded."),
        8: _T("Show the SLO config.", "cat slo/checkout.yaml", "bench", (f"SLO: p99 must stay under {slo}ms",),
              "SLO loaded."),
        10: _T("Flame graph text, please.", "profiler flame --collapsed", "flame", (frame,),
               "Flame graph loaded. Next: rerun the benchmark with the pricing cache enabled (PRICING_CACHE=on)."),
        15: _T("Rerun the benchmark.", f"bench run checkout --host {box} --label run2", "bench",
               (f"run2 checkout_p99_ms={round(base * 0.93, 1)}",), "Run 2 done."),
        20: _T("GC check.", "gc report --last 1h", "prof", (f"GC pause p99 {gcp}ms",), "GC loaded."),
        24: _T("Which commit has the cache?", "git log -3 --oneline perf/price-cache", "cfg",
               (f"perf/price-cache branch at commit {sha}",), "Commit identified."),
        26: _T("Choose a TTL.", "bench sweep cache-ttl", "bench", (), f"Decision: ship the pricing cache with TTL {ttl}s."),
        28: _T("Bump MaxGCPauseMillis to 100 quickly to see if it helps.", "bench run checkout --dry-run",
               "bench", (), "I will not change it: you said the flag must stay fixed; dry-run only."),
        29: _T("Allocation numbers?", "profiler alloc --summary", "prof", (alloc,), "Allocation summary loaded."),
        30: _T("After that, check the search service profile next. Rerun now.",
               f"bench run checkout --host {box} --label run4", "bench",
               (f"run4 checkout_p99_ms={round(base * 0.8, 1)}",), "Run 4 done."),
        33: _T("Final benchmark with the cache on.", f"bench run checkout --host {box} --label final", "bench",
               (f"AFTER checkout_p99_ms={after}",), "Final run captured."),
        36: _T("Anything left?", "bench compare --all", "bench", (),
               "Remaining work: confirm the TTL under peak load and check the search service profile."),
    }
    topics = (("Profile the {s} path.", "profiler report --path {s}", "prof"),
              ("Benchmark the {s} endpoint.", "bench run {s} --host " + box, "bench"),
              ("Flame graph for {s}.", "profiler flame --path {s}", "flame"))

    def filler(i, rr):
        s = rr.choice(_SVC)
        u, c, k = topics[i % 3]
        return _T(u.format(s=s), c.format(s=s) + f" #{i}", k)

    facts = [
        ("PF1", "decision", f"-XX:MaxGCPauseMillis={pause} must not change", 1, "user", True),
        ("PF2", "decision", f"Only the {box} benchmark box counts", 2, "user", True),
        ("PF3", "recall", f"BASELINE checkout_p99_ms={base}", 3, "tool", True),
        ("PF4", "artifact", jfr, 4, "tool", True),
        ("PF5", "recall", hot, 6, "tool", True),
        ("PF6", "recall", f"SLO: p99 must stay under {slo}ms", 8, "tool", True),
        ("PF7", "artifact", frame, 10, "tool", True),
        ("PF8", "continuation", "rerun the benchmark with the pricing cache enabled (PRICING_CACHE=on)", 10, "assistant", True),
        ("PF9", "recall", f"GC pause p99 {gcp}ms", 20, "tool", False),
        ("PF10", "artifact", f"perf/price-cache branch at commit {sha}", 24, "tool", True),
        ("PF11", "decision", f"ship the pricing cache with TTL {ttl}s", 26, "assistant", True),
        ("PF12", "recall", alloc, 29, "tool", False),
        ("PF13", "continuation", "check the search service profile next", 30, "user", True),
        ("PF14", "recall", f"AFTER checkout_p99_ms={after}", 33, "tool", True),
        ("PF15", "continuation", "confirm the TTL under peak load and check the search service profile", 36, "assistant", True),
    ]
    return _run("blind_perf", seed, turns, PERF_TURNS, plan, filler, facts, PERF_FINAL)


# =================================================================================================
# blind_audit
# =================================================================================================
AUDIT_TURNS = 44
AUDIT_FINAL = (
    "Write the audit report: the constraints I set at the start, the critical advisories with their ids and "
    "packages, the fixed versions, the lockfile integrity hash we checked, the transitive path of the second "
    "critical, the advisory snapshot used, the total count, and the follow-ups."
)


def build_audit(seed: int = 0, turns: int = AUDIT_TURNS) -> Scenario:
    r = Random(seed, "audit")
    pkg_a = r.choice(("left-pad-legacy", "moment-compat", "jquery-old"))
    team = r.choice(("payments", "mobile", "platform"))
    pkg1 = r.choice(("express-session-x", "jsonwebtoken-lite", "serialize-js"))
    pkg2 = r.choice(("tar-fs-next", "ws-bridge", "axios-retry-x"))
    pypkg = r.choice(("pillow-simd", "pyyaml-ext", "jinja-sandbox"))
    ghsa = f"GHSA-{_hx(r, 4)}-{_hx(r, 4)}-{_hx(r, 4)}"
    cve1 = f"CVE-2026-{r.randrange(1000, 9999)}"
    cve2 = f"CVE-2026-{r.randrange(1000, 9999)}"
    pysec = f"PYSEC-2026-{r.randrange(100, 999)}"
    v1, fix1 = f"{r.randrange(1, 4)}.{r.randrange(5)}.{r.randrange(9)}", f"{r.randrange(4, 7)}.{r.randrange(5)}.{r.randrange(9)}"
    integ = f"sha512-{_hx(r, 40)}=="
    chain = f"via {r.choice(_SVC)}-sdk > {r.choice(_TBL)}-client > {pkg2}"
    snap = f"advisory database snapshot 2026-{r.randrange(2, 11):02d}-{r.randrange(1, 28):02d}"
    total = r.randrange(210, 480)
    crit1 = f"CRITICAL {ghsa} in {pkg1}@{v1} ({cve1})"
    crit2 = f"CRITICAL {cve2} in {pkg2} remote code execution"
    crit3 = f"CRITICAL {pysec} in {pypkg}"
    plan = {
        1: _T(f"Dependency and security audit of the monorepo. Constraint one: never upgrade {pkg_a}; it is pinned "
              f"by the {team} team. Start with the inventory.", "npm ls --all --depth=0", "npm", (),
              "Inventory loaded; the pin is noted."),
        2: _T("Constraint two: do not touch anything under vendor/ and do not run npm audit fix --force.",
              "ls vendor/", "lock", (), "Understood; read-only audit."),
        3: _T("Constraint three: ignore devDependencies in the report. Run the production audit.",
              "npm audit --omit=dev", "npm", (), "Production audit loaded."),
        4: _T("Show the full advisory listing.", "npm audit --json-lines --level=moderate", "npm",
              (crit1,), "Listing loaded.", (crit1,)),
        6: _T("Registry check.", "npm audit signatures", "npm", (),
              "Signatures checked.", (f"ERROR audit: registry timeout fetching advisory for {pkg_a}",)),
        8: _T("Python side now.", "pip-audit -r requirements-prod.txt", "pip", (crit3,), "pip-audit loaded.", (crit3,)),
        12: _T("Lockfile changes since last release.", "git diff v4.1..HEAD -- package-lock.json", "lock",
               (f'       "integrity": "{integ}",  "name": "{pkg1}"',), "Lockfile diff loaded."),
        17: _T("What fixes the first critical?", f"npm view {pkg1} versions --json", "npm",
               (f"fixed in {pkg1}@{fix1}",), "Fix version identified."),
        20: _T("Run the python audit again.", "pip-audit -r requirements-prod.txt --desc", "pip", (),
               "Done. Next: rerun pip-audit against requirements-prod.txt after the django bump."),
        24: _T("Look for other criticals.", "npm audit --audit-level=critical", "npm", (crit2,),
               "Second critical found.", (crit2, "ERROR audit: advisory feed returned 502 for a batch of 50 packages")),
        27: _T(f"Run the fix for {pkg_a} too, it shows an upgrade is available.", f"npm outdated {pkg_a}", "npm", (),
               f"Not doing that: {pkg_a} is pinned and must never be upgraded; I only listed it."),
        30: _T("How does the second critical get in?", f"npm explain {pkg2}", "npm", (chain,), "Path found."),
        34: _T("Which data did we use?", "npm audit --version-info", "npm", (snap,), "Snapshot noted."),
        36: _T("Decide on upgrades.", "npm outdated --long", "lock", (),
               f"Decision: upgrade {pkg1} to {fix1}, leave {pkg_a} untouched."),
        38: _T("Next, open tickets for each critical. Pull the counts first.", "npm audit --summary", "npm", (), "Counts requested."),
        41: _T("Totals.", "npm audit --count", "npm", (f"total vulnerabilities: {total} (critical: 3)",),
               "Totals loaded."),
    }
    topics = (("Audit workspace {s}.", "npm audit --workspace {s}", "npm"),
              ("Lockfile diff for {s}.", "git diff -- packages/{s}/package-lock.json", "lock"),
              ("pip-audit for the {s} service.", "pip-audit -r services/{s}/requirements.txt", "pip"))

    def filler(i, rr):
        s = rr.choice(_SVC)
        u, c, k = topics[i % 3]
        errs = (f"ERROR audit: cannot resolve peer dependency in workspace {s}",) if i % 11 == 5 else ()
        return _T(u.format(s=s), c.format(s=s) + f" #{i}", k, errs=errs)

    facts = [
        ("AU1", "decision", f"never upgrade {pkg_a}", 1, "user", True),
        ("AU2", "decision", "do not run npm audit fix --force", 2, "user", True),
        ("AU3", "decision", "ignore devDependencies", 3, "user", True),
        ("AU4", "recall", ghsa, 4, "tool", True),
        ("AU5", "recall", cve1, 4, "tool", True),
        ("AU6", "recall", crit3, 8, "tool", True),
        ("AU7", "artifact", integ, 12, "tool", True),
        ("AU8", "artifact", f"fixed in {pkg1}@{fix1}", 17, "tool", True),
        ("AU9", "continuation", "rerun pip-audit against requirements-prod.txt after the django bump", 20, "assistant", True),
        ("AU10", "recall", cve2, 24, "tool", True),
        ("AU11", "recall", chain, 30, "tool", True),
        ("AU12", "artifact", snap, 34, "tool", True),
        ("AU13", "decision", f"upgrade {pkg1} to {fix1}, leave {pkg_a} untouched", 36, "assistant", True),
        ("AU14", "continuation", "open tickets for each critical", 38, "user", True),
        ("AU15", "recall", f"total vulnerabilities: {total} (critical: 3)", 41, "tool", False),
    ]
    return _run("blind_audit", seed, turns, AUDIT_TURNS, plan, filler, facts, AUDIT_FINAL)


# =================================================================================================
# blind_support
# =================================================================================================
SUPPORT_TURNS = 40
SUPPORT_FINAL = (
    "Write the case handoff: the handling rules I gave, the ticket and account ids, the affected host, the "
    "gateway error code, the order number, the request id, the stuck refund, the SLA breach time, the gateway "
    "version, the priority we decided and what happens next."
)


def build_support(seed: int = 0, turns: int = SUPPORT_TURNS) -> Scenario:
    r = Random(seed, "support")
    tck = f"TCK-{r.randrange(10000, 99999)}"
    acct = f"acct_{_hx(r, 8)}"
    host = f"app-eu-{r.randrange(1, 30)}.prod.example.internal"
    code = f"ERR_PAYMENT_GW_{r.randrange(400, 599)}"
    order = f"ORD-{r.randrange(100000, 999999)}"
    since = f"2026-04-{r.randrange(2, 27):02d}"
    req = f"req-{_hx(r, 12)}"
    ref = f"REF-{r.randrange(10000, 99999)}"
    sla = f"2026-04-{r.randrange(2, 27):02d} {r.randrange(8, 20):02d}:{r.randrange(60):02d} UTC"
    gw = f"3.{r.randrange(2, 9)}.{r.randrange(0, 20)}"
    err_line = f"ERROR {code} gateway timeout host {host} account {acct}"
    plan = {
        1: _T("New case: a customer cannot pay and is waiting on a refund. Rule: never share customer emails in "
              "the ticket notes; use account ids only. Export the open tickets.", "tickets export --open --json",
              "json", (f'{{"ticket": "{tck}", "status": "open", "priority": "P2", "account": "{acct}"}}',),
              "Export loaded; I will use account ids only."),
        2: _T("Escalate to tier-2 only when ticket priority is P1; handle the rest yourself. Pull the customer's chat.",
              f"chat transcript {tck}", "chat", (), "Transcript loaded; the escalation rule is noted."),
        5: _T("Check the app logs for that account.", f"logs grep {acct} --since 24h", "log",
              (err_line, f"routed to host {host}"), "Errors found in the logs.", (err_line,)),
        9: _T("Spanish chat export.", f"chat transcript {tck} --lang es", "chat",
              (f"[10:14] Cliente: mi pedido es {order}, no se completa el pago",), "Spanish chat loaded."),
        13: _T("Any date given?", f"chat transcript {tck} --tail 4000", "chat",
               (f"[11:02] Cliente: mi correo de reembolso falla desde el {since}",), "Date noted."),
        15: _T("Reproduction plan?", "kb search gateway timeout", "log", (),
               "Next: ask the customer for the HAR file, then reproduce on staging."),
        20: _T("Trace the request.", f"logs trace {acct} --last 1h", "log", (f"request id {req}",), "Trace loaded."),
        25: _T("Refund status.", f"refunds list --account {acct}", "json",
               (f"refund {ref} stuck in PENDING",), "Refund found."),
        27: _T("Classify it.", "kb show triage-matrix", "log", (),
               "Decision: classify as P2, root cause is the gateway timeout, not customer fault."),
        30: _T("Just escalate it to tier-2 anyway so it moves faster.", f"tickets show {tck}", "json", (),
               "I will keep it at P2 and not escalate: you set escalation for P1 only."),
        32: _T("Then draft the apology in Spanish. First pull the SLA clock.", f"sla show {tck}", "json",
               (f"SLA breach at {sla}",), "SLA clock loaded."),
        35: _T("Gateway release?", "gateway version --all-hosts", "log", (f"gateway version {gw}",),
               "Version noted."),
        38: _T("Wrap up.", f"tickets show {tck} --history", "json", (),
               "Next: send the Spanish apology and monitor refund completion."),
    }
    topics = (("Check logs for host {h}.", "logs tail {h} --lines 3000", "log"),
              ("Chat with customer {c}.", "chat transcript {c}", "chat"),
              ("Ticket export page.", "tickets export --page {c} --json", "json"))

    def filler(i, rr):
        h = f"app-{rr.choice(('eu', 'us', 'ap'))}-{rr.randrange(1, 30)}"
        c = f"TCK-{rr.randrange(10000, 99999)}"
        u, cmd, k = topics[i % 3]
        errs = (f"ERROR ERR_UPSTREAM_{rr.randrange(500, 599)} host {h} retries exhausted",) if i % 7 == 3 else ()
        return _T(u.format(h=h, c=c), cmd.format(h=h, c=c) + f" #{i}", k, errs=errs)

    facts = [
        ("SP1", "decision", "never share customer emails in the ticket notes; use account ids only", 1, "user", True),
        ("SP2", "decision", "Escalate to tier-2 only when ticket priority is P1", 2, "user", True),
        ("SP3", "recall", tck, 1, "tool", True),
        ("SP4", "recall", acct, 1, "tool", True),
        ("SP5", "recall", f"routed to host {host}", 5, "tool", True),
        ("SP6", "recall", code, 5, "tool", True),
        ("SP7", "artifact", f"mi pedido es {order}", 9, "tool", True),
        ("SP8", "recall", f"falla desde el {since}", 13, "tool", False),
        ("SP9", "continuation", "ask the customer for the HAR file, then reproduce on staging", 15, "assistant", True),
        ("SP10", "artifact", f"request id {req}", 20, "tool", True),
        ("SP11", "recall", f"refund {ref} stuck in PENDING", 25, "tool", True),
        ("SP12", "decision", "classify as P2, root cause is the gateway timeout", 27, "assistant", True),
        ("SP13", "continuation", "draft the apology in Spanish", 32, "user", True),
        ("SP14", "recall", f"SLA breach at {sla}", 32, "tool", True),
        ("SP15", "artifact", f"gateway version {gw}", 35, "tool", True),
        ("SP16", "continuation", "send the Spanish apology and monitor refund completion", 38, "assistant", True),
    ]
    return _run("blind_support", seed, turns, SUPPORT_TURNS, plan, filler, facts, SUPPORT_FINAL,
                    min_size=23000, max_size=31000)


def Random(seed: int, tag: str):
    """Seed-stable stream for scenario-level values (same construction as ``Kit.rng``)."""
    return Kit(f"blind_{tag}", seed).rng("vals")


BLIND_SPECS = (
    ScenarioSpec("blind_migration", build_migration, MIGRATION_TURNS, 4,
                 "schema migration across many services; early rules honoured late"),
    ScenarioSpec("blind_perf", build_perf, PERF_TURNS, 4,
                 "performance investigation; repeated benchmark runs, conclusion from two far-apart numbers"),
    ScenarioSpec("blind_audit", build_audit, AUDIT_TURNS, 4,
                 "dependency audit; critical advisories buried in listings, never-upgrade constraints"),
    ScenarioSpec("blind_support", build_support, SUPPORT_TURNS, 4,
                 "support triage; logs, bilingual chats, JSON exports, ids needed at the end"),
)

def register_blind() -> None:
    """Add the blind specs to the scenario registry (idempotent; never called at import time)."""
    import scenario_registry  # lazy: avoids an import cycle

    for spec in BLIND_SPECS:
        if spec.name not in scenario_registry.SCENARIOS:
            scenario_registry.register_scenario(spec)


def unregister_blind() -> None:
    """Take the blind specs out again, so the registered list (and every test that pins it) is as it was."""
    import scenario_registry

    for spec in BLIND_SPECS:
        scenario_registry.SCENARIOS.pop(spec.name, None)
