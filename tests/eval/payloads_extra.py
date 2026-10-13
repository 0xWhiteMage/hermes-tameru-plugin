"""Tool-result generators for the extra replay scenarios (``scenarios_extra``).

Same rules as ``tests/fixtures/hermes_payloads.py`` (which supplies the Hermes envelopes and a few bodies used
here): every generator takes a seeded ``random.Random``, never touches the clock or ``hash()``, and the same
seed gives byte-identical output. The bodies follow the real tools' formats (``kubectl``, ``journalctl``,
``docker logs``, ``pgbouncer``, ``pytest``, ``rg``, ``jq`` ...) closely enough for the engines' text
heuristics (log levels, tracebacks, tables, JSON, source code) to meet what they meet in practice; they are
synthetic, and nothing here claims to be a real system's output.

Error lines follow the fingerprint contract of the harness (``replay.error_fingerprint``): a level tag
(``ERROR``/``FATAL``/``CRITICAL``/``FAILED``), an exception line or a traceback header. Every generator that
prints errors also returns them, so a scenario can say which error lines the model was shown.
"""
from __future__ import annotations

import functools
import json
import random
import string
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent.parent
if str(_TESTS_DIR) not in sys.path:  # ``fixtures`` lives next to ``eval``
    sys.path.insert(0, str(_TESTS_DIR))

from fixtures import hermes_payloads as hp  # noqa: E402

# Level words that carry an error fingerprint (the harness' ``_LEVEL_RE``, upper case only).
ERROR_LEVELS = frozenset({"ERROR", "FATAL", "CRITICAL", "PANIC", "FAILED"})

Template = tuple[str, str, str]  # (level, logger or process, message template)


def hms(sec: int) -> str:
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def hex_(rng: random.Random, n: int) -> str:
    return hp._hex(rng, n)


_FIELD_GENERATORS: dict[str, Callable[[random.Random], object]] = {
    "m": lambda r: r.choice(["GET", "GET", "POST", "PUT"]),
    "p": lambda r: r.choice(hp._PATHS).format(id=r.randint(10000, 99999), sku=f"SKU-{r.randint(10000, 99999)}"),
    "st": lambda r: r.choice([200, 200, 200, 201, 204, 304, 404]),
    "ms": lambda r: r.randint(3, 2400),
    "h": lambda r: hex_(r, 8),
    "h16": lambda r: hex_(r, 16),
    "n": lambda r: r.randint(1, 12),
    "s": lambda r: r.randint(10000, 99999),
    "q": lambda r: r.randint(1, 5),
    "o": lambda r: r.randint(10000, 99999),
    "amt": lambda r: f"{r.randint(5, 900)}.{r.randint(0, 99):02d}",
    "qn": lambda r: r.choice(["fulfil", "email", "reconcile"]),
    "b": lambda r: r.randint(0, 255),
    "c": lambda r: r.randint(0, 255),
    "d": lambda r: r.randint(1, 254),
    "a": lambda r: r.randint(1, 4),
    "w": lambda r: r.randint(1, 9),
    "x": lambda r: r.randint(100000, 999999),
    "x3": lambda r: r.randint(120, 980),
    "pid": lambda r: r.randint(1000, 9999),
    "port": lambda r: r.randint(20000, 60000),
    "k": lambda r: r.randint(3, 40),
    "u": lambda r: hp._uuid(r),
}


@functools.lru_cache(maxsize=None)
def _fields_of(tpl: str) -> tuple[str, ...]:
    """The placeholders of ``tpl`` in the fixed order the generators are drawn in."""
    used = {name for _, name, _, _ in string.Formatter().parse(tpl) if name}
    return tuple(name for name in _FIELD_GENERATORS if name in used)


def fill(tpl: str, rng: random.Random) -> str:
    """Fill the placeholders of a message template with random values (drawn in a fixed order, so a template
    always consumes the same stream)."""
    return tpl.format(**{name: _FIELD_GENERATORS[name](rng) for name in _fields_of(tpl)})


# =================================================================================================
# Logs
# =================================================================================================
@dataclass(frozen=True)
class LogStyle:
    """One kind of log: how a line looks and what it says."""

    fmt: Callable[[int, int, str, str, str], str]  # (sec, ms, level, logger, message) -> line
    info: tuple[Template, ...]
    warn: tuple[Template, ...]
    errors: tuple[Template, ...]  # level-tagged: every template is exactly one error fingerprint
    burst_warn: tuple[Template, ...] = ()  # warnings that only show up while the incident burns
    notable: tuple[Template, ...] = ()  # important lines no fingerprint sees (OOM kill, systemd, redis warnings)
    warn_rate: float = 0.07
    step: int = 3  # up to this many seconds between lines


@dataclass
class Log:
    lines: list[str]
    error_index: list[int] = field(default_factory=list)  # lines that carry an error fingerprint

    def window(self, start: int, stop: int) -> tuple[str, list[str]]:
        """``(text, error lines)`` of ``lines[start:stop]`` (what ``tail`` / ``--tail`` prints at that moment)."""
        text = "\n".join(self.lines[start:stop]) + "\n"
        return text, [self.lines[i] for i in self.error_index if start <= i < stop]


def burst_start(n_lines: int, burst: tuple[float, float]) -> int:
    """Index of the first line of the error burst of ``make_log(n_lines=..., burst=...)``."""
    return int(n_lines * burst[0])


def make_log(
    rng: random.Random,
    style: LogStyle,
    *,
    n_lines: int,
    start_sec: int,
    burst: tuple[float, float] = (0.3, 0.8),
    burst_rate: float = 0.3,
    n_errors: int = 20,
    n_notable: int = 0,
    plants: dict[int, Template] | None = None,
) -> Log:
    """A log of ``n_lines`` lines whose incident burst lies in ``burst`` (fractions of the log).

    Inside the burst ``burst_rate`` of the lines are the style's burst-only warnings; ``n_errors`` lines are
    error lines (every error template at least once when ``n_errors`` allows, the rest random repeats) and
    ``n_notable`` are lines no fingerprint sees. ``plants`` (line index -> ``(level, logger, message)``)
    replace the line at that index and keep its time; a planted line with an error level is an error line.
    """
    t_ms = start_sec * 1000
    stamps: list[tuple[int, int]] = []
    lines: list[str] = []
    for _ in range(n_lines):
        t_ms += rng.randint(1, max(1, style.step * 1000))  # strictly increasing, like a real log
        sec, ms = divmod(t_ms, 1000)
        stamps.append((sec, ms))
        warn = rng.random() < style.warn_rate
        level, logger, tpl = rng.choice(style.warn if warn else style.info)
        lines.append(style.fmt(sec, ms, level, fill(logger, rng), fill(tpl, rng)))
    taken = set(plants or {})
    lo, hi = burst_start(n_lines, burst), int(n_lines * burst[1])
    if style.burst_warn:
        for i in range(lo, hi):
            if i not in taken and rng.random() < burst_rate:
                level, logger, tpl = rng.choice(style.burst_warn)
                lines[i] = style.fmt(*stamps[i], level, fill(logger, rng), fill(tpl, rng))
    n_errors = n_errors if style.errors else 0
    free = [i for i in range(lo, hi) if i not in taken]
    picked = sorted(rng.sample(free, min(n_errors + n_notable, len(free))))
    kinds = (list(range(len(style.errors))) + [rng.randrange(len(style.errors)) for _ in range(max(0, n_errors - len(style.errors)))])[:n_errors] if n_errors else []
    rng.shuffle(kinds)
    order = list(range(len(picked)))
    rng.shuffle(order)  # which of the picked slots become errors, which notable lines
    error_index: list[int] = []
    for slot, pos in enumerate(order):
        idx = picked[pos]
        sec, ms = stamps[idx]
        if slot < len(kinds):
            level, logger, tpl = style.errors[kinds[slot]]
            lines[idx] = style.fmt(sec, ms, level, fill(logger, rng), fill(tpl, rng))
            error_index.append(idx)
        elif style.notable:
            level, logger, tpl = rng.choice(style.notable)
            lines[idx] = style.fmt(sec, ms, level, fill(logger, rng), fill(tpl, rng))
    for idx, (level, logger, msg) in sorted((plants or {}).items()):
        sec, ms = stamps[idx]
        lines[idx] = style.fmt(sec, ms, level, fill(logger, rng), msg)
        if level in ERROR_LEVELS:
            error_index.append(idx)
    return Log(lines, sorted(set(error_index)))


