"""Hermes-shaped tool payloads, generated deterministically.

Envelope shapes (verified against the pinned Hermes checkout, ``tools/file_operations_common.py``,
``tools/terminal_tool.py`` and ``tools/tool_result_storage.py``):

* ``read_file``    -> ``ReadResult.to_dict()``: ``content`` carries a ``<n>|`` gutter, plus ``total_lines``,
  ``file_size``, ``truncated``, ``is_binary``, ``is_image``, ``not_found``.
* ``terminal``     -> ``{"output": ..., "exit_code": N, "error": null}``.
* ``patch``        -> ``PatchResult.to_dict()``: ``success``, ``diff``, ``files_modified``.
* ``search_files`` -> ``SearchResult.to_dict(densify=True)``: ``total_count`` + path-grouped ``matches_text``.
* oversized output -> the ``<persisted-output>`` block built by ``_build_persisted_message``.
* ``skill_view``   -> ``{"success": true, "name": ..., "content": ..., ...}``.

Bodies (logs, pytest output, diffs, ...) are synthetic but follow the real tools' formats.
Every generator takes a seeded ``random.Random``; the same seed gives byte-identical output.
``plant`` arguments place a caller-chosen line at a fractional position so the replay scenario can
hide gold facts deep inside an otherwise random payload.
"""
from __future__ import annotations

import json
import random
from collections.abc import Sequence

# --------------------------------------------------------------------------------------------
# Envelopes
# --------------------------------------------------------------------------------------------


def _numbered(text: str, start: int = 1) -> str:
    """The ``<n>|`` gutter ``read_file`` puts in front of every line."""
    text = text.removesuffix("\n")
    return "\n".join(f"{i}|{line}" for i, line in enumerate(text.split("\n"), start=start))


def read_file_result(text: str, *, offset: int = 1) -> str:
    """``read_file`` tool result for ``text`` (JSON string, Hermes key order)."""
    numbered = _numbered(text, offset)
    return json.dumps(
        {
            "content": numbered,
            "total_lines": numbered.count("\n") + 1,
            "file_size": len(text.encode("utf-8")),
            "truncated": False,
            "is_binary": False,
            "is_image": False,
            "not_found": False,
        },
        ensure_ascii=False,
    )


def terminal_result(output: str, *, exit_code: int = 0, error: str | None = None) -> str:
    """``terminal`` tool result envelope."""
    return json.dumps({"output": output, "exit_code": exit_code, "error": error}, ensure_ascii=False)


def patch_result(diff: str, files_modified: Sequence[str]) -> str:
    """``patch`` tool result (``PatchResult.to_dict()`` for a successful single-file edit)."""
    return json.dumps(
        {"success": True, "diff": diff, "files_modified": list(files_modified)}, ensure_ascii=False
    )


def search_files_result(matches: Sequence[tuple[str, int, str]]) -> str:
    """``search_files`` result; with >= 5 matches Hermes densifies to a path-grouped text block."""
    body: dict = {"total_count": len(matches)}
    if len(matches) >= 5:
        lines: list[str] = []
        current = None
        for path, line_no, content in matches:
            if path != current:
                lines.append(path)
                current = path
            lines.append(f"  {line_no}: {content.rstrip()}")
        body["matches_format"] = (
            "path-grouped: each file path on its own line, followed by "
            "indented '<line>: <content>' rows for matches in that file"
        )
        body["matches_text"] = "\n".join(lines)
    else:
        body["matches"] = [{"path": p, "line": n, "content": c} for p, n, c in matches]
    return json.dumps(body, ensure_ascii=False)


def persisted_output(preview: str, original_size: int, file_path: str, *, has_more: bool = True) -> str:
    """The ``<persisted-output>`` block Hermes substitutes for an oversized result.

    Mirrors ``tools.tool_result_storage._build_persisted_message`` text-for-text (the real-Hermes
    replay test asserts the two agree).
    """
    size_kb = original_size / 1024
    size_str = f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb:.1f} KB"
    return (
        "<persisted-output>\n"
        f"This tool result was too large ({original_size:,} characters, {size_str}).\n"
        f"Full output saved to: {file_path}\n"
        "Use the read_file tool with offset and limit to access specific sections of this output.\n"
        "Recovery: page through the saved file with read_file (offset/limit) or "
        "process it with execute_code — do NOT re-request the same data from the "
        "remote API; the full result is already on disk.\n\n"
        f"Preview (first {len(preview)} chars):\n"
        + preview
        + ("\n..." if has_more else "")
        + "\n</persisted-output>"
    )


