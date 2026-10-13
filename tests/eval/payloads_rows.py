"""Tool-result generators for the ``smallrows`` and ``i18n`` replay scenarios (``scenarios_rows``).

Same rules as ``payloads_extra`` (seeded ``random.Random`` only, no clock, no ``hash()``, byte-identical per seed;
synthetic bodies that follow the real tools' formats closely enough for the engines' text heuristics).

``smallrows``: per-service results of 800-3,000 characters (a service manifest, its requirement pins, a health
document, a short log, a ``git log``, a TODO grep, ``ls -la``, a test summary). ``i18n``: logs and JSON documents in
Chinese, Japanese, Korean, Arabic and Russian, written the way the real tools write them (``ensure_ascii=False``).
"""
from __future__ import annotations

import json
import random
from collections.abc import Sequence

import payloads_extra as pe
from payloads_extra import Log, LogStyle, hms

SERVICES = (
    "auth", "billing", "catalog", "cart", "checkout", "inventory", "ledger",
    "mailer", "notify", "orders", "payments", "search", "shipping", "tax",
)
_OWNERS = {
    "auth": "team-identity", "billing": "team-money", "catalog": "team-discovery", "cart": "team-checkout",
    "checkout": "team-checkout", "inventory": "team-supply", "ledger": "team-money", "mailer": "team-comms",
    "notify": "team-comms", "orders": "team-orders", "payments": "team-money", "search": "team-discovery",
    "shipping": "team-supply", "tax": "team-money",
}
_PORTS = {s: 8080 + i for i, s in enumerate(SERVICES)}

# ---- pinned requirements --------------------------------------------------------------------------
_LIBS = [
    ("fastapi", "0.110.0"), ("uvicorn", "0.29.0"), ("starlette", "0.36.3"), ("pydantic", "2.6.4"),
    ("sqlalchemy", "2.0.29"), ("psycopg2-binary", "2.9.9"), ("alembic", "1.13.1"), ("redis", "5.0.3"),
    ("httpx", "0.27.0"), ("tenacity", "8.2.3"), ("structlog", "24.1.0"), ("prometheus-client", "0.20.0"),
    ("opentelemetry-sdk", "1.24.0"), ("orjson", "3.10.0"), ("python-dateutil", "2.9.0.post0"), ("boto3", "1.34.69"),
    ("celery", "5.3.6"), ("kombu", "5.3.5"), ("cryptography", "42.0.5"), ("pyjwt", "2.8.0"), ("jinja2", "3.1.3"),
    ("gunicorn", "21.2.0"), ("ujson", "5.9.0"), ("requests", "2.31.0"), ("attrs", "23.2.0"), ("anyio", "4.3.0"),
    ("certifi", "2024.2.2"), ("idna", "3.6"), ("h11", "0.14.0"), ("sniffio", "1.3.1"), ("click", "8.1.7"),
    ("typing-extensions", "4.10.0"), ("greenlet", "3.0.3"), ("pyyaml", "6.0.1"), ("markupsafe", "2.1.5"),
]


def requirements_txt(rng: random.Random, svc: str, *, n: int = 30, plant: dict[int, str] | None = None) -> str:
    """``services/<svc>/requirements.txt``: pinned libraries, a header comment, a few ``-e`` / comment lines."""
    libs = rng.sample(_LIBS, min(n, len(_LIBS)))
    libs.sort()
    lines = [f"# services/{svc}/requirements.txt  (pip-compile, python 3.12)", "--index-url https://pypi.shop.example/simple", ""]
    for i, (name, ver) in enumerate(libs):
        patch = rng.randint(0, 3)
        major, minor, *_rest = ver.split(".")
        lines.append(f"{name}=={ver if patch == 0 else f'{major}.{minor}.{patch}'}")
        lines.append(f"    # via {rng.choice(['-r requirements.in', 'fastapi', 'httpx', 'sqlalchemy', 'shop-common', 'celery'])}")
    lines += ["", f"-e ./libs/shop-common  # shared models for {svc}"]
    for idx, text in (plant or {}).items():
        lines.insert(min(idx, len(lines)), text)
    return "\n".join(lines) + "\n"