# ---- styles -----------------------------------------------------------------------------------------
def _fmt_app(sec: int, ms: int, level: str, logger: str, msg: str) -> str:
    return f"2025-03-14T{hms(sec)}.{ms:03d}Z {level:<5} {logger} {msg}"


def _fmt_worker(sec: int, ms: int, level: str, logger: str, msg: str) -> str:
    return f"2025-03-14 {hms(sec)},{ms:03d} {level:<5} [{logger}] {msg}"


def _fmt_journal(sec: int, ms: int, level: str, logger: str, msg: str) -> str:
    return f"Mar 14 {hms(sec)} worker-07 {logger}: {level + ' ' if level else ''}{msg}"


def _fmt_pgbouncer(sec: int, ms: int, level: str, logger: str, msg: str) -> str:
    return f"2025-03-14 {hms(sec)}.{ms:03d} UTC [1] {level} {msg}"


def _fmt_redis(sec: int, ms: int, level: str, logger: str, msg: str) -> str:
    return f"1:M 14 Mar 2025 {hms(sec)}.{ms:03d} {level} {msg}"


APP_LOG = LogStyle(
    _fmt_app,
    info=(
        ("INFO", "shop.http", "{m} {p} {st} {ms}ms trace_id={h16} req_id={h}"),
        ("INFO", "shop.http", "{m} {p} {st} {ms}ms trace_id={h16} req_id={h}"),
        ("INFO", "shop.cart", "cart updated cart_id=c-{h} items={q} dur_ms={ms}"),
        ("INFO", "shop.orders", "holds listed order=ORD-{o} count={q} dur_ms={ms}"),
        ("INFO", "shop.pricing", "price quote order=ORD-{o} total={amt} currency=EUR"),
        ("INFO", "shop.payments", "charge created order=ORD-{o} charge=ch_{h}"),
        ("INFO", "shop.session", "session refreshed user=U-{o} ip=10.{b}.{c}.{d}"),
    ),
    warn=(
        ("WARN", "shop.http", "slow request {m} {p} dur_ms={ms} trace_id={h16}"),
        ("WARN", "shop.payments", "retrying charge order=ORD-{o} attempt={a}"),
    ),
    burst_warn=(
        ("WARN", "shop.db.pool", "waited {ms}ms for a connection (pool_size=38 in_use={k} waiting={w})"),
        ("WARN", "shop.checkout", "checkout failed order=ORD-{o} status=504 reason=pool_timeout"),
        ("WARN", "shop.checkout", "checkout failed order=ORD-{o} status=504 reason=pool_timeout"),
    ),
    errors=(
        ("ERROR", "shop.db.pool", "sqlalchemy.exc.TimeoutError: QueuePool limit of size 38 overflow 0 reached, connection timed out, timeout 30.00 (order=ORD-{o})"),
        ("ERROR", "shop.payments", "PaymentGatewayTimeout: stripe-proxy did not answer within {ms}ms (order=ORD-{o}, attempt={a})"),
        ("ERROR", "shop.orders", "HoldsLookupFailed: could not list holds for order=ORD-{o}: pool exhausted"),
        ("CRITICAL", "uvicorn.error", "request queue limit exceeded ({n} pending), shedding load on pod checkout-api-{h}"),
        ("ERROR", "shop.cache", "redis.exceptions.TimeoutError: Timeout reading from socket (host=cart-redis-{w}.shop.svc:6379)"),
    ),
    warn_rate=0.14,
    step=4,
)

WORKER_LOG = LogStyle(
    _fmt_worker,
    info=(
        ("INFO", "shop.worker.queue", "picked job id={h} queue={qn}"),
        ("INFO", "shop.worker.queue", "job done id={h} dur_ms={ms}"),
        ("INFO", "shop.worker.hb", "heartbeat worker-{n} jobs_in_flight={w} rss_mb={x3}"),
        ("INFO", "shop.worker.reconcile", "released {q} holds for order=ORD-{o}"),
    ),
    warn=(("WARN", "shop.worker.queue", "job id={h} slow dur_ms={ms}"),),
    burst_warn=(
        ("WARN", "shop.worker.queue", "retrying job id={h} attempt={a}"),
        ("WARN", "shop.worker.queue", "job id={h} slow dur_ms={ms}"),
    ),
    errors=(
        ("ERROR", "shop.worker.reconcile", "reconcile_holds failed: QueuePool limit of size 38 overflow 0 reached (job={h})"),
        ("ERROR", "shop.worker.mailer", "SMTPServerDisconnected: Connection unexpectedly closed (job={h})"),
        ("FATAL", "shop.worker.main", "worker-{n} exiting: too many consecutive failures (k={k}) job={h}"),
        ("ERROR", "shop.worker.ledger", "LedgerWriteError: could not serialize access due to concurrent update (xid={x})"),
    ),
    warn_rate=0.08,
    step=7,
)

JOURNAL_LOG = LogStyle(
    _fmt_journal,
    info=(
        ("INFO", "shop-worker[{pid}]", "picked job id={h} queue={qn}"),
        ("INFO", "shop-worker[{pid}]", "job done id={h} dur_ms={ms}"),
        ("", "systemd[1]", "Started shop-worker@{n}.service - Shop worker {n}."),
        ("", "systemd[1]", "Stopping shop-worker@{n}.service - Shop worker {n}..."),
    ),
    warn=(("WARN", "shop-worker[{pid}]", "retrying job id={h} attempt={a}"),),
    burst_warn=(("WARN", "shop-worker[{pid}]", "job id={h} slow dur_ms={ms}"),),
    errors=(
        ("ERROR", "shop-worker[{pid}]", "reconcile_holds failed: QueuePool limit of size 38 overflow 0 reached (job={h})"),
        ("FATAL", "shop-worker[{pid}]", "worker-{n} exiting: too many consecutive failures (k={k}) job={h}"),
    ),
    notable=(
        ("", "kernel", "Out of memory: Killed process {pid} (python) total-vm:{x}kB, anon-rss:{x}kB, file-rss:0kB, shmem-rss:0kB, UID:998 pgtables:{x}kB oom_score_adj:0"),
        ("", "systemd[1]", "shop-worker@{n}.service: Main process exited, code=killed, status=9/KILL"),
        ("", "systemd[1]", "shop-worker@{n}.service: Failed with result 'oom-kill'."),
        ("", "systemd[1]", "shop-worker@{n}.service: Scheduled restart job, restart counter is at {k}."),
    ),
    warn_rate=0.05,
    step=4,
)

PGBOUNCER_LOG = LogStyle(
    _fmt_pgbouncer,
    info=(
        ("LOG", "", "stats: {k} xacts/s, {w} queries/s, in {x} B/s, out {x} B/s, xact {ms} us, query {ms} us, wait {a} us"),
        ("LOG", "", "C-0x{h}: shop/shop@10.0.{b}.{c}:{port} login attempt: db=shop user=shop tls=no"),
        ("LOG", "", "C-0x{h}: shop/shop@10.0.{b}.{c}:{port} closing because: client close request (age={n}s)"),
        ("LOG", "", "S-0x{h}: shop/shop@10.0.9.4:5432 new connection to server (from 10.0.3.17:{port})"),
    ),
    warn=(("WARNING", "", "C-0x{h}: shop/shop@10.0.{b}.{c}:{port} client login timeout"),),
    burst_warn=(("WARNING", "", "C-0x{h}: shop/shop@10.0.{b}.{c}:{port} pooler error: server conn crashed?"),),
    errors=(
        ("ERROR", "", "C-0x{h}: shop/shop@10.0.{b}.{c}:{port} pooler error: no more connections allowed (max_client_conn)"),
        ("ERROR", "", "S-0x{h}: shop/shop@10.0.9.4:5432 closing because: server conn crashed? (age={n}s)"),
    ),
    warn_rate=0.10,
    step=1,
)

REDIS_LOG = LogStyle(
    _fmt_redis,
    info=(
        ("*", "", "{q} changes in 300 seconds. Saving..."),
        ("*", "", "Background saving started by pid {pid}"),
        ("*", "", "DB saved on disk"),
        ("*", "", "Background saving terminated with success"),
        ("*", "", "RDB: {w} MB of memory used by copy-on-write"),
    ),
    warn=(("#", "", "Client id={n} addr=10.0.{b}.{c}:{port} laddr=10.0.3.12:6379 name= age={k} idle=0 qbuf={x} obl={x} oll={q} omem={x} scheduled to be closed ASAP for overcoming of output buffer limits."),),
    errors=(),  # redis marks severity with a symbol, not a level word: no fingerprint, like the real thing
    burst_warn=(("#", "", "Client id={n} addr=10.0.{b}.{c}:{port} laddr=10.0.3.12:6379 name= age={k} idle=0 qbuf={x} obl={x} oll={q} omem={x} scheduled to be closed ASAP for overcoming of output buffer limits."),),
    notable=(
        ("#", "", "Can't save in background: fork: Cannot allocate memory"),
        ("#", "", "Background saving error"),
    ),
    warn_rate=0.04,
    step=4,
)