def skill_view_result(name: str, description: str, body: str, *, tags: Sequence[str] = ()) -> str:
    """``skill_view`` result for a skill whose SKILL.md body is ``body``."""
    return json.dumps(
        {
            "success": True,
            "name": name,
            "description": description,
            "tags": list(tags),
            "related_skills": [],
            "content": body,
            "path": f"{name}/SKILL.md",
            "skill_dir": f"/home/agent/.hermes/skills/{name}",
            "linked_files": None,
            "usage_hint": None,
            "_source_path": f"/home/agent/.hermes/skills/{name}/SKILL.md",
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------


def _hex(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def _uuid(rng: random.Random) -> str:
    h = _hex(rng, 32)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _plant(lines: list[str], plant: dict[float, str] | None) -> list[str]:
    """Insert each ``plant`` line at its fractional position (ascending, so offsets stay valid)."""
    out = list(lines)
    for frac in sorted(plant or {}):
        out.insert(max(0, min(len(out), int(len(out) * frac))), (plant or {})[frac])
    return out


# --------------------------------------------------------------------------------------------
# Python source (read_file / cat -n bodies)
# --------------------------------------------------------------------------------------------

_NOUNS = ["invoice", "address", "coupon", "shipment", "refund", "catalog", "customer", "warehouse", "tax", "carrier"]
_TYPES = ["Order", "Invoice", "Shipment", "Customer", "Coupon", "Refund"]

_FUNC_TEMPLATES = [
    '''def filter_{noun}s(items: Iterable[{typ}], *, limit: int = {n}) -> list[{typ}]:
    """Return at most ``limit`` items that pass the {noun} check."""
    out: list[{typ}] = []
    for item in items:
        if not _passes_{noun}_check(item):
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out
''',
    '''def {noun}_total(lines: Sequence[Line], *, currency: str = "EUR", rounding: int = {n}) -> Decimal:
    """Sum line totals for a {noun} and round to ``rounding`` places."""
    total = Decimal("0")
    for line in lines:
        total += Decimal(line.unit_price) * line.qty
    return total.quantize(Decimal(1).scaleb(-2), rounding=ROUND_HALF_UP)
''',
    '''def parse_{noun}_id(raw: str) -> str:
    """Normalise a {noun} identifier coming from the API ({n} chars max)."""
    raw = raw.strip().upper()
    if not raw.startswith("{pfx}-"):
        raise ValueError(f"not a {noun} id: {{raw!r}}")
    if len(raw) > {n}:
        raise ValueError(f"{noun} id too long: {{raw!r}}")
    return raw
''',
    '''def {noun}_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Public view of a {noun} record (drops internal fields)."""
    hidden = {{"_etag", "_shard", "internal_notes"}}
    return {{key: value for key, value in record.items() if key not in hidden}}
''',
    '''def retry_{noun}_call(fn: Callable[[], T], *, attempts: int = {n}, delay: float = 0.2) -> T:
    """Call ``fn`` until it succeeds or ``attempts`` run out; sleeps ``delay`` * 2**i between tries."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            time.sleep(delay * (2 ** i))
    assert last is not None
    raise last
''',
    '''def group_{noun}s_by_{field}(items: Iterable[{typ}]) -> dict[str, list[{typ}]]:
    """Bucket {noun}s by their ``{field}`` attribute, keeping input order inside each bucket."""
    buckets: dict[str, list[{typ}]] = defaultdict(list)
    for item in items:
        buckets[getattr(item, "{field}")].append(item)
    return dict(buckets)
''',
    '''@dataclass
class {Cls}Config:
    """Settings for the {noun} pipeline."""

    batch_size: int = {n}
    timeout_s: float = {n}.5
    strict: bool = False
    tags: tuple[str, ...] = ("{noun}", "{field}")
''',
    '''def validate_{noun}(payload: dict[str, Any]) -> list[str]:
    """Return a list of human readable problems with a {noun} payload (empty = valid)."""
    problems: list[str] = []
    for key in ("id", "{field}", "created_at"):
        if key not in payload:
            problems.append(f"missing {{key}}")
    if payload.get("qty", 0) < 0:
        problems.append("qty must be >= 0")
    return problems
''',
    '''def {noun}_cache_key(parts: Sequence[str], *, version: int = {n}) -> str:
    """Stable cache key: sha1 of the joined parts, prefixed with the schema ``version``."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return f"v{{version}}:{noun}:{{digest[:16]}}"
''',
    '''def paginate_{noun}s(client: ApiClient, *, page_size: int = {n}) -> Iterator[{typ}]:
    """Yield every {noun} from the API, following ``next_cursor`` until exhausted."""
    cursor: str | None = None
    while True:
        page = client.get("/{noun}s", params={{"limit": page_size, "cursor": cursor}})
        yield from page["items"]
        cursor = page.get("next_cursor")
        if not cursor:
            return
''',
]


def python_functions(rng: random.Random, n: int) -> str:
    """``n`` plausible helper functions / dataclasses, separated by blank lines."""
    parts = []
    for _ in range(n):
        tpl = rng.choice(_FUNC_TEMPLATES)
        noun = rng.choice(_NOUNS)
        parts.append(
            tpl.format(
                noun=noun,
                Cls=noun.capitalize(),
                typ=rng.choice(_TYPES),
                field=rng.choice(["status", "region", "carrier", "owner", "tier"]),
                n=rng.choice([8, 16, 24, 32, 48, 64, 100]),
                pfx=noun[:3].upper(),
            )
        )
    return "\n\n".join(parts)


ORDERS_SERVICE_HEADER = '''"""Order service: reservation, payment capture and fulfilment orchestration."""
from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar

from shop.inventory import InventoryClient, StockReservationConflict
from shop.ledger import LedgerWriteError, LedgerWriter
from shop.payments import PaymentGateway, PaymentGatewayTimeout

log = logging.getLogger("orders.service")
T = TypeVar("T")

RESERVATION_TTL_SECONDS = 420
MAX_LINE_ITEMS = 64
CAPTURE_TIMEOUT_S = 12.5
'''

RESERVE_STOCK_BUGGY = '''def reserve_stock(order, inventory, ledger):
    """Reserve stock for every line item and record the hold in the ledger."""
    holds = []
    for line in order.lines:
        hold = inventory.reserve(line.sku, line.qty, ttl=RESERVATION_TTL_SECONDS)
        holds.append(hold)
    ledger.write_entry(order.id, "reserve", [h.id for h in holds])
    return holds
'''

RESERVE_STOCK_FIXED = '''def release_holds(inventory, holds):
    """Release every hold in ``holds``; used when a reservation cannot be completed."""
    for hold in holds:
        inventory.release(hold.id)


def reserve_stock(order, inventory, ledger):
    """Reserve stock for every line item and record the hold in the ledger.

    If any step fails, every hold taken so far is released before re-raising.
    """
    holds = []
    try:
        for line in order.lines:
            hold = inventory.reserve(line.sku, line.qty, ttl=RESERVATION_TTL_SECONDS)
            holds.append(hold)
        ledger.write_entry(order.id, "reserve", [h.id for h in holds])
    except (StockReservationConflict, LedgerWriteError):
        release_holds(inventory, holds)
        raise
    return holds
'''

ORDERS_SERVICE_TAIL = '''def capture_payment(order, gateway, *, attempts: int = 3):
    """Capture the payment for ``order``; retries on gateway timeouts."""
    for attempt in range(1, attempts + 1):
        try:
            return gateway.capture(order.id, order.total, timeout=CAPTURE_TIMEOUT_S)
        except PaymentGatewayTimeout:
            log.warning("capture timeout order=%s attempt=%s", order.id, attempt)
            if attempt == attempts:
                raise
            time.sleep(0.25 * attempt)


def place_order(order, inventory, ledger, gateway):
    """Reserve stock, capture payment, then confirm. The public entry point of the checkout flow."""
    holds = reserve_stock(order, inventory, ledger)
    receipt = capture_payment(order, gateway)
    ledger.write_entry(order.id, "capture", [receipt.id])
    log.info("order placed id=%s holds=%d", order.id, len(holds))
    return receipt
'''


def orders_service_source(rng: random.Random, *, patched: bool) -> str:
    """``src/orders/service.py`` before (``patched=False``) or after the stock-rollback fix."""
    filler = python_functions(rng, 20)
    reserve = RESERVE_STOCK_FIXED if patched else RESERVE_STOCK_BUGGY
    return f"{ORDERS_SERVICE_HEADER}\n\n{filler}\n\n{reserve}\n\n{ORDERS_SERVICE_TAIL}\n"


def generic_module_source(rng: random.Random, doc: str, n: int = 22) -> str:
    """A plausible helper module of ``n`` functions (used for extra ``read_file`` traffic)."""
    header = (
        f'"""{doc}"""\nfrom __future__ import annotations\n\nimport hashlib\nimport time\n'
        "from collections import defaultdict\nfrom dataclasses import dataclass\nfrom decimal import ROUND_HALF_UP, Decimal\n"
        "from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar\n\nT = TypeVar(\"T\")\n\n\n"
    )
    return header + python_functions(rng, n) + "\n"


def orders_models_source(rng: random.Random) -> str:
    header = '''"""Order domain models."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class OrderStatus(str, Enum):
    NEW = "new"
    RESERVED = "reserved"
    PAID = "paid"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


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

    @property
    def total(self) -> Decimal:
        return sum((Decimal(l.unit_price) * l.qty for l in self.lines), Decimal("0"))
'''
    return header + "\n\n" + python_functions(rng, 16) + "\n"


def orders_tests_source(rng: random.Random, *, with_rollback_test: bool = True) -> str:
    header = '''"""Tests for the order service."""
import pytest

from shop.ledger import LedgerWriteError
from shop.orders.service import place_order, reserve_stock
from tests.fakes import FakeInventory, FakeLedger, FakeGateway, make_order


@pytest.fixture
def fake_inventory():
    return FakeInventory(stock={"SKU-48213": 5, "SKU-77120": 2})


@pytest.fixture
def fake_ledger():
    return FakeLedger()


def test_reserve_stock_happy_path(fake_inventory, fake_ledger):
    order = make_order(lines=[("SKU-48213", 2)])
    holds = reserve_stock(order, fake_inventory, fake_ledger)
    assert len(holds) == 1
    assert fake_ledger.entries[-1][1] == "reserve"
'''
    rollback = '''

def test_reserve_stock_rollback(fake_inventory, fake_ledger):
    order = make_order(lines=[("SKU-48213", 2), ("SKU-77120", 1)])
    fake_ledger.fail_next_write = True
    with pytest.raises(LedgerWriteError):
        reserve_stock(order, fake_inventory, fake_ledger)
    assert fake_inventory.active_holds() == []
'''
    extra = []
    for i in range(16):
        noun = rng.choice(_NOUNS)
        extra.append(
            f'''def test_{noun}_{i}_roundtrip(fake_inventory, fake_ledger):
    order = make_order(lines=[("SKU-48213", {rng.choice([1, 2, 3])})])
    receipt = place_order(order, fake_inventory, fake_ledger, FakeGateway())
    assert receipt.amount == order.total
    assert fake_ledger.entries[-1][1] == "capture"
'''
        )
    return header + (rollback if with_rollback_test else "") + "\n\n" + "\n\n".join(extra)


def settings_yaml(rng: random.Random, *, plant: dict[float, str] | None = None) -> str:
    """A ~6 KB service config; ``plant`` lines land at fractional positions."""
    lines = [
        "# shop service configuration (production profile)",
        "service:",
        "  name: shop-orders",
        "  environment: production",
        "  region: eu-central-1",
        "http:",
        "  bind: 0.0.0.0:8443",
        "  read_timeout_s: 15",
        "  write_timeout_s: 30",
    ]
    for group in ["inventory", "payments", "ledger", "search", "notifications", "fraud", "shipping", "tax"]:
        lines.append(f"{group}:")
        lines.append(f"  endpoint: https://{group}-svc.internal:{rng.choice([8080, 8081, 9090, 9443])}")
        lines.append(f"  timeout_ms: {rng.choice([500, 800, 1200, 2500, 4000])}")
        lines.append(f"  pool_size: {rng.choice([8, 16, 32, 64])}")
        lines.append("  retry:")
        lines.append(f"    backoff_base_ms: {rng.choice([50, 100, 200])}")
        lines.append(f"    backoff_cap_ms: {rng.choice([2000, 5000, 10000])}")
        lines.append("  circuit_breaker:")
        lines.append(f"    failure_threshold: {rng.choice([5, 10, 20])}")
        lines.append(f"    reset_after_s: {rng.choice([15, 30, 60])}")
        lines.append("  tags: [" + ", ".join(rng.sample(["core", "edge", "batch", "pci", "eu", "us"], 3)) + "]")
        lines.append("")
    body = _plant(lines, plant)
    return "\n".join(body) + "\n"


def cat_n(text: str) -> str:
    """``cat -n`` output: right-aligned 6-wide number, a tab, then the line."""
    text = text.removesuffix("\n")
    return "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(text.split("\n"), start=1)) + "\n"


# --------------------------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------------------------

#: (level, logger, message template). Digits/hex/uuids differ between occurrences, so every
#: template is exactly one error fingerprint after masking.
ERROR_TEMPLATES: list[tuple[str, str, str]] = [
    ("ERROR", "payments.gateway", "PaymentGatewayTimeout: upstream stripe-proxy timed out after {ms}ms (order_id=ORD-{o}, attempt={a})"),
    ("ERROR", "inventory.reserve", "StockReservationConflict: sku=SKU-{s} reserved_by=tx-{h} expected_version={v} actual_version={w}"),
    ("ERROR", "ledger.writer", 'LedgerWriteError: duplicate key value violates unique constraint "ledger_entries_pkey" (entry_id={u})'),
    ("FATAL", "worker.pool", "WorkerCrash: worker-{n} exited with signal 9 (out of memory)"),
    ("ERROR", "http.server", "UpstreamBadGateway: 502 from inventory-svc POST /v2/reserve trace_id={h}"),
    ("ERROR", "cache.redis", "ConnectionResetError: [Errno 104] Connection reset by peer (host=redis-{n}.internal:6379)"),
    ("CRITICAL", "scheduler.cron", "JobMissedDeadline: nightly-reconcile exceeded {n}s budget"),
]

_PATHS = [
    "/api/v2/orders", "/api/v2/orders/{id}", "/api/v2/cart", "/api/v2/cart/items", "/api/v2/inventory/{sku}",
    "/api/v2/payments/capture", "/healthz", "/api/v2/users/me", "/api/v2/search", "/api/v2/shipments/{id}",
]
_INFO_MSGS = [
    "request completed method={m} path={p} status={st} dur_ms={ms} req_id={h}",
    "cache hit key=v3:order:{h} ttl={n}s",
    "reservation created sku=SKU-{s} qty={q} hold=h-{h}",
    "ledger entry written order=ORD-{o} kind={k} entry_id={u}",
    "payment captured order=ORD-{o} amount={amt} currency=EUR",
    "worker-{n} picked job id={h} queue={qn}",
    "session refreshed user=U-{o} ip=10.{b}.{c}.{d}",
]
_WARN_MSGS = [
    "slow redis command GET latency={ms}ms host=redis-{n}.internal",
    "request exceeded 800ms method={m} path={p} dur_ms={ms} req_id={h}",
    "retrying inventory reserve sku=SKU-{s} attempt={a}",
]


def _fill(tpl: str, rng: random.Random) -> str:
    path = rng.choice(_PATHS).format(id=rng.randint(10000, 99999), sku=f"SKU-{rng.randint(10000, 99999)}")
    return tpl.format(
        m=rng.choice(["GET", "GET", "POST", "PUT"]),
        p=path,
        st=rng.choice([200, 200, 200, 201, 204, 304, 404]),
        ms=rng.randint(3, 2400),
        h=_hex(rng, 8),
        u=_uuid(rng),
        n=rng.randint(1, 12),
        s=rng.randint(10000, 99999),
        q=rng.randint(1, 5),
        o=rng.randint(10000, 99999),
        k=rng.choice(["reserve", "capture", "release"]),
        amt=f"{rng.randint(5, 900)}.{rng.randint(0, 99):02d}",
        qn=rng.choice(["fulfil", "email", "reconcile"]),
        b=rng.randint(0, 255), c=rng.randint(0, 255), d=rng.randint(1, 254),
        a=rng.randint(1, 4),
        v=rng.randint(1, 40), w=rng.randint(41, 90),
    )


def server_log(
    rng: random.Random,
    *,
    n_lines: int = 450,
    start_hour: int = 2,
    plant: dict[float, str] | None = None,
    error_extra: int = 14,
    with_errors: bool = True,
) -> tuple[str, list[str]]:
    """An application log slice with a burst of level-tagged errors in its middle.

    Returns ``(text, error_lines)``. Every entry of ``ERROR_TEMPLATES`` appears at least once, plus
    ``error_extra`` repeats of random templates (same fingerprint, different digits/ids). ``plant``
    maps a fractional position to a literal line (used to hide gold facts). ``with_errors=False``
    yields an INFO/WARN-only window (the "burst is over" slice).
    """
    # Timestamps: 2025-03-14T02:xx:xx.mmmZ, advancing 0-3 s per line.
    sec = start_hour * 3600 + 10 * 60
    lines: list[str] = []
    for _ in range(n_lines):
        sec += rng.randint(0, 3)
        ts = f"2025-03-14T{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}.{rng.randint(0, 999):03d}Z"
        if rng.random() < 0.07:
            msg = _fill(rng.choice(_WARN_MSGS), rng)
            lines.append(f"{ts} WARN  {rng.choice(['cache.redis', 'http.server', 'inventory.reserve'])} {msg}")
        else:
            lines.append(f"{ts} INFO  {rng.choice(['http.server', 'orders.service', 'inventory.reserve', 'ledger.writer', 'worker.pool'])} {_fill(rng.choice(_INFO_MSGS), rng)}")
    # Error burst between 30% and 80% of the slice.
    chosen = (
        list(range(len(ERROR_TEMPLATES))) + [rng.randrange(len(ERROR_TEMPLATES)) for _ in range(error_extra)]
        if with_errors
        else []
    )
    positions = sorted(rng.sample(range(int(n_lines * 0.30), int(n_lines * 0.80)), len(chosen)))
    error_lines: list[str] = []
    rng.shuffle(chosen)
    for pos, tpl_idx in zip(positions, chosen):
        level, logger_name, tpl = ERROR_TEMPLATES[tpl_idx]
        ts = lines[pos].split(" ", 1)[0]
        line = f"{ts} {level:<5} {logger_name} {_fill(tpl, rng)}"
        lines[pos] = line
        error_lines.append(line)
    return "\n".join(_plant(lines, plant)) + "\n", error_lines


def cjk_log(rng: random.Random, *, n_lines: int = 160, plant: dict[float, str] | None = None) -> str:
    """Support-tooling log with Chinese, Japanese and Korean messages (CJK level tags)."""
    msgs = [
        ("[信息]", "订单 ORD-{o} 已创建 (客户={u}, 商品数={q})"),
        ("[信息]", "支付成功: 订单 ORD-{o}, 金额 {amt} EUR"),
        ("[警告]", "仓库 {w} 响应缓慢 ({ms}ms), 正在重试"),
        ("[错误]", "库存预留失败: 订单 ORD-{o}, SKU-{s} 库存不足 (剩余=0, 需要={q})"),
        ("[情報]", "注文 ORD-{o} を処理中 (顧客ID=C-{u})"),
        ("[警告]", "在庫照会が遅延しています ({ms}ms) 倉庫={w}"),
        ("[エラー]", "決済タイムアウト: 注文 ORD-{o} (試行={q})"),
        ("[정보]", "주문 ORD-{o} 처리 완료 (고객={u})"),
        ("[오류]", "재고 예약 실패: 주문 ORD-{o}, SKU-{s} 재고 부족"),
    ]
    sec = 2 * 3600 + 15 * 60
    out = []
    for _ in range(n_lines):
        sec += rng.randint(1, 4)
        level, tpl = rng.choice(msgs)
        text = tpl.format(
            o=rng.randint(10000, 99999), u=rng.randint(1000, 9999), q=rng.randint(1, 5),
            amt=f"{rng.randint(5, 900)}.{rng.randint(0, 99):02d}", w=rng.choice(["法兰克福", "大阪", "서울"]),
            ms=rng.randint(200, 3000), s=rng.randint(10000, 99999),
        )
        out.append(f"2025-03-14 {sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d} {level} {text}")
    return "\n".join(_plant(out, plant)) + "\n"


def journal_preview(rng: random.Random, chars: int = 1500) -> str:
    """The inline preview Hermes keeps of an oversized ``terminal`` result (``generate_preview``).

    The persisted text is the whole JSON envelope (``{"output": "...", ...}``), whose escaped newlines
    leave no real line break to cut at, so the preview is its first ``chars`` characters.
    """
    lines = []
    sec = 2 * 3600 + 17 * 60
    while sum(len(x) + 1 for x in lines) < chars:
        sec += rng.randint(0, 2)
        lines.append(
            f"Mar 14 {sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d} shop-orders-7c9d systemd[1]: "
            f"{rng.choice(['Started', 'Reloaded', 'Stopped'])} shop-worker@{rng.randint(1, 12)}.service - Shop worker {rng.randint(1, 12)}."
        )
    envelope = terminal_result("\n".join(lines))
    last_nl = envelope.rfind("\n", 0, chars)
    return envelope[: last_nl + 1 if last_nl > chars // 2 else chars]


# --------------------------------------------------------------------------------------------
# pytest / git / grep output
# --------------------------------------------------------------------------------------------


def pytest_output(
    rng: random.Random, *, failed: Sequence[str], n_passed: int, extra_log_lines: int = 60, plant_summary: str | None = None
) -> str:
    """pytest -x -q style output: dots, FAILURES with tracebacks and captured log, short summary."""
    total = n_passed + len(failed)
    files = ["tests/test_orders.py", "tests/test_inventory.py", "tests/test_ledger.py", "tests/test_payments.py", "tests/test_api.py"]
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
    for f in files:
        k = max(1, n_passed // len(files))
        marks = "".join("." for _ in range(k))
        done += k
        out.append(f"{f} {marks}{'F' * len(failed) if f == files[0] else ''}  [{min(100, done * 100 // total):3d}%]")
    out += ["", "=================================== FAILURES ==================================="]
    for name in failed:
        out += [
            f"__________________________ {name} __________________________",
            "",
            f"    def {name}(fake_inventory, fake_ledger):",
            '        order = make_order(lines=[("SKU-48213", 2), ("SKU-77120", 1)])',
            "        fake_ledger.fail_next_write = True",
            ">       with pytest.raises(LedgerWriteError):",
            "            reserve_stock(order, fake_inventory, fake_ledger)",
            "        assert fake_inventory.active_holds() == []",
            "E       AssertionError: assert [Hold(id='h-1', sku='SKU-48213', qty=2), Hold(id='h-2', sku='SKU-77120', qty=1)] == []",
            "E         Left contains 2 more items, first extra item: Hold(id='h-1', sku='SKU-48213', qty=2)",
            "",
            "tests/test_orders.py:41: AssertionError",
            "------------------------------ Captured log call -------------------------------",
        ]
        for _ in range(extra_log_lines):
            out.append(
                f"INFO     orders.service:service.py:{rng.randint(20, 140)} reserve sku=SKU-{rng.randint(10000, 99999)} "
                f"qty={rng.randint(1, 5)} hold=h-{_hex(rng, 6)} ttl={rng.choice([60, 120, 420])}"
            )
        out.append("")
    out.append("=========================== short test summary info ============================")
    for name in failed:
        out.append(f"FAILED tests/test_orders.py::{name} - AssertionError: assert [Hold(id='h-1'...")
    out.append(plant_summary or f"======================== {len(failed)} failed, {n_passed} passed in 8.42s ========================")
    return "\n".join(out) + "\n"


def pytest_verbose_output(rng: random.Random, n: int) -> str:
    """``pytest -v`` output: one PASSED line per test, then the summary line."""
    mods = ["test_orders", "test_inventory", "test_ledger", "test_payments", "test_api", "test_search"]
    out = ["============================= test session starts ==============================",
           "platform linux -- Python 3.12.3, pytest-8.2.0, pluggy-1.5.0 -- /usr/bin/python3",
           "rootdir: /srv/shop", f"collecting ... collected {n} items", ""]
    for i in range(n):
        mod = mods[i * len(mods) // n]
        out.append(f"tests/{mod}.py::test_{rng.choice(_NOUNS)}_{rng.choice(['roundtrip', 'validates', 'handles_empty', 'retries', 'rejects_negative'])}_{i} PASSED [{(i + 1) * 100 // n:3d}%]")
    out += ["", f"============================= {n} passed in 9.31s =============================="]
    return "\n".join(out) + "\n"


def git_log_stat(rng: random.Random, n: int = 30) -> str:
    """``git log --stat -n`` output."""
    out = []
    for i in range(n):
        out += [
            f"commit {_hex(rng, 40)}",
            f"Author: {rng.choice(['Mika Tanaka', 'Sam Okoro', 'Lena Vogel'])} <dev@shop.example>",
            f"Date:   Thu Mar {rng.randint(1, 13):02d} {rng.randint(8, 19):02d}:{rng.randint(0, 59):02d}:00 2025 +0000",
            "",
            f"    {rng.choice(['orders', 'inventory', 'ledger', 'payments'])}: {rng.choice(['tighten validation', 'log reservation ids', 'batch writes', 'bump client timeout', 'add jitter to backoff'])}",
            "",
        ]
        for _f in range(rng.randint(1, 4)):
            out.append(f" src/{rng.choice(['orders', 'inventory', 'ledger', 'payments'])}/{rng.choice(['service', 'api', 'client'])}.py | {rng.randint(2, 40)} {'+' * rng.randint(1, 12)}{'-' * rng.randint(0, 6)}")
        out += [f" {rng.randint(1, 4)} files changed, {rng.randint(3, 60)} insertions(+), {rng.randint(0, 20)} deletions(-)", ""]
    return "\n".join(out)


def pytest_green_output(n_passed: int) -> str:
    return (
        "============================= test session starts ==============================\n"
        "platform linux -- Python 3.12.3, pytest-8.2.0, pluggy-1.5.0\n"
        "rootdir: /srv/shop\n"
        f"collected {n_passed} items\n\n"
        "tests/test_orders.py ................                                    [ 11%]\n"
        "tests/test_inventory.py ..............................................   [ 44%]\n"
        "tests/test_ledger.py ...........................................         [ 74%]\n"
        "tests/test_api.py ..................................................      [100%]\n\n"
        f"============================= {n_passed} passed in 9.07s ==============================\n"
    )


def git_diff(rng: random.Random, *, service_old: str, service_new: str, n_extra_files: int = 4) -> str:
    """``git diff`` for the stock-rollback change: the real service hunk plus unrelated file hunks."""
    out = []

    def hunk_header(path: str, a: str, b: str) -> list[str]:
        return [
            f"diff --git a/{path} b/{path}",
            f"index {a}..{b} 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
        ]

    old_lines = service_old.splitlines()
    new_lines = service_new.splitlines()
    out += hunk_header("src/orders/service.py", "3f9a1c2", "8be41d7")
    out.append(f"@@ -{1},{len(old_lines)} +{1},{len(new_lines)} @@ def reserve_stock(order, inventory, ledger):")
    out += [f"-{l}" for l in old_lines]
    out += [f"+{l}" for l in new_lines]
    extra_paths = ["src/orders/api.py", "src/inventory/client.py", "src/ledger/writer.py", "docs/runbook.md", "src/shared/retry.py", "config/settings.yaml"]
    for path in rng.sample(extra_paths, min(n_extra_files, len(extra_paths))):
        out += hunk_header(path, _hex(rng, 7), _hex(rng, 7))
        for _h in range(rng.randint(3, 6)):
            start = rng.randint(10, 400)
            out.append(f"@@ -{start},9 +{start},11 @@ {rng.choice(['def handle(self, request):', 'class Client:', 'def flush(self):'])}")
            for _c in range(3):
                out.append(f"     {rng.choice(['    ', '        '])}{rng.choice(['return', 'self.', 'if not', 'for item in'])} {_hex(rng, 6)}")
            out.append(f"-        timeout = {rng.randint(1, 9)}")
            out.append(f"+        timeout = {rng.randint(10, 30)}  # raised for slow reservations")
            out.append("+        log.debug(\"timeout configured: %s\", timeout)")
            for _c in range(3):
                out.append(f"     {rng.choice(['    ', '        '])}{rng.choice(['yield', 'raise', 'await', 'assert'])} {_hex(rng, 6)}")
    return "\n".join(out) + "\n"


def grep_n_output(rng: random.Random, token: str, *, n: int = 140) -> str:
    """``rg -n <token> src tests`` output: ``path:line:content`` rows."""
    paths = [f"src/{pkg}/{mod}.py" for pkg in ("orders", "inventory", "ledger", "payments", "shared") for mod in ("service", "api", "client", "models")]
    paths += [f"tests/test_{x}.py" for x in ("orders", "inventory", "ledger", "payments", "api")]
    rows = []
    templates = [
        "        raise {t}(sku, expected=expected, actual=actual)",
        "    except {t} as exc:",
        "        log.error(\"{t}: %s\", exc)",
        "from shop.inventory import {t}",
        "        with pytest.raises({t}):",
        "    # TODO: surface {t} to the caller instead of swallowing it",
        "class {t}(RuntimeError):",
    ]
    for _ in range(n):
        rows.append((rng.choice(paths), rng.randint(5, 480), rng.choice(templates).format(t=token)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return "\n".join(f"{p}:{ln}:{txt}" for p, ln, txt in rows) + "\n"


def search_matches(rng: random.Random, token: str, *, n: int = 24) -> list[tuple[str, int, str]]:
    """Matches for a ``search_files`` call (path, line, content), grouped by path."""
    paths = [f"src/{pkg}/{mod}.py" for pkg in ("orders", "inventory", "ledger") for mod in ("service", "api", "client")]
    rows = []
    for _ in range(n):
        rows.append((rng.choice(paths), rng.randint(5, 300), f"    {rng.choice(['return', 'yield', 'await', 'assert'])} {token}(order_{rng.randint(1, 99)}, ledger)"))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


# --------------------------------------------------------------------------------------------
# JSON API dump (gh api style)
# --------------------------------------------------------------------------------------------


def gh_api_runs(
    rng: random.Random, n: int = 300, *, plant_index: int | None = None, plant_name: str = "deploy-9d41"
) -> str:
    """``gh api repos/.../actions/runs --paginate | jq``-like list of ``n`` compact objects (JSON text)."""
    names = ["ci", "deploy-prod", "deploy-staging", "lint", "release", "nightly", "e2e"]
    objs = []
    for i in range(n):
        name = rng.choice(names)
        if name.startswith("deploy"):
            name = f"{name}-{_hex(rng, 4)}"
        objs.append(
            {
                "id": 9_100_000 + i * 7 + rng.randint(0, 5),
                "name": name,
                "head_branch": rng.choice(["main", "main", "fix/retry-jitter", "feat/search-v2", "release/2025.03"]),
                "head_sha": _hex(rng, 12),
                "conclusion": rng.choice(["success", "success", "success", "failure", "cancelled"]),
                "created_at": f"2025-03-{rng.randint(1, 14):02d}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z",
            }
        )
    if plant_index is not None:
        objs[plant_index]["name"] = plant_name
        objs[plant_index]["conclusion"] = "failure"
        objs[plant_index]["head_branch"] = "main"
    return json.dumps(objs)


# --------------------------------------------------------------------------------------------
# SKILL.md body
# --------------------------------------------------------------------------------------------


def skill_body(rng: random.Random, *, n_sections: int = 9) -> str:
    """A ~8 KB SKILL.md for a debugging skill."""
    head = (
        "---\nname: systematic-debugging\ndescription: Hypothesis-driven debugging workflow\n---\n\n"
        "# Systematic debugging\n\nUse this skill when a failure has no obvious cause yet.\n"
    )
    sections = []
    topics = ["Reproduce first", "Narrow the blast radius", "Read the logs end to end", "Form one hypothesis at a time",
              "Instrument, do not guess", "Bisect", "Fix the cause, not the symptom", "Add a regression test", "Write down what you learned"]
    for i in range(n_sections):
        t = topics[i % len(topics)]
        body = "\n".join(
            f"{j}. {rng.choice(['Check', 'Confirm', 'Record', 'Compare', 'Isolate'])} "
            f"{rng.choice(['the failing input', 'the exact error text', 'the first bad commit', 'timing around the failure', 'config drift between environments'])} "
            f"before {rng.choice(['changing code', 'proposing a fix', 'restarting services', 'closing the ticket'])}."
            for j in range(1, 8)
        )
        sections.append(f"\n## {i + 1}. {t}\n\n{body}\n")
    return head + "".join(sections)