# ---- manifest -----------------------------------------------------------------------------------------
def service_yaml(rng: random.Random, svc: str, *, plant: dict[str, str] | None = None) -> str:
    """``services/<svc>/service.yaml``: owner, image, resources, environment, probes, alerts (900-2,000 chars)."""
    plant = plant or {}
    cpu = rng.choice(["250m", "500m", "1", "2"])
    mem = rng.choice(["256Mi", "384Mi", "512Mi", "768Mi", "1Gi"])
    lines = [
        f"# services/{svc}/service.yaml",
        f"name: {svc}",
        f"owner: {_OWNERS[svc]}",
        f"tier: {rng.choice([1, 1, 2, 3])}",
        "runtime:",
        f"  image: registry.shop.example/{svc}:2025.03.{rng.randint(1, 13)}-{pe.hex_(rng, 7)}",
        f"  port: {_PORTS[svc]}",
        f"  replicas: {rng.choice([2, 3, 4, 6])}",
        "  resources:",
        f"    requests: {{cpu: {cpu}, memory: {mem}}}",
        f"    limits: {{cpu: \"{rng.choice(['1', '2', '4'])}\", memory: {rng.choice(['768Mi', '1Gi', '2Gi'])}}}",
        "env:",
        "  LOG_LEVEL: info",
        f"  DB_URL: postgres://{svc}@pgbouncer.shop.svc:6432/{svc}",
        f"  DB_POOL_MAX: {plant.get('DB_POOL_MAX', rng.choice([10, 20, 25, 30, 40]))}",
        f"  REDIS_URL: redis://cart-redis.shop.svc:6379/{rng.randint(0, 5)}",
        f"  REQUEST_TIMEOUT_MS: {plant.get('REQUEST_TIMEOUT_MS', rng.choice([1500, 2000, 3000, 5000]))}",
        "  FEATURE_FLAGS_URL: http://flags.shop.svc/api/v1",
    ]
    for key in sorted(k for k in plant if k not in ("DB_POOL_MAX", "REQUEST_TIMEOUT_MS")):
        lines.append(f"  {key}: {plant[key]}")
    lines += [
        f"  CACHE_TTL_S: {rng.choice([30, 60, 300, 900])}",
        f"  RETRY_MAX: {rng.choice([2, 3, 5])}",
        f"  RETRY_BACKOFF_MS: {rng.choice([50, 100, 250])}",
        f"  SENTRY_DSN: https://{pe.hex_(rng, 16)}@sentry.shop.example/{rng.randint(10, 99)}",
        "  OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector.monitoring.svc:4317",
        f"  MAX_BODY_BYTES: {rng.choice([262144, 1048576, 4194304])}",
        "probes:",
        "  liveness: {path: /livez, period_s: 10, failure_threshold: 3}",
        f"  readiness: {{path: /healthz, period_s: 5, failure_threshold: {rng.choice([2, 3])}}}",
        "deps:",
    ] + [f"  - {d}" for d in rng.sample(["postgres", "redis", "flags", "stripe-proxy", "inventory", "ledger", "smtp", "s3"], rng.randint(2, 5))]
    if rng.random() < 0.7:
        lines += ["slo:", f"  availability: {rng.choice([99.5, 99.9, 99.95])}", f"  latency_p99_ms: {rng.choice([250, 400, 800, 1200])}"]
    if rng.random() < 0.6:
        lines += ["alerts:", f"  - name: {svc}-5xx", "    expr: rate(http_requests_total{code=~\"5..\"}[5m]) > 0.05", "    for: 5m",
                  f"  - name: {svc}-saturation", "    expr: db_pool_in_use / db_pool_size > 0.9", "    for: 10m"]
    lines += [f"dashboards: [grafana/d/{svc}-overview, grafana/d/{svc}-db]", f"runbook: https://wiki.shop.example/runbooks/{svc}"]
    return "\n".join(lines) + "\n"