def python_traceback_log(rng: random.Random, *, n_requests: int, start_sec: int) -> tuple[str, list[str]]:
    """The previous container's log: request lines, then tracebacks ending in the pool timeout and the OOM kill."""
    out: list[str] = []
    errors: list[str] = []
    sec = start_sec
    frames = [
        '  File "/app/shop/orders/api.py", line {a}, in list_open_holds\n    holds = repo.find_open_holds(order_id)',
        '  File "/app/shop/orders/repository.py", line {b}, in find_open_holds\n    session = self._pool.connect()',
        '  File "/usr/local/lib/python3.12/site-packages/sqlalchemy/pool/base.py", line {c}, in connect\n    return _ConnectionFairy._checkout(self)',
        '  File "/usr/local/lib/python3.12/site-packages/sqlalchemy/pool/impl.py", line {d}, in _do_get\n    return self._create_connection()',
    ]
    for i in range(n_requests):
        sec += rng.randint(0, 3)
        ms = rng.randint(0, 999)
        stamp = f"2025-03-14T{hms(sec)}.{ms:03d}Z"
        out.append(f"{stamp} INFO  shop.http {fill('{m} {p} {st} {ms}ms trace_id={h16} req_id={h}', rng)}")
        if i % 9 == 4:
            head = f"{stamp} ERROR shop.db.pool Exception in ASGI application"
            exc = (
                "sqlalchemy.exc.TimeoutError: QueuePool limit of size 38 overflow 0 reached, connection timed out, "
                "timeout 30.00 (Background on this error at: https://sqlalche.me/e/20/3o7r)"
            )
            body = "\n".join(
                f.format(a=rng.randint(180, 260), b=rng.randint(60, 120), c=rng.randint(1100, 1300), d=rng.randint(100, 200))
                for f in frames
            )
            out.append(head + "\nTraceback (most recent call last):\n" + body + "\n" + exc)
            errors += [head, "Traceback (most recent call last):", exc]
    sec += 2
    tail = f"2025-03-14T{hms(sec)}.101Z CRITICAL uvicorn.error request queue limit exceeded (214 pending), shedding load on pod checkout-api-7d9f8c6b5d-x2k4q"
    out += [tail, "Killed"]
    errors.append(tail)
    return "\n".join(out) + "\n", errors


# =================================================================================================
# kubectl / helm / docker
# =================================================================================================
_ROW_NODES = ["ip-10-0-3-17", "ip-10-0-3-41", "ip-10-0-4-9", "ip-10-0-4-23", "ip-10-0-5-30"]