# ---- health -------------------------------------------------------------------------------------------------
def healthz_json(rng: random.Random, svc: str, *, schema: str | None = None, degraded: str | None = None) -> str:
    """``curl -s http://<svc>.internal/healthz``: status, version, dependency checks (800-1,500 chars)."""
    deps = {}
    for name in rng.sample(["postgres", "redis", "flags", "stripe-proxy", "smtp", "s3", "inventory"], rng.randint(3, 5)):
        deps[name] = {"status": "ok", "latency_ms": rng.randint(1, 80), "checked_at": f"2025-03-14T02:{rng.randint(10, 59):02d}:{rng.randint(0, 59):02d}Z"}
    if degraded:
        deps[degraded] = {"status": "degraded", "latency_ms": rng.randint(900, 4000), "error": "timeout after 1000ms"}
    doc = {
        "service": svc, "status": "degraded" if degraded else "ok",
        "version": f"2025.03.{rng.randint(1, 13)}", "commit": pe.hex_(rng, 12),
        "uptime_s": rng.randint(3600, 900000), "schema_version": schema or f"2025.03.{rng.randint(1, 4)}-{svc[:2]}-{rng.randint(10, 99)}",
        "checks": deps,
        "pool": {"size": rng.choice([10, 20, 25, 30]), "in_use": rng.randint(0, 9), "waiting": rng.randint(0, 3)},
        "feature_flags": {k: rng.choice([True, False]) for k in ("holds_v2", "price_quotes_cache", "stripe_retries_v2", "search_rerank")},
        "recent_errors": [{"at": f"2025-03-14T02:{rng.randint(10, 59):02d}:{rng.randint(0, 59):02d}Z", "kind": rng.choice(["timeout", "reset", "429"]),
                           "count": rng.randint(1, 9)} for _ in range(3)],
        "build": {"python": "3.12.3", "image": f"registry.shop.example/{svc}", "built_at": "2025-03-13T16:02:41Z"},
    }
    return json.dumps(doc, indent=2) + "\n"


# ---- git / grep / ls / pytest ----------------------------------------------------------------------------------------
def git_log_for(rng: random.Random, svc: str, n: int = 24, *, plant: dict[int, str] | None = None) -> str:
    subjects = [f"{svc}: tighten validation", f"{svc}: bump pydantic", f"{svc}: add retry to client", f"{svc}: log request ids",
                f"{svc}: fix flaky test", f"{svc}: cache config lookups", f"{svc}: drop unused import", f"{svc}: batch writes",
                f"{svc}: handle 429 from upstream", f"{svc}: tune pool size", f"{svc}: upgrade base image", f"{svc}: rename helper"]
    lines = [f"{pe.hex_(rng, 7)} {rng.choice(subjects)} (#{rng.randint(1800, 2399)})" for _ in range(n)]
    for i, text in (plant or {}).items():
        lines[i] = text
    return "\n".join(lines) + "\n"


def todo_grep(rng: random.Random, svc: str, n: int = 16, *, plant: dict[int, str] | None = None) -> str:
    """``rg -n 'TODO|FIXME' services/<svc>``: ``path:line:text`` rows (800-1,600 chars)."""
    files = [f"services/{svc}/{m}.py" for m in ("api", "client", "service", "models", "settings", "worker", "cache")]
    texts = ["# TODO: handle the empty response from upstream", "# FIXME: this swallows the timeout", "# TODO: move to settings",
             "# TODO(team): remove after the migration", "# FIXME: n+1 query when listing", "# TODO: add a test for the 429 path",
             "# TODO: make the backoff configurable", "# FIXME: log the request id here"]
    rows = sorted((rng.choice(files), rng.randint(8, 420), "    " + rng.choice(texts)) for _ in range(n))
    lines = [f"{p}:{ln}:{t}" for p, ln, t in rows]
    for i, text in (plant or {}).items():
        lines[min(i, len(lines) - 1)] = text
    return "\n".join(lines) + "\n"


def ls_la(rng: random.Random, svc: str) -> str:
    names = ["Dockerfile", "Makefile", "README.md", "alembic.ini", "api.py", "client.py", "models.py", "requirements.txt", "service.yaml",
             "settings.py", "tests", "worker.py", "migrations", ".dockerignore", "pyproject.toml"]
    rows = ["total %d" % rng.randint(40, 180),
            "drwxr-xr-x 5 deploy deploy 4096 Mar 13 17:44 .", "drwxr-xr-x 16 deploy deploy 4096 Mar 12 08:30 .."]
    for n in names:
        d = "d" if n in ("tests", "migrations") else "-"
        rows.append(f"{d}rw-r--r-- 1 deploy deploy {rng.randint(180, 18000):>5} Mar {rng.randint(1, 13):>2} {rng.randint(8, 18):02d}:{rng.randint(0, 59):02d} {n}")
    return "\n".join(rows) + "\n"