def _table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header))]
    lines = ["   ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip() for r in [header, *rows]]
    return "\n".join(lines) + "\n"


class PodRoster:
    """The pods of the ``shop`` namespace, fixed for a whole session (names, IPs and nodes do not change between
    two ``kubectl get pods``; only states and ages do)."""

    OLD_RS, NEW_RS = "7d9f8c6b5d", "5c6b7f9d84"

    def __init__(self, rng: random.Random) -> None:
        self.old = [f"checkout-api-{self.OLD_RS}-{hex_(rng, 5)}" for _ in range(6)]
        self.old[0] = f"checkout-api-{self.OLD_RS}-x2k4q"  # the pod the session looks at first
        self.new = [f"checkout-api-{self.NEW_RS}-{hex_(rng, 5)}" for _ in range(6)]
        self.workers = [f"orders-worker-6f7c9b8d4-{hex_(rng, 5)}" for _ in range(3)]
        self.inventory = [f"inventory-sync-5d8f6c7b9-{hex_(rng, 5)}" for _ in range(2)]
        self.search = [f"search-api-7b9c5d6f8-{hex_(rng, 5)}" for _ in range(3)]
        self.ingress = f"nginx-ingress-controller-5f6d8b7c4-{hex_(rng, 5)}"
        names = [*self.old, *self.new, *self.workers, *self.inventory, *self.search, self.ingress,
                 "cart-redis-0", "cart-redis-1", "cart-redis-2"]
        self.ip = {n: f"10.42.{rng.randint(1, 6)}.{rng.randint(2, 250)}" for n in names}
        self.node = {n: rng.choice(_ROW_NODES) for n in names}
        self.worker_restarts = [rng.choice([0, 0, 1, 4]) for _ in self.workers]

    def _row(self, name: str, ready: str, status: str, restarts: str, age: str) -> list[str]:
        return [name, ready, status, restarts, age, self.ip[name], self.node[name], "<none>", "<none>"]

    def get_pods(self, phase: str, minute: int) -> str:
        """``kubectl -n shop get pods -o wide``. ``phase``: incident | rolling | recovering | stable; ``minute``
        is the time of the command in minutes after 02:00 (it sets the ages)."""
        rows: list[list[str]] = []
        if phase == "incident":
            states = [("0/1", "CrashLoopBackOff", "9 (2m41s ago)"), ("0/1", "OOMKilled", "7 (51s ago)"),
                      ("1/1", "Running", "3 (6m12s ago)"), ("0/1", "CrashLoopBackOff", "6 (4m3s ago)"),
                      ("1/1", "Running", "2 (9m30s ago)"), ("1/1", "Running", "1 (14m ago)")]
            rows += [self._row(n, *st, f"{minute + 8}m") for n, st in zip(self.old, states)]
        elif phase == "rolling":
            for i, n in enumerate(self.old):
                rows.append(self._row(n, "1/1" if i > 3 else "0/1", "Terminating" if i < 4 else "Running",
                                      "0" if i > 3 else "5", f"{minute + 8}m"))
            for i, n in enumerate(self.new[:4]):
                rows.append(self._row(n, "1/1" if i < 2 else "0/1", "Running" if i < 2 else "ContainerCreating",
                                      "0", f"{14 + 7 * i}s"))
        elif phase == "recovering":
            for i, n in enumerate(self.new):
                rows.append(self._row(n, "1/1" if i < 5 else "0/1", "Running" if i < 5 else "ContainerCreating", "0",
                                      f"{2 + (5 - i) // 2}m{10 + 9 * i}s"))
        else:
            for i, n in enumerate(self.new):
                rows.append(self._row(n, "1/1", "Running", "0", f"{minute - 40 + i}m"))
        for n, r in zip(self.workers, self.worker_restarts):
            rows.append(self._row(n, "1/1", "Running", str(r) if phase == "incident" else "0", "6d4h"))
        for n in ("cart-redis-0", "cart-redis-1", "cart-redis-2"):
            rows.append(self._row(n, "1/1", "Running", "0", "19d"))
        rows += [self._row(n, "1/1", "Running", "0", "6d4h") for n in self.inventory]
        rows += [self._row(n, "1/1", "Running", "0", "2d9h") for n in self.search]
        rows.append(self._row(self.ingress, "1/1", "Running", "0", "19d"))
        return _table(
            ["NAME", "READY", "STATUS", "RESTARTS", "AGE", "IP", "NODE", "NOMINATED NODE", "READINESS GATES"], rows
        )

    def jq_pods(self, rng: random.Random) -> str:
        """``kubectl get pods -o json | jq '[.items[] | {...}]'`` while the incident burns (jq prints indent 2)."""
        out = []
        for name in [*self.old, *self.workers, *self.inventory, *self.search]:
            api = name.startswith("checkout-api")
            out.append({
                "name": name,
                "node": self.node[name],
                "podIP": self.ip[name],
                "phase": "Running",
                "image": "registry.shop.internal/checkout-api:v2025.03.14-1" if api else "registry.shop.internal/" + name.rsplit("-", 2)[0] + ":v2025.03.11-1",
                "restarts": rng.randint(3, 9) if api else rng.choice([0, 0, 1]),
                "lastState": {"terminated": {"reason": rng.choice(["OOMKilled", "OOMKilled", "Error"]), "exitCode": rng.choice([137, 137, 1]),
                                             "finishedAt": f"2025-03-14T02:{rng.randint(8, 24):02d}:{rng.randint(0, 59):02d}Z"}} if api else {},
                "resources": {"limits": {"cpu": "2", "memory": "1536Mi"} if api else {"cpu": "1", "memory": "768Mi"},
                              "requests": {"cpu": "500m", "memory": "1Gi"} if api else {"cpu": "250m", "memory": "512Mi"}},
                "conditions": {"Ready": "False" if api and rng.random() < 0.5 else "True", "ContainersReady": "True", "PodScheduled": "True"},
                "startedAt": f"2025-03-14T01:{rng.randint(50, 59):02d}:{rng.randint(0, 59):02d}Z",
            })
        return json.dumps(out, indent=2)

    def top_pods(self, rng: random.Random, phase: str) -> str:
        """``kubectl -n shop top pods`` (hot while the incident burns, idle after the rollback)."""
        hot = phase == "incident"
        rows = [[n, f"{rng.randint(1500, 1990) if hot else rng.randint(120, 480)}m",
                 f"{rng.randint(1380, 1536) if hot else rng.randint(380, 760)}Mi"]
                for n in (self.old if hot else self.new)]
        rows += [[n, f"{rng.randint(80, 400)}m", f"{rng.randint(300, 640)}Mi"] for n in self.workers]
        return _table(["NAME", "CPU(cores)", "MEMORY(bytes)"], rows)


def kubectl_get_events(rng: random.Random, n: int = 38) -> str:
    """``kubectl -n shop get events --sort-by=.lastTimestamp`` (the last ``n`` lines, oldest first)."""
    msgs = [
        ("Warning", "Unhealthy", "pod/checkout-api-7d9f8c6b5d-{s}", "Liveness probe failed: HTTP probe failed with statuscode: 503"),
        ("Warning", "Unhealthy", "pod/checkout-api-7d9f8c6b5d-{s}", "Readiness probe failed: Get \"http://10.42.{n}.{q}:8000/healthz\": context deadline exceeded"),
        ("Warning", "BackOff", "pod/checkout-api-7d9f8c6b5d-{s}", "Back-off restarting failed container checkout-api in pod checkout-api-7d9f8c6b5d-{s}_shop(a41e)"),
        ("Normal", "Killing", "pod/checkout-api-7d9f8c6b5d-{s}", "Container checkout-api failed liveness probe, will be restarted"),
        ("Warning", "OOMKilling", "node/ip-10-0-3-17", "Memory cgroup out of memory: Killed process {x} (python) total-vm:2412344kB, anon-rss:1572864kB"),
        ("Normal", "Pulled", "pod/checkout-api-7d9f8c6b5d-{s}", "Container image \"registry.shop.internal/checkout-api:v2025.03.14-1\" already present on machine"),
        ("Normal", "Created", "pod/checkout-api-7d9f8c6b5d-{s}", "Created container checkout-api"),
        ("Normal", "Started", "pod/checkout-api-7d9f8c6b5d-{s}", "Started container checkout-api"),
        ("Warning", "FailedGetResourceMetric", "horizontalpodautoscaler/checkout-api", "failed to get cpu utilization: did not receive metrics for targeted pods (pods might be unready)"),
        ("Normal", "SuccessfulRescale", "horizontalpodautoscaler/checkout-api", "New size: 6; reason: All metrics below target"),
    ]
    events = []
    for _ in range(n):
        typ, reason, obj, msg = rng.choice(msgs)
        suffix = hex_(rng, 5)
        age = rng.randint(3, 29 * 60 + 59)
        events.append((age, typ, reason, obj.replace("{s}", suffix), fill(msg.replace("{s}", suffix), rng)))
    events.sort(key=lambda e: -e[0])  # oldest first
    rows = [[f"{age // 60}m{age % 60}s" if age >= 60 else f"{age}s", typ, reason, obj, msg] for age, typ, reason, obj, msg in events]
    return _table(["LAST SEEN", "TYPE", "REASON", "OBJECT", "MESSAGE"], rows)


def kubectl_describe_pod(rng: random.Random, *, finished: str, restarts: int = 9) -> str:
    """``kubectl -n shop describe pod checkout-api-7d9f8c6b5d-x2k4q`` of a container that was OOM killed.

    ``finished`` is the time (``HH:MM:SS``) the last container instance died; it is the one value of the output
    a scenario plants.
    """
    uid = hp._uuid(rng)
    cid = hex_(rng, 64)
    image_id = hex_(rng, 64)
    events = [
        ("Warning", "Unhealthy", f"{rng.randint(1, 4)}m{rng.randint(1, 59)}s (x{rng.randint(20, 90)} over 29m)", "kubelet", "Liveness probe failed: HTTP probe failed with statuscode: 503"),
        ("Warning", "BackOff", f"{rng.randint(1, 3)}m{rng.randint(1, 59)}s (x{rng.randint(80, 170)} over 28m)", "kubelet", "Back-off restarting failed container checkout-api in pod checkout-api-7d9f8c6b5d-x2k4q_shop(" + uid + ")"),
        ("Normal", "Pulled", f"{rng.randint(5, 9)}m (x{restarts} over 31m)", "kubelet", "Container image \"registry.shop.internal/checkout-api:v2025.03.14-1\" already present on machine"),
        ("Normal", "Created", f"{rng.randint(5, 9)}m (x{restarts} over 31m)", "kubelet", "Created container checkout-api"),
        ("Normal", "Started", f"{rng.randint(5, 9)}m (x{restarts} over 31m)", "kubelet", "Started container checkout-api"),
        ("Warning", "Unhealthy", f"{rng.randint(1, 3)}m (x{rng.randint(30, 60)} over 30m)", "kubelet", "Readiness probe failed: Get \"http://10.42.3.17:8000/healthz\": context deadline exceeded"),
    ]
    ev = _table(["Type", "Reason", "Age", "From", "Message"], [list(e) for e in events])
    return f"""Name:             checkout-api-7d9f8c6b5d-x2k4q
Namespace:        shop
Priority:         0
Service Account:  checkout-api
Node:             ip-10-0-3-17/10.0.3.17
Start Time:       Fri, 14 Mar 2025 01:52:09 +0000
Labels:           app=checkout-api
                  pod-template-hash=7d9f8c6b5d
                  release=v2025.03.14-1
Annotations:      kubectl.kubernetes.io/restartedAt: 2025-03-14T01:51:58Z
                  prometheus.io/port: 9102
                  prometheus.io/scrape: true
Status:           Running
IP:               10.42.3.17
IPs:
  IP:           10.42.3.17
Controlled By:  ReplicaSet/checkout-api-7d9f8c6b5d
Containers:
  checkout-api:
    Container ID:   containerd://{cid}
    Image:          registry.shop.internal/checkout-api:v2025.03.14-1
    Image ID:       registry.shop.internal/checkout-api@sha256:{image_id}
    Port:           8000/TCP
    Host Port:      0/TCP
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
      Started:      Fri, 14 Mar 2025 02:08:44 +0000
      Finished:     Fri, 14 Mar 2025 {finished} +0000
    Ready:          False
    Restart Count:  {restarts}
    Limits:
      cpu:     2
      memory:  1536Mi
    Requests:
      cpu:      500m
      memory:   1Gi
    Liveness:   http-get http://:8000/healthz delay=20s timeout=2s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8000/readyz delay=10s timeout=2s period=5s #success=1 #failure=3
    Environment Variables from:
      checkout-api-config  ConfigMap  Optional: false
    Environment:
      APP_ENV:               production
      DATABASE_URL:          <set to the key 'url' in secret 'checkout-db'>  Optional: false
      REDIS_URL:             redis://cart-redis.shop.svc:6379/0
      HOLDS_V2:              true
    Mounts:
      /dumps from dumps (rw)
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-{hex_(rng, 5)} (ro)
Conditions:
  Type                        Status
  PodReadyToStartContainers   True
  Initialized                 True
  Ready                       False
  ContainersReady             False
  PodScheduled                True
Volumes:
  dumps:
    Type:       EmptyDir (a temporary directory that shares a pod's lifetime)
    Medium:
    SizeLimit:  2Gi
QoS Class:                   Burstable
Node-Selectors:              <none>
Tolerations:                 node.kubernetes.io/not-ready:NoExecute op=Exists for 300s
                             node.kubernetes.io/unreachable:NoExecute op=Exists for 300s
Events:
{ev}"""


def configmap_yaml(rng: random.Random, *, pool_line: str) -> str:
    """``kubectl -n shop get configmap checkout-api-config -o yaml`` with a ``application.properties`` block."""
    props = [
        "server.port=8000",
        "server.request-timeout-ms=30000",
        "payments.stripe.base-url=https://stripe-proxy.shop.svc:8443",
        f"payments.stripe.timeout-ms={rng.choice([20000, 25000, 30000])}",
        "payments.stripe.retries=2",
        "cache.redis.url=redis://cart-redis.shop.svc:6379/0",
        "cache.redis.timeout-ms=500",
        pool_line,
        "db.pool.overflow=0",
        "db.pool.timeout-s=30",
        "db.pool.recycle-s=1800",
        f"holds.batch-size={rng.choice([50, 100, 200])}",
        "holds.ttl-s=420",
        "feature.holds_v2=true",
        "ledger.url=http://ledger.shop.svc:9000",
        "ledger.timeout-ms=1500",
        "inventory.url=http://inventory-svc.shop.svc:8080",
        f"inventory.timeout-ms={rng.choice([800, 1200, 2500])}",
        "metrics.port=9102",
        "log.level=INFO",
    ]
    body = "\n".join("    " + p for p in props)
    return (
        "apiVersion: v1\ndata:\n  application.properties: |\n" + body + "\n"
        "kind: ConfigMap\nmetadata:\n  annotations:\n    meta.helm.sh/release-name: checkout-api\n"
        "    meta.helm.sh/release-namespace: shop\n  creationTimestamp: \"2024-11-02T09:14:31Z\"\n"
        "  labels:\n    app.kubernetes.io/managed-by: Helm\n  name: checkout-api-config\n  namespace: shop\n"
        f"  resourceVersion: \"{rng.randint(10_000_000, 99_999_999)}\"\n  uid: {hp._uuid(rng)}\n"
    )


def helm_history(rng: random.Random, *, last: int = 42) -> str:
    """``helm -n shop history checkout-api``: 13 revisions; ``last`` is the one that is deployed."""
    rows: list[list[str]] = []
    days = ["Mon Mar 03", "Tue Mar 04", "Wed Mar 05", "Thu Mar 06", "Fri Mar 07", "Mon Mar 10", "Tue Mar 11",
            "Wed Mar 12", "Thu Mar 13", "Thu Mar 13", "Fri Mar 14"]
    versions = ["v2025.03.03-1", "v2025.03.04-2", "v2025.03.05-1", "v2025.03.06-1", "v2025.03.07-2", "v2025.03.10-1",
                "v2025.03.11-1", "v2025.03.12-2", "v2025.03.13-1", "v2025.03.13-2", "v2025.03.14-1"]
    first = last - len(versions) + 1
    for i, (day, ver) in enumerate(zip(days, versions)):
        rev = first + i
        status = "deployed" if rev == last else "superseded"
        when = "01:51:52" if rev == last else f"{rng.randint(8, 18):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
        rows.append([str(rev), f"{day} {when} 2025", status, "checkout-api-3.18.2", ver, "Upgrade complete"])
    return _table(["REVISION", "UPDATED", "STATUS", "CHART", "APP VERSION", "DESCRIPTION"], rows)


def kubectl_exec_ls_dumps(*, name: str) -> str:
    return (
        "total 1482796\n"
        "drwxrwxrwx 2 root   root        4096 Mar 14 02:11 .\n"
        "drwxr-xr-x 1 root   root        4096 Mar 14 01:52 ..\n"
        f"-rw-r--r-- 1 app    app    {1_518_338_048} Mar 14 02:11 {name}\n"
        "-rw-r--r-- 1 app    app          81920 Mar 14 02:11 tracemalloc-top.txt\n"
    )


# =================================================================================================
# JSON dumps
# =================================================================================================
def prometheus_range_json(
    rng: random.Random, *, n_series: int = 8, n_points: int = 240, start_ts: int = 1_741_917_600, trace_id: str | None = None,
) -> str:
    """A Prometheus ``query_range`` response (compact JSON on one line, as ``curl -s`` prints it).

    ``trace_id`` labels one extra series (the slowest request's exemplar)."""
    routes = ["/api/v2/orders", "/api/v2/orders/{id}/holds", "/api/v2/cart", "/api/v2/payments/capture",
              "/api/v2/inventory/{sku}", "/healthz", "/api/v2/search", "/api/v2/users/me"]
    result = []
    for s in range(n_series):
        base = rng.uniform(0.04, 0.4)
        values = []
        for i in range(n_points):
            spike = 6.0 if 40 <= i <= 150 and s in (1, 3) else 1.0
            values.append([start_ts + i * 15, f"{base * spike * rng.uniform(0.7, 1.6):.4f}"])
        result.append({
            "metric": {"__name__": "http_request_duration_seconds", "quantile": "0.99", "job": "checkout-api",
                       "instance": f"10.42.{rng.randint(1, 6)}.{rng.randint(2, 250)}:9102", "route": routes[s % len(routes)]},
            "values": values,
        })
    if trace_id is not None:
        values = [[start_ts + i * 15, f"{rng.uniform(0.2, 1.1):.4f}"] for i in range(0, n_points, 6)]
        values[len(values) // 2][1] = "4.8213"
        result.append({
            "metric": {"__name__": "http_slowest_request_seconds", "job": "checkout-api", "route": "/api/v2/orders/{id}/holds",
                       "trace_id": trace_id},
            "values": values,
        })
    return json.dumps({"status": "success", "data": {"resultType": "matrix", "result": result}}, separators=(",", ":"))


def orders_api_json(rng: random.Random, *, n: int = 220, total_note: str, start_sec: int = 2 * 3600 + 14 * 60) -> str:
    """``curl -s '.../orders?status=failed&since=...'``: a compact JSON page of failed orders plus ``meta``."""
    reasons = ["payment_timeout", "pool_timeout", "stock_conflict", "payment_timeout", "pool_timeout", "ledger_error"]
    data = []
    sec = start_sec
    for i in range(n):
        sec += rng.randint(0, 12)
        data.append({
            "id": f"ORD-{rng.randint(4_417_100, 4_419_900)}",
            "status": "failed",
            "failure_reason": rng.choice(reasons),
            "amount": f"{rng.randint(5, 900)}.{rng.randint(0, 99):02d}",
            "currency": "EUR",
            "created_at": f"2025-03-14T{hms(sec)}Z",
            "customer_id": f"C-{rng.randint(10000, 99999)}",
            "items": rng.randint(1, 6),
            "shipping": {"country": rng.choice(["DE", "FR", "NL", "ES", "IT", "PL"]), "method": rng.choice(["dhl", "dpd", "ups"])},
            "attempts": rng.randint(1, 3),
            "trace_id": hex_(rng, 16),
        })
    meta = {"returned": n, "next_cursor": hex_(rng, 24), "summary": total_note}
    return json.dumps({"data": data, "meta": meta}, separators=(",", ":"))


def kubectl_events_json(rng: random.Random, *, n: int = 36) -> str:
    """``kubectl -n shop get events --field-selector type=Warning -o json`` (indent 4, as kubectl prints it)."""
    reasons = [
        ("Unhealthy", "Liveness probe failed: HTTP probe failed with statuscode: 503"),
        ("Unhealthy", "Readiness probe failed: Get \"http://10.42.3.17:8000/healthz\": context deadline exceeded"),
        ("BackOff", "Back-off restarting failed container checkout-api in pod checkout-api-7d9f8c6b5d-{s}_shop"),
        ("OOMKilling", "Memory cgroup out of memory: Killed process {x} (python) total-vm:2412344kB, anon-rss:1572864kB"),
        ("FailedGetResourceMetric", "failed to get cpu utilization: did not receive metrics for targeted pods"),
    ]
    items = []
    sec = 2 * 3600 + 9 * 60
    for i in range(n):
        sec += rng.randint(5, 90)
        reason, msg = rng.choice(reasons)
        pod = f"checkout-api-7d9f8c6b5d-{hex_(rng, 5)}"
        stamp = f"2025-03-14T{hms(sec)}Z"
        items.append({
            "apiVersion": "v1",
            "count": rng.randint(1, 120),
            "eventTime": None,
            "firstTimestamp": f"2025-03-14T{hms(max(0, sec - rng.randint(60, 900)))}Z",
            "involvedObject": {"apiVersion": "v1", "fieldPath": "spec.containers{checkout-api}", "kind": "Pod",
                               "name": pod, "namespace": "shop", "resourceVersion": str(rng.randint(10_000_000, 99_999_999)),
                               "uid": hp._uuid(rng)},
            "kind": "Event",
            "lastTimestamp": stamp,
            "message": fill(msg.replace("{s}", hex_(rng, 5)), rng),
            "metadata": {"creationTimestamp": stamp, "name": f"{pod}.{hex_(rng, 16)}", "namespace": "shop",
                         "resourceVersion": str(rng.randint(10_000_000, 99_999_999)), "uid": hp._uuid(rng)},
            "reason": reason,
            "reportingComponent": "kubelet",
            "reportingInstance": rng.choice(_ROW_NODES),
            "source": {"component": "kubelet", "host": rng.choice(_ROW_NODES)},
            "type": "Warning",
        })
    return json.dumps({"apiVersion": "v1", "items": items, "kind": "List", "metadata": {"resourceVersion": ""}}, indent=4)


# =================================================================================================
# Source and tests (oncall)
# =================================================================================================
REPOSITORY_HEADER = '''"""Order repository: holds, reservations and their lifecycle (SQLAlchemy, QueuePool)."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Iterator, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("shop.orders.repository")

OPEN_HOLD_STATES = ("reserved", "pending")
'''

FIND_OPEN_HOLDS_LEAKY = '''    def find_open_holds(self, order_id: str) -> list[dict[str, Any]]:
        """Open holds of ``order_id`` (reserved or pending), newest first."""
        session = self._pool.connect()
        rows = session.execute(
            text("select id, sku, qty, state from holds where order_id = :oid and state in :states order by id desc"),
            {"oid": order_id, "states": OPEN_HOLD_STATES},
        ).mappings().all()
        holds = [dict(r) for r in rows]
        session.close()
        return holds
'''

FIND_OPEN_HOLDS_FIXED = '''    def find_open_holds(self, order_id: str) -> list[dict[str, Any]]:
        """Open holds of ``order_id`` (reserved or pending), newest first."""
        with self._pool.connect() as session:
            rows = session.execute(
                text("select id, sku, qty, state from holds where order_id = :oid and state in :states order by id desc"),
                {"oid": order_id, "states": OPEN_HOLD_STATES},
            ).mappings().all()
            return [dict(r) for r in rows]
'''


def repository_source(rng: random.Random, *, patched: bool) -> str:
    """``src/shop/orders/repository.py``: a class whose ``find_open_holds`` leaks the connection on any error."""
    cls = (
        "class OrderRepository:\n"
        '    """Reads and writes order holds through the shared connection pool."""\n\n'
        "    def __init__(self, pool: Engine) -> None:\n        self._pool = pool\n\n"
        + (FIND_OPEN_HOLDS_FIXED if patched else FIND_OPEN_HOLDS_LEAKY)
    )
    return f"{REPOSITORY_HEADER}\n\n{cls}\n\n{hp.python_functions(rng, 22)}\n"


def pytest_run(
    rng: random.Random,
    *,
    failed: Sequence[tuple[str, str, str]] = (),
    passed: int,
    seconds: float = 8.42,
    log_lines: int = 0,
    files: Sequence[str] = ("tests/test_orders.py", "tests/test_repository.py", "tests/test_pricing.py", "tests/test_api.py"),
) -> tuple[str, list[str]]:
    """``pytest -q`` output. ``failed``: ``(node id, exception line, source line)``; returns ``(text, error lines)``.

    The error lines are the ``FAILED ...`` summary lines and the exception lines at the start of a line
    (what the harness' fingerprints see); ``E   ...`` detail lines carry no fingerprint, like a real run's."""
    total = passed + len(failed)
    out = [
        "============================= test session starts ==============================",
        "platform linux -- Python 3.12.3, pytest-8.2.0, pluggy-1.5.0",
        "rootdir: /srv/shop",
        "configfile: pyproject.toml",
        "plugins: cov-5.0.0, asyncio-0.23.6, xdist-3.5.0",
        f"collected {total} items",
        "",
    ]
    done = 0
    for i, f in enumerate(files):
        k = max(1, passed // len(files))
        marks = "." * k
        done += k
        bad = "F" * sum(1 for nodeid, _, _ in failed if nodeid.startswith(f)) if failed else ""
        out.append(f"{f} {marks}{bad}  [{min(100, done * 100 // max(1, total)):3d}%]")
    errors: list[str] = []
    if failed:
        out += ["", "=================================== FAILURES ==================================="]
        for nodeid, exc, src in failed:
            name = nodeid.split("::")[-1]
            out += [
                f"__________________________ {name} __________________________",
                "",
                f"    def {name}(fake_inventory, fake_ledger):",
                "        order = make_order(lines=[(\"SKU-48213\", 2)])",
                f">       {src}",
                f"E       {exc}",
                "",
                f"{nodeid.split('::')[0]}:{rng.randint(20, 220)}: {exc.split(':')[0]}",
            ]
            if log_lines:
                out.append("------------------------------ Captured log call -------------------------------")
                for _ in range(log_lines):
                    out.append(fill("INFO     shop.orders.service:service.py:{k} reserve sku=SKU-{s} qty={q} hold=h-{h}", rng))
            out.append("")
        out.append("=========================== short test summary info ============================")
        for nodeid, exc, _ in failed:
            line = f"FAILED {nodeid} - {exc}"
            out.append(line)
            errors.append(line)
        out.append(f"======================== {len(failed)} failed, {passed} passed in {seconds:.2f}s ========================")
    else:
        out += ["", f"============================= {passed} passed in {seconds:.2f}s =============================="]
    return "\n".join(out) + "\n", errors


def git_log_oneline(rng: random.Random, n: int, *, plant: dict[int, str] | None = None) -> str:
    """``git log --oneline -n``: short hash and subject; ``plant`` replaces the line at an index."""
    subjects = ["tighten validation", "log reservation ids", "batch ledger writes", "bump client timeout",
                "add jitter to backoff", "split holds query", "fix typo in runbook", "drop unused import",
                "cache price quotes", "retry stripe call", "rename helper", "update dependencies"]
    areas = ["orders", "inventory", "ledger", "payments", "holds", "cart", "ci"]
    lines = [f"{hex_(rng, 7)} {rng.choice(areas)}: {rng.choice(subjects)}" for _ in range(n)]
    for i, text in (plant or {}).items():
        lines[i] = text
    return "\n".join(lines) + "\n"


def simple_diff(path: str, old: str, new: str) -> str:
    """The ``diff`` field of a ``patch`` result for replacing ``old`` by ``new`` (one hunk, no context)."""
    old_lines, new_lines = old.rstrip("\n").splitlines(), new.rstrip("\n").splitlines()
    return "\n".join(
        [f"--- a/{path}", f"+++ b/{path}", f"@@ -1,{len(old_lines)} +1,{len(new_lines)} @@"]
        + [f"-{line}" for line in old_lines] + [f"+{line}" for line in new_lines]
    )


def runbook_markdown(rng: random.Random) -> str:
    """``runbooks/checkout-5xx.md``: a short on-call runbook."""
    steps = [
        "Check `kubectl -n shop get pods -o wide` for CrashLoopBackOff or OOMKilled pods.",
        "Tail the API logs: `kubectl -n shop logs deploy/checkout-api --since=20m --tail=500`.",
        "Look at the p99 latency and 5xx rate panels on the Checkout dashboard.",
        "If the last deploy is under an hour old, compare with `helm -n shop history checkout-api`.",
        "Roll back with `helm -n shop rollback checkout-api <revision> --wait` before debugging further.",
        "Page the owning team if the 5xx rate stays above 5% ten minutes after a rollback.",
    ]
    body = [
        "# Checkout 5xx runbook", "",
        "Owner: payments-platform. Pager: `checkout-primary`. Dashboard: Grafana > Shop > Checkout.", "",
        "## First ten minutes", "",
    ] + [f"{i}. {s}" for i, s in enumerate(steps, 1)] + ["", "## Known causes", "",
        "- Payment gateway timeouts (stripe-proxy): check the gateway status page, do not roll back.",
        "- Redis latency on `cart-redis`: check `docker logs` of the sidecar and the memory graph.",
        f"- Slow inventory service: {rng.choice(['raise', 'check'])} `inventory.timeout-ms` only after reading its logs.",
    ]
    body += ["", "## Escalation", "", "SEV-1 only when checkout is down for all regions; everything else is SEV-2.", ""]
    return "\n".join(body)


def slo_json(rng: random.Random, *, note: str) -> str:
    """``curl -s .../slos/<name>``: a compact SLO status document (burn rates, budget) with a free-text note."""
    good = rng.randint(41_000_000, 43_000_000)
    return json.dumps({
        "name": "checkout-availability", "objective": 99.9, "window": "30d", "service": "checkout-api",
        "good": good, "total": good + rng.randint(1_800, 2_400),
        "burn_rate": {"5m": round(rng.uniform(0.1, 0.9), 2), "1h": round(rng.uniform(0.4, 1.4), 2),
                      "6h": round(rng.uniform(1.0, 3.2), 2), "3d": round(rng.uniform(0.6, 1.8), 2)},
        "budget": {"allowed_bad": 43_100, "spent_bad": rng.randint(2_000, 2_500), "remaining_pct": round(rng.uniform(94.0, 96.0), 2)},
        "alerts": [{"name": "SLOBurnRateHigh", "state": "resolved", "since": "2025-03-14T02:47:00Z"}],
        "note": note,
        "updated_at": "2025-03-14T03:09:12Z",
    }, separators=(",", ":"))


# =================================================================================================
# A refactoring session: a small virtual repository (the files the agent reads and patches)
# =================================================================================================
def write_file_result(content: str, *, dirs_created: bool = False) -> str:
    """``write_file`` tool result (``WriteResult.to_dict()``)."""
    return json.dumps(
        {"bytes_written": len(content.encode("utf-8")), "dirs_created": dirs_created, "verified": True}, ensure_ascii=False
    )


def unified_diff(path: str, old: str, new: str, *, context: int = 2) -> str:
    """A unified diff of two texts, as the ``patch`` tool shows it (``--- a/`` ... ``+++ b/`` ...)."""
    import difflib

    return "\n".join(difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=context
    ))


class VirtualRepo:
    """Files by path; ``read`` / ``patch`` / ``write`` keep them consistent so every re-read shows the edits."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = dict(files)

    def read(self, path: str) -> str:
        return self.files[path]

    def patch(self, path: str, old: str, new: str, *, replace_all: bool = False) -> tuple[str, str]:
        """Replace ``old`` by ``new`` in ``path`` (exactly once unless ``replace_all``); returns ``(diff, new text)``."""
        text = self.files[path]
        count = text.count(old)
        assert count >= 1 and (replace_all or count == 1), f"{path}: {count} matches for the old string"
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        self.files[path] = updated
        return unified_diff(path, text, updated), updated

    def write(self, path: str, content: str) -> bool:
        """Create or overwrite ``path``; True when the file is new."""
        created = path not in self.files
        self.files[path] = content
        return created

    def grep(self, token: str, *roots: str) -> list[tuple[str, int, str]]:
        """``(path, line number, line)`` of every line containing ``token`` under ``roots`` (path order)."""
        out = []
        for path in sorted(self.files):
            if roots and not path.startswith(roots):
                continue
            for n, line in enumerate(self.files[path].split("\n"), 1):
                if token in line:
                    out.append((path, n, line))
        return out

    def count_by_file(self, token: str, *roots: str) -> list[tuple[str, int]]:
        found: dict[str, int] = {}
        for path, _n, _line in self.grep(token, *roots):
            found[path] = found.get(path, 0) + 1
        return sorted(found.items())


_FLOWS = ["invoice", "subscription", "backorder", "pickup", "giftcard", "preorder", "bundle", "return", "sample",
          "quote", "dropship", "wishlist", "coupon", "loyalty", "gift", "warranty", "refund", "exchange"]
_TRANSITIONS = [("NEW", "RESERVED"), ("RESERVED", "PAID"), ("PAID", "FULFILLED"), ("NEW", "CANCELLED"),
                ("RESERVED", "CANCELLED"), ("PAID", "CANCELLED")]
_STATUS_TEMPLATES = [
    '''def mark_@B_L@_@N@(order: Order, *, at: datetime | None = None) -> Order:
    """Move ``order`` from @A@ to @B@ for the @N@ flow."""
    if order.status is not @S@.@A@:
        raise InvalidTransition(order.id, order.status, @S@.@B@)
    order.status = @S@.@B@
    order.updated_at = at or _now()
    log.info("order %s: @A_L@ -> @B_L@ (@N@)", order.id)
    return order
''',
    '''def is_@N@_open(order: Order) -> bool:
    """True while the @N@ part of ``order`` can still change."""
    return order.status in (@S@.NEW, @S@.RESERVED)
''',
    '''def @N@_status_filter(*, include_cancelled: bool = False) -> str:
    """Comma separated status values for the @N@ listing query."""
    states = [@S@.NEW.value, @S@.RESERVED.value, @S@.PAID.value, @S@.FULFILLED.value]
    if include_cancelled:
        states.append(@S@.CANCELLED.value)
    return ",".join(states)
''',
    '''def bucket_@N@_by_status(orders: Iterable[Order]) -> dict[@S@, list[Order]]:
    """Group @N@ orders by their status, keeping input order inside each bucket."""
    buckets: dict[@S@, list[Order]] = {state: [] for state in @S@}
    for order in orders:
        buckets[order.status].append(order)
    return buckets
''',
    '''def @N@_label(order: Order) -> str:
    """Human readable status of the @N@ part of ``order`` for the UI."""
    labels = {
        @S@.NEW: "new",
        @S@.RESERVED: "stock reserved",
        @S@.PAID: "paid",
        @S@.FULFILLED: "shipped",
        @S@.CANCELLED: "cancelled",
    }
    return labels[order.status]
''',
    '''def require_@N@_state(*allowed: @S@):
    """Decorator: the wrapped @N@ handler only runs for orders in one of ``allowed``."""
    def wrap(fn):
        def inner(order: Order, *args, **kwargs):
            if order.status not in allowed:
                raise InvalidTransition(order.id, order.status, allowed)
            return fn(order, *args, **kwargs)
        return inner
    return wrap
''',
]


def status_functions(rng: random.Random, n: int, enum: str = "OrderStatus") -> str:
    """``n`` functions that use the order status enum in different ways (unique names)."""
    flows = list(_FLOWS)
    rng.shuffle(flows)
    parts = []
    for i in range(n):
        tpl = _STATUS_TEMPLATES[i % len(_STATUS_TEMPLATES)] if i < len(_STATUS_TEMPLATES) else rng.choice(_STATUS_TEMPLATES)
        a, b = rng.choice(_TRANSITIONS)
        parts.append(
            tpl.replace("@S@", enum).replace("@N@", flows[i % len(flows)]).replace("@A@", a).replace("@B@", b)
            .replace("@A_L@", a.lower()).replace("@B_L@", b.lower())
        )
    return "\n\n".join(parts)


MODELS_HEADER = '''"""Order domain models and their lifecycle helpers."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Sequence

log = logging.getLogger("shop.orders.models")


class OrderStatus(str, Enum):
    """Lifecycle of an order."""

    NEW = "new"
    RESERVED = "reserved"
    PAID = "paid"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class InvalidTransition(RuntimeError):
    """An order was asked to move to a state it cannot reach from where it is."""


@dataclass
class Line:
    sku: str
    qty: int
    unit_price: str


@dataclass
class Order:
    id: str
    customer_id: str
    lines: list[Line] = field(default_factory=list)
    status: OrderStatus = OrderStatus.NEW
    updated_at: datetime | None = None

    @property
    def total(self) -> Decimal:
        return sum((Decimal(l.unit_price) * l.qty for l in self.lines), Decimal("0"))


def _now() -> datetime:
    return datetime.now(timezone.utc)
'''

PRICING_HEADER_CONSTANTS = '''TAX_ROUNDING = 4  # decimal places kept while summing; rounded to 2 only at the very end
DISCOUNT_TIER_CAP = Decimal("0.35")  # marketing sign-off: stacked discounts never exceed 35 percent (SHOP-3917)
TAX_RATES = {
    "DE": Decimal("0.19"),
    "FR": Decimal("0.20"),
    "NL": Decimal("0.21"),
    "ES": Decimal("0.21"),
    "IT": Decimal("0.22"),
    "PL": Decimal("0.23"),
}
'''

PRICING_FUNCTIONS = '''def compute_subtotal(lines: Sequence[Line]) -> Decimal:
    """Sum of unit price times quantity over ``lines``, kept at TAX_ROUNDING places."""
    total = Decimal("0")
    step = Decimal(1).scaleb(-TAX_ROUNDING)
    for line in lines:
        if line.qty <= 0:
            raise ValueError(f"line {line.sku}: quantity must be positive")
        total += (Decimal(line.unit_price) * line.qty).quantize(step)
    return total


def apply_discount_tiers(subtotal: Decimal, tiers: Sequence[Tier]) -> Decimal:
    """Apply every tier whose threshold ``subtotal`` reaches; the stacked discount never exceeds the cap."""
    rate = Decimal("0")
    for tier in sorted(tiers, key=lambda t: t.threshold):
        if subtotal >= tier.threshold:
            rate += tier.rate
    rate = min(rate, DISCOUNT_TIER_CAP)
    discount = (subtotal * rate).quantize(Decimal(1).scaleb(-TAX_ROUNDING))
    log.debug("discount rate=%s subtotal=%s discount=%s", rate, subtotal, discount)
    return subtotal - discount


def tax_for_region(amount: Decimal, region: str) -> Decimal:
    """Tax due on ``amount`` for ``region`` (an ISO country code); unknown regions pay the highest rate."""
    rate = TAX_RATES.get(region.upper(), max(TAX_RATES.values()))
    return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
'''


def shop_module(
    rng: random.Random, *, doc: str, imports: str, n_status: int, n_filler: int, pricing: bool = False,
    sql: bool = False,
) -> str:
    """A source module of the shop: imports, optional pricing block, status functions and filler helpers."""
    head = f'"""{doc}"""\nfrom __future__ import annotations\n\nimport logging\nfrom datetime import datetime\nfrom decimal import ROUND_HALF_UP, Decimal\n{imports}\n\nlog = logging.getLogger(__name__)\n'
    blocks = [head]
    if pricing:
        blocks += [PRICING_HEADER_CONSTANTS, PRICING_FUNCTIONS]
    if sql:
        blocks.append(
            "REVENUE_SQL = (\n"
            "    \"select date_trunc('day', created_at) as day, sum(total) as revenue from orders \"\n"
            "    f\"where status in ('{OrderStatus.PAID.value}', '{OrderStatus.FULFILLED.value}') group by 1 order by 1\"\n"
            ")\n"
            "CANCELLED_SQL = f\"select id, total from orders where status = '{OrderStatus.CANCELLED.value}' and created_at >= %s\"\n"
        )
    blocks.append(status_functions(rng, n_status))
    blocks.append(hp.python_functions(rng, n_filler))
    return "\n\n".join(blocks) + "\n"


def refactor_files(rng_for: Callable[[str], random.Random]) -> dict[str, str]:
    """The repository the refactoring session starts from (about 150 KB over 7 files)."""
    imports = "from shop.orders.models import InvalidTransition, Line, Order, OrderStatus\nfrom typing import Iterable, Sequence\n"
    files = {
        "src/shop/orders/models.py": MODELS_HEADER + "\n\n" + status_functions(rng_for("models"), 10) + "\n\n" + hp.python_functions(rng_for("models-filler"), 22) + "\n",
        "src/shop/orders/service.py": shop_module(
            rng_for("service"), doc="Order service: reservation, pricing and payment orchestration.",
            imports=imports + "from shop.orders.tiers import Tier\n", n_status=14, n_filler=34, pricing=True),
        "src/shop/orders/api.py": shop_module(
            rng_for("api"), doc="HTTP handlers of the orders API.", imports=imports, n_status=12, n_filler=26),
        "src/shop/cart.py": shop_module(
            rng_for("cart"), doc="The shopping cart.", imports=imports, n_status=8, n_filler=24),
        "src/shop/checkout.py": shop_module(
            rng_for("checkout"), doc="Checkout flow: quote, reserve, capture.", imports=imports, n_status=10, n_filler=28),
        "src/shop/reports.py": shop_module(
            rng_for("reports"), doc="Revenue and cancellation reports.", imports=imports, n_status=6, n_filler=24, sql=True),
        "tests/test_orders.py": order_tests(rng_for("tests")),
    }
    return files


def order_tests(rng: random.Random, n: int = 34) -> str:
    """``tests/test_orders.py``: fixtures plus ``n`` tests that move orders through the status enum."""
    flows = list(_FLOWS)
    rng.shuffle(flows)
    out = [
        '"""Tests for the order lifecycle."""\nimport pytest\n\nfrom shop.orders.models import InvalidTransition, OrderStatus\n'
        "from tests.fakes import make_order\n\n"
        "@pytest.fixture\ndef order():\n    return make_order(status=OrderStatus.NEW)\n"
    ]
    for i in range(n):
        a, b = rng.choice(_TRANSITIONS)
        name = flows[i % len(flows)]
        out.append(
            f"def test_{name}_{a.lower()}_to_{b.lower()}_{i}(order):\n"
            f"    order.status = OrderStatus.{a}\n"
            f"    moved = move_{name}(order, OrderStatus.{b})\n"
            f"    assert moved.status is OrderStatus.{b}\n"
            f"    assert moved.updated_at is not None\n"
            + (f"    with pytest.raises(InvalidTransition):\n        move_{name}(moved, OrderStatus.NEW)\n" if i % 3 == 0 else "")
        )
    return "\n\n".join(out) + "\n"


def engine_module() -> str:
    """``src/shop/pricing/engine.py``: the pricing block moved out of the order service, discount cap lowered."""
    return (
        '"""Pricing: subtotals, discount tiers and tax."""\nfrom __future__ import annotations\n\nimport logging\n'
        "from decimal import ROUND_HALF_UP, Decimal\nfrom typing import Sequence\n\n"
        "from shop.orders.models import Line\nfrom shop.orders.tiers import Tier\n\nlog = logging.getLogger(__name__)\n\n"
        + PRICING_HEADER_CONSTANTS.replace('Decimal("0.35")  # marketing sign-off: stacked discounts never exceed 35 percent (SHOP-3917)',
                                           'Decimal("0.30")  # lowered from 0.35 when the pricing code moved here')
        + "\n\n" + PRICING_FUNCTIONS
    )


def pricing_tests() -> str:
    return '''"""Tests for the pricing engine."""
from decimal import Decimal

import pytest

from shop.orders.models import Line
from shop.orders.tiers import Tier
from shop.pricing.engine import DISCOUNT_TIER_CAP, apply_discount_tiers, compute_subtotal, tax_for_region


def test_compute_subtotal_sums_lines():
    lines = [Line("SKU-1", 2, "10.00"), Line("SKU-2", 1, "5.50")]
    assert compute_subtotal(lines) == Decimal("25.5000")


def test_compute_subtotal_rejects_empty_quantity():
    with pytest.raises(ValueError):
        compute_subtotal([Line("SKU-1", 0, "10.00")])


def test_discount_tier_cap():
    tiers = [Tier(Decimal("100"), Decimal("0.20")), Tier(Decimal("200"), Decimal("0.20"))]
    assert apply_discount_tiers(Decimal("500"), tiers) == Decimal("500") * (1 - DISCOUNT_TIER_CAP)


def test_tax_for_region_known_and_unknown():
    assert tax_for_region(Decimal("100"), "DE") == Decimal("19.00")
    assert tax_for_region(Decimal("100"), "XX") == Decimal("23.00")


def test_tax_rounds_half_up():
    assert tax_for_region(Decimal("0.50"), "DE") == Decimal("0.10")


def test_tiers_below_threshold_do_nothing():
    assert apply_discount_tiers(Decimal("10"), [Tier(Decimal("100"), Decimal("0.10"))]) == Decimal("10")
'''


def pytest_collect_errors(
    modules: Sequence[tuple[str, int]], symbol: str, source: str, *, module: str = "shop.orders.models",
) -> tuple[str, list[str]]:
    """``pytest -q`` output when ``len(modules)`` test modules cannot be collected (``ImportError``).

    ``modules``: ``(path, line of the import)``. Returns ``(text, ERROR lines)``."""
    out = ["==================================== ERRORS ===================================="]
    errors: list[str] = []
    for path, lineno in modules:
        out += [
            f"_______________ ERROR collecting {path} _______________",
            f"ImportError while importing test module '/srv/shop/{path}'.",
            "Hint: make sure your test modules/packages have valid Python names.",
            "Traceback:",
            "/usr/local/lib/python3.12/importlib/__init__.py:90: in import_module",
            "    return _bootstrap._gcd_import(name[level:], package, level)",
            f"{path}:{lineno}: in <module>",
            f"    {source}",
            f"E   ImportError: cannot import name '{symbol}' from '{module}' (/srv/shop/src/{module.replace('.', '/')}.py)",
        ]
    out.append("=========================== short test summary info ============================")
    for path, _ in modules:
        line = f"ERROR {path}"
        out.append(line)
        errors.append(line)
    out.append(f"!!!!!!!!!!!!!!!!!!! Interrupted: {len(modules)} errors during collection !!!!!!!!!!!!!!!!!!!!")
    return "\n".join(out) + "\n", errors


def ruff_output(findings: Sequence[tuple[str, int, str]]) -> str:
    """``ruff check`` output: ``path:line:col: F401 `x` imported but unused`` rows and the summary."""
    rows = [f"{path}:{line}:{rng_col(i)}: F401 [*] `{name}` imported but unused" for i, (path, line, name) in enumerate(findings)]
    return "\n".join(rows + [f"Found {len(findings)} errors.", f"[*] {len(findings)} fixable with the `--fix` option."]) + "\n"


def rng_col(i: int) -> int:
    return 8 + (i * 7) % 31