def pytest_summary(rng: random.Random, svc: str, *, passed: int, failed: Sequence[str] = ()) -> tuple[str, list[str]]:
    """A short ``pytest -q`` run of one service's tests; returns ``(text, FAILED lines)``."""
    files = ["test_api.py", "test_client.py", "test_models.py", "test_settings.py", "test_worker.py"]
    out = ["============================= test session starts ==============================", "platform linux -- Python 3.12.3, pytest-8.2.0",
           f"rootdir: /srv/shop/services/{svc}", f"collected {passed + len(failed)} items", ""]
    done = 0
    for f in files:
        k = max(1, passed // len(files))
        done += k
        out.append(f"tests/{f} {'.' * k}  [{min(100, done * 100 // max(1, passed + len(failed))):3d}%]")
    errs: list[str] = []
    out += ["", "=============================== warnings summary ===============================",
            f"services/{svc}/tests/test_client.py::test_retries_on_429", "  /usr/local/lib/python3.12/site-packages/httpx/_client.py:1255: DeprecationWarning: "
            "The 'app' shortcut is now deprecated. Use the explicit style 'transport=WSGITransport(app=...)' instead.",
            f"services/{svc}/tests/test_settings.py::test_env_overrides", "  /usr/local/lib/python3.12/site-packages/pydantic/_internal/_config.py:291: "
            "PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead.",
            "-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html"]
    if failed:
        out += ["", "=========================== short test summary info ============================"]
        for name in failed:
            line = f"FAILED services/{svc}/tests/test_api.py::{name} - AssertionError: expected 200, got 503"
            out.append(line)
            errs.append(line)
        out.append(f"======================== {len(failed)} failed, {passed} passed in {rng.uniform(1.2, 4.9):.2f}s ========================")
    else:
        out.append(f"============================= {passed} passed in {rng.uniform(1.2, 4.9):.2f}s ==============================")
    return "\n".join(out) + "\n", errs


# ---- a short container log ------------------------------------------------------------------------------------------------
def service_log(rng: random.Random, svc: str, *, n_lines: int = 28, n_errors: int = 3, start_sec: int = 2 * 3600 + 30 * 60,
                plants: dict[int, pe.Template] | None = None) -> Log:
    """``docker logs --tail n <svc>``: request lines plus ``n_errors`` error lines from templates specific to ``svc``."""
    style = LogStyle(
        pe._fmt_app,
        info=(("INFO", f"{svc}.http", "{m} {p} {st} {ms}ms trace_id={h16}"), ("INFO", f"{svc}.http", "{m} {p} {st} {ms}ms trace_id={h16}"),
              ("INFO", f"{svc}.worker", "job done id={h} dur_ms={ms}"), ("INFO", f"{svc}.cache", "refreshed {q} keys in {ms}ms")),
        warn=(("WARN", f"{svc}.http", "slow request {m} {p} dur_ms={ms}"),),
        errors=(
            ("ERROR", f"{svc}.db", f"psycopg2.OperationalError: connection to server for {svc} was lost (pid={{pid}})"),
            ("ERROR", f"{svc}.client", f"UpstreamTimeout: {svc} call to inventory exceeded {{ms}}ms (attempt={{a}})"),
            ("CRITICAL", f"{svc}.main", f"{svc} readiness probe failed {{k}} times in a row"),
        ),
        warn_rate=0.12, step=3,
    )
    return pe.make_log(rng, style, n_lines=n_lines, start_sec=start_sec, burst=(0.3, 0.9), n_errors=n_errors, plants=plants)


def deploy_describe(rng: random.Random, svc: str) -> str:
    """``kubectl -n shop describe deploy/<svc>`` (2,000-2,800 chars)."""
    rs = f"{svc}-{pe.hex_(rng, 9)}"
    lines = [
        f"Name:                   {svc}", "Namespace:              shop", f"CreationTimestamp:      Mon, {rng.randint(1, 28):02d} Jan 2025 09:{rng.randint(10, 59)}:11 +0000",
        f"Labels:                 app={svc}", f"                        team={_OWNERS[svc]}", f"Annotations:            deployment.kubernetes.io/revision: {rng.randint(12, 90)}",
        f"Selector:               app={svc}", f"Replicas:               {rng.choice([3, 4, 6])} desired | {rng.choice([3, 4, 6])} updated | {rng.choice([3, 4, 6])} total | {rng.choice([3, 4, 6])} available | 0 unavailable",
        "StrategyType:           RollingUpdate", "MinReadySeconds:         0", "RollingUpdateStrategy:  25% max unavailable, 25% max surge", "Pod Template:",
        f"  Labels:  app={svc}", "  Containers:", f"   {svc}:", f"    Image:      registry.shop.example/{svc}:2025.03.{rng.randint(1, 13)}-{pe.hex_(rng, 7)}",
        f"    Port:       {_PORTS[svc]}/TCP", "    Host Port:  0/TCP", "    Limits:", f"      cpu:     {rng.choice(['1', '2'])}", f"      memory:  {rng.choice(['768Mi', '1Gi', '2Gi'])}",
        "    Requests:", "      cpu:      250m", "      memory:   384Mi", f"    Liveness:   http-get http://:{_PORTS[svc]}/livez delay=10s timeout=1s period=10s #success=1 #failure=3",
        f"    Readiness:  http-get http://:{_PORTS[svc]}/healthz delay=5s timeout=1s period=5s #success=1 #failure=3", "    Environment Variables from:",
        f"      {svc}-config  ConfigMap  Optional: false", f"      {svc}-secrets  Secret  Optional: false", "    Environment:", "      LOG_LEVEL:  info",
        "  Volumes:  <none>", "Conditions:", "  Type           Status  Reason", "  ----           ------  ------",
        "  Available      True    MinimumReplicasAvailable", "  Progressing    True    NewReplicaSetAvailable", f"OldReplicaSets:  {svc}-{pe.hex_(rng, 9)} (0/0 replicas created)",
        f"NewReplicaSet:   {rs} ({rng.choice([3, 4, 6])}/{rng.choice([3, 4, 6])} replicas created)", "Events:", "  Type    Reason             Age   From                   Message",
        "  ----    ------             ----  ----                   -------",
        f"  Normal  ScalingReplicaSet  {rng.randint(2, 40)}d   deployment-controller  Scaled up replica set {rs} to {rng.choice([3, 4, 6])}",
        f"  Normal  ScalingReplicaSet  {rng.randint(2, 40)}d   deployment-controller  Scaled down replica set {svc}-{pe.hex_(rng, 9)} to 0",
    ]
    return "\n".join(lines) + "\n"


def metrics_head(rng: random.Random, svc: str, n: int = 16) -> str:
    """``curl -s http://<svc>/metrics | head -n 48``: Prometheus text exposition (HELP / TYPE / samples)."""
    out = []
    for name, kind, help_ in (("http_requests_total", "counter", "Total HTTP requests"), ("http_request_duration_seconds_sum", "counter", "Request latency sum"),
                              ("db_pool_in_use", "gauge", "Connections checked out"), ("db_pool_size", "gauge", "Configured pool size"),
                              ("process_resident_memory_bytes", "gauge", "Resident memory size in bytes"), ("cache_hits_total", "counter", "Cache hits")):
        out += [f"# HELP {name} {help_}", f"# TYPE {name} {kind}"]
        if name == "http_requests_total":
            out += [f'{name}{{service="{svc}",method="{m}",code="{c}"}} {rng.randint(10, 900000)}' for m, c in
                    (("GET", "200"), ("GET", "404"), ("POST", "200"), ("POST", "500"), ("GET", "503"))]
        else:
            out += [f'{name}{{service="{svc}"}} {rng.randint(1, 900000)}']
    return "\n".join(out[:n * 3]) + "\n"


def config_dump_json(rng: random.Random, svc: str) -> str:
    """``curl -s http://<svc>/debug/config``: the effective settings (1,800-2,600 chars of indented JSON)."""
    doc = {
        "service": svc, "env": "prod", "region": "eu-1",
        "http": {"port": _PORTS[svc], "workers": rng.choice([2, 4, 8]), "keepalive_s": rng.choice([5, 15, 30]), "max_body_bytes": rng.choice([262144, 1048576])},
        "db": {"host": "pgbouncer.shop.svc", "port": 6432, "name": svc, "pool_max": rng.choice([10, 20, 25, 30, 40]), "pool_timeout_s": rng.choice([5, 10, 30]),
               "statement_timeout_ms": rng.choice([2000, 5000, 15000])},
        "cache": {"url": "redis://cart-redis.shop.svc:6379", "ttl_s": rng.choice([30, 60, 300]), "namespace": f"{svc}:v{rng.randint(1, 4)}"},
        "retry": {"max": rng.choice([2, 3, 5]), "backoff_ms": rng.choice([50, 100, 250]), "jitter": True,
                  "retry_on": rng.sample(["timeout", "reset", "429", "502", "503"], 3)},
        "limits": {"rps": rng.choice([100, 250, 500]), "burst": rng.choice([20, 50, 100]), "per_client_rps": rng.choice([5, 10, 25])},
        "observability": {"otlp": "http://otel-collector.monitoring.svc:4317", "sample_rate": rng.choice([0.01, 0.05, 0.1]), "log_level": "info"},
        "features": {k: rng.choice([True, False]) for k in ("holds_v2", "price_quotes_cache", "stripe_retries_v2", "search_rerank", "new_checkout_flow")},
        "upstreams": {u: {"url": f"http://{u}.shop.svc", "timeout_ms": rng.choice([500, 1000, 2000, 3000])} for u in rng.sample(["inventory", "ledger", "orders", "payments", "tax"], 3)},
    }
    return json.dumps(doc, indent=2) + "\n"


def helm_list(rng: random.Random) -> str:
    rows = [["NAME", "NAMESPACE", "REVISION", "UPDATED", "STATUS", "CHART", "APP VERSION"]]
    for svc in SERVICES:
        rows.append([svc, "shop", str(rng.randint(12, 90)), f"2025-03-{rng.randint(1, 13):02d} {rng.randint(8, 18):02d}:{rng.randint(0, 59):02d}:11 +0000 UTC",
                     "deployed", f"shop-service-{rng.choice(['3.4.1', '3.4.2', '3.5.0'])}", f"2025.03.{rng.randint(1, 13)}"])
    return pe._table(rows[0], rows[1:]) + "\n"


def top_pods_services(rng: random.Random) -> str:
    rows = [["NAME", "CPU(cores)", "MEMORY(bytes)"]]
    for svc in SERVICES:
        for i in range(rng.randint(2, 3)):
            rows.append([f"{svc}-{pe.hex_(rng, 9)}-{pe.hex_(rng, 5)}", f"{rng.randint(5, 700)}m", f"{rng.randint(90, 900)}Mi"])
    return pe._table(rows[0], rows[1:]) + "\n"


def versions_grep(rng: random.Random, lib: str) -> str:
    """``rg -n '^<lib>==' services/*/requirements.txt``: one row per service."""
    major, minor = rng.choice([("2", "0"), ("1", "4")])
    rows = []
    for svc in SERVICES:
        rows.append(f"services/{svc}/requirements.txt:{rng.randint(4, 30)}:{lib}=={major}.{minor}.{rng.choice([20, 24, 25, 29, 29, 29])}")
    return "\n".join(rows) + "\n"


# =================================================================================================
# i18n
# =================================================================================================
def _fmt_i18n(sec: int, ms: int, level: str, logger: str, msg: str) -> str:
    return f"2025-03-14 {hms(sec)} {level:<5} [{logger}] {msg}"


ZH_LOG = LogStyle(
    _fmt_i18n,
    info=(("INFO", "订单", "订单 ORD-{o} 已创建 (客户=C-{x}, 商品数={q})"), ("INFO", "支付", "支付成功: 订单 ORD-{o}, 金额 {amt} EUR"),
          ("INFO", "物流", "运单 {x} 已揽收 (承运商=顺丰, 重量={q}kg)"), ("INFO", "订单", "订单 ORD-{o} 状态变更: 已支付 -> 已发货")),
    warn=(("WARN", "仓库", "仓库 {w} 响应缓慢 ({ms}ms), 正在重试"),),
    burst_warn=(("WARN", "库存", "库存同步延迟 {ms}ms (队列长度={k})"),),
    errors=(("ERROR", "库存", "库存预留失败: 订单 ORD-{o}, SKU-{x} 库存不足 (剩余=0, 需要={q})"),
            ("ERROR", "支付", "支付网关超时: 订单 ORD-{o} 在 {ms}ms 内无响应 (第{a}次尝试)"),
            ("FATAL", "队列", "消费者 {n} 连续失败 {k} 次, 已退出")),
    warn_rate=0.10, step=3,
)
JA_LOG = LogStyle(
    _fmt_i18n,
    info=(("INFO", "注文", "注文 ORD-{o} を処理中 (顧客ID=C-{x})"), ("INFO", "決済", "決済完了: 注文 ORD-{o} 金額 {amt} EUR"),
          ("INFO", "配送", "出荷準備完了: 運送状 {x} (重量={q}kg)")),
    warn=(("WARN", "在庫", "在庫照会が遅延しています ({ms}ms) 倉庫={w}"),),
    burst_warn=(("WARN", "在庫", "在庫同期の遅延 {ms}ms (キュー長={k})"),),
    errors=(("ERROR", "決済", "決済タイムアウト: 注文 ORD-{o} ({a}回目の試行)"),
            ("ERROR", "在庫", "在庫引当に失敗しました: 注文 ORD-{o}, SKU-{x}"),
            ("CRITICAL", "配送", "配送業者APIが応答しません ({ms}ms)")),
    warn_rate=0.10, step=3,
)
KO_LOG = LogStyle(
    _fmt_i18n,
    info=(("INFO", "주문", "주문 ORD-{o} 처리 완료 (고객=C-{x})"), ("INFO", "결제", "결제 성공: 주문 ORD-{o}, 금액 {amt} EUR"),
          ("INFO", "배송", "송장 {x} 발송 준비 (무게={q}kg)")),
    warn=(("WARN", "재고", "재고 조회 지연 ({ms}ms) 창고={w}"),),
    burst_warn=(("WARN", "재고", "재고 동기화 지연 {ms}ms (큐 길이={k})"),),
    errors=(("ERROR", "재고", "재고 예약 실패: 주문 ORD-{o}, SKU-{x} 재고 부족"),
            ("ERROR", "결제", "결제 게이트웨이 시간 초과: 주문 ORD-{o} ({a}번째 시도)"),
            ("FATAL", "큐", "소비자 {n} 이(가) {k}회 연속 실패하여 종료되었습니다")),
    warn_rate=0.10, step=3,
)
AR_LOG = LogStyle(
    _fmt_i18n,
    info=(("INFO", "الطلبات", "تم إنشاء الطلب ORD-{o} (العميل=C-{x}، عدد المنتجات={q})"), ("INFO", "الدفع", "نجح الدفع: الطلب ORD-{o}، المبلغ {amt} EUR"),
          ("INFO", "الشحن", "تم تجهيز الشحنة {x} (الوزن={q}كجم)")),
    warn=(("WARN", "المخزون", "استجابة المستودع {w} بطيئة ({ms}ms)، جارٍ إعادة المحاولة"),),
    burst_warn=(("WARN", "المخزون", "تأخر مزامنة المخزون {ms}ms (طول الطابور={k})"),),
    errors=(("ERROR", "المخزون", "فشل حجز المخزون: الطلب ORD-{o}، SKU-{x} الكمية غير كافية"),
            ("ERROR", "الدفع", "انتهت مهلة بوابة الدفع: الطلب ORD-{o} (المحاولة {a})"),
            ("CRITICAL", "الشحن", "واجهة شركة الشحن لا تستجيب ({ms}ms)")),
    warn_rate=0.10, step=3,
)
RU_LOG = LogStyle(
    _fmt_i18n,
    info=(("INFO", "заказы", "Заказ ORD-{o} создан (клиент=C-{x}, позиций={q})"), ("INFO", "оплата", "Оплата прошла: заказ ORD-{o}, сумма {amt} EUR"),
          ("INFO", "доставка", "Отправление {x} готово к выдаче (вес={q}кг)")),
    warn=(("WARN", "склад", "Склад {w} отвечает медленно ({ms}ms), повторная попытка"),),
    burst_warn=(("WARN", "склад", "Задержка синхронизации остатков {ms}ms (очередь={k})"),),
    errors=(("ERROR", "склад", "Не удалось зарезервировать товар: заказ ORD-{o}, SKU-{x}, остаток недостаточен"),
            ("ERROR", "оплата", "Таймаут платёжного шлюза: заказ ORD-{o} (попытка {a})"),
            ("FATAL", "очередь", "Потребитель {n} завершён после {k} ошибок подряд")),
    warn_rate=0.10, step=3,
)

_I18N_WAREHOUSES = {"zh": ["法兰克福", "大阪", "上海"], "ja": ["大阪", "名古屋", "福岡"], "ko": ["서울", "부산", "인천"],
                    "ar": ["دبي", "الرياض", "القاهرة"], "ru": ["Москва", "Казань", "Самара"]}


def i18n_log(rng: random.Random, lang: str, *, n_lines: int, start_sec: int, n_errors: int = 12, plants: dict[int, pe.Template] | None = None,
             burst: tuple[float, float] = (0.3, 0.85)) -> Log:
    """A service log in ``lang`` (zh / ja / ko / ar / ru); level words stay ASCII (as in real tooling), messages are localized."""
    style = {"zh": ZH_LOG, "ja": JA_LOG, "ko": KO_LOG, "ar": AR_LOG, "ru": RU_LOG}[lang]
    log = pe.make_log(rng, style, n_lines=n_lines, start_sec=start_sec, burst=burst, n_errors=n_errors, plants=plants)
    names = _I18N_WAREHOUSES[lang]
    log.lines[:] = [line.replace("{w}", rng.choice(names)) if "{w}" in line else line for line in log.lines]
    return log


def i18n_messages_json(rng: random.Random, *, n: int, plants: dict[int, str] | None = None) -> str:
    """A customer-messages API dump: ``n`` tickets in five languages, ``ensure_ascii=False`` like the real client."""
    bodies = {
        "zh": ["我的订单 ORD-{o} 还没有发货, 请尽快处理。", "收到的商品与描述不符, 申请退款。", "请把收货地址改成上海市浦东新区世纪大道 {q} 号。"],
        "ja": ["注文 ORD-{o} がまだ届いていません。確認をお願いします。", "商品が破損していました。返金をお願いします。", "配送先を変更したいです (大阪府大阪市北区 {q}-{q}-{q})。"],
        "ko": ["주문 ORD-{o} 이(가) 아직 도착하지 않았습니다.", "상품이 설명과 다릅니다. 환불을 요청합니다.", "배송지를 서울특별시 강남구 {q}번지로 변경해 주세요."],
        "ar": ["لم يصل طلبي ORD-{o} بعد، الرجاء المتابعة.", "المنتج لا يطابق الوصف وأريد استرجاع المبلغ.", "أرجو تغيير عنوان التسليم إلى الرياض، حي العليا {q}."],
        "ru": ["Мой заказ ORD-{o} до сих пор не доставлен, проверьте, пожалуйста.", "Товар не соответствует описанию, прошу вернуть деньги.", "Измените адрес доставки: Москва, ул. Тверская, д. {q}."],
    }
    langs = list(bodies)
    items = []
    for i in range(n):
        lang = langs[i % len(langs)]
        text = rng.choice(bodies[lang]).format(o=rng.randint(10000, 99999), q=rng.randint(1, 99))
        items.append({"id": f"T-{rng.randint(100000, 999999)}", "lang": lang, "customer": f"C-{rng.randint(1000, 9999)}",
                      "status": rng.choice(["open", "open", "pending", "solved"]), "priority": rng.choice(["low", "normal", "high"]),
                      "created_at": f"2025-03-14T{hms(2 * 3600 + 20 * 60 + i * 11)}Z", "message": text})
    for idx, text in (plants or {}).items():
        items[idx]["message"] = text
    return json.dumps({"tickets": items, "count": n}, ensure_ascii=False, indent=1)


def i18n_glossary_yaml(rng: random.Random) -> str:
    """``config/i18n/status_labels.yaml``: status labels in six languages."""
    labels = {"new": {"en": "New", "zh": "新建", "ja": "新規", "ko": "신규", "ar": "جديد", "ru": "Новый"},
              "reserved": {"en": "Reserved", "zh": "已预留", "ja": "引当済み", "ko": "예약됨", "ar": "محجوز", "ru": "Зарезервирован"},
              "paid": {"en": "Paid", "zh": "已支付", "ja": "支払済み", "ko": "결제 완료", "ar": "مدفوع", "ru": "Оплачен"},
              "fulfilled": {"en": "Shipped", "zh": "已发货", "ja": "発送済み", "ko": "배송됨", "ar": "تم الشحن", "ru": "Отправлен"},
              "cancelled": {"en": "Cancelled", "zh": "已取消", "ja": "キャンセル", "ko": "취소됨", "ar": "ملغي", "ru": "Отменён"}}
    out = ["# config/i18n/status_labels.yaml", "# generated from the translation sheet by tools/i18n/export_labels.py", "version: 7"]
    for key, langs in labels.items():
        out.append(f"{key}:")
        out += [f"  {lang}: \"{text}\"" for lang, text in langs.items()]
    return "\n".join(out) + "\n"
