"""The ``smallrows`` and ``i18n`` replay scenarios.

* ``smallrows``  40 turns of an audit across twelve services: almost every result is 800-3,000 characters (a
                 manifest, requirement pins, a health document, a short log, a ``git log``, a TODO grep, a test
                 summary). Per-row overhead, Hermes' minimum result size for pruning (8,000 characters) and the
                 plugin's (2,000) matter here; there are no big rows to cut.
* ``i18n``       32 turns of a support triage: logs, customer tickets and message catalogs in Chinese, Japanese,
                 Korean, Arabic and Russian (``ensure_ascii=False``, as the real tools print them), a catalog
                 patched and re-read, user messages in Japanese and Chinese.
"""
from __future__ import annotations

import json
import random

import payloads_extra as pe
import payloads_rows as pr
from fixtures import hermes_payloads as hp
from scenario import Scenario
from scenario_kit import Kit, ScenarioSpec

# =================================================================================================
# smallrows
# =================================================================================================
SMALLROWS_TURNS = 40

SMALLROWS_FINAL_QUESTION = (
    "Write the audit summary for the upgrade ticket. I need: the billing pool size, the ledger schema version, the "
    "ticket behind the tax VAT fallback, the CVE behind the cryptography pin in payments, the expired hold in the "
    "checkout log, the cart change that went in last, the inventory test that fails, the search timeout, which "
    "service still pins sqlalchemy 1.4, which services we skip and why, what we decided about ledger and about "
    "Python, the upgrade order, where the audit goes, when the upgrade starts, and who to ping and where."
)

_AUDIT = (  # (service, user remark or None); mailer and notify are skipped by decision
    "auth", "billing", "catalog", "cart", "checkout", "inventory", "ledger", "orders", "payments", "search", "shipping", "tax",
)


def build_smallrows(seed: int = 0, turns: int = SMALLROWS_TURNS) -> Scenario:
    kit = Kit("smallrows", seed)
    step, rnd, tool = kit.step, kit.rnd, hp.terminal_result
    roster = pe.PodRoster(kit.rng("roster"))
    repo = "/srv/shop"

    def cat(svc: str, name: str, text: str, nth: int | None = None):
        return step("terminal", {"command": f"cat {repo}/services/{svc}/{name}"}, tool(text), nth=nth)

    def service_round(svc: str, *, yaml_plant=None, req_plant=None, schema=None, degraded=None, log_plants=None,
                      git_plant=None, todo_plant=None, n_errors=3, failed=(), nth=None):
        """The two rounds the audit runs for one service; returns ``(rounds, error lines)``."""
        r = lambda tag: kit.rng(f"{svc}:{tag}:{nth or 1}")
        log = pr.service_log(r("log"), svc, n_errors=n_errors, plants=log_plants)
        log_text, errs = log.window(0, len(log.lines))
        rounds = []
        if nth is None:
            rounds.append(rnd(
                f"Reading the manifest, the pins and the layout of {svc}.",
                cat(svc, "service.yaml", pr.service_yaml(r("yaml"), svc, plant=yaml_plant)),
                cat(svc, "requirements.txt", pr.requirements_txt(r("req"), svc, plant=req_plant)),
                step("terminal", {"command": f"ls -la {repo}/services/{svc}"}, tool(pr.ls_la(r("ls"), svc))),
                step("terminal", {"command": f"kubectl -n shop describe deploy/{svc}"}, tool(pr.deploy_describe(r("describe"), svc))),
                step("terminal", {"command": f"curl -s http://{svc}.shop.svc:{pr._PORTS[svc]}/debug/config"}, tool(pr.config_dump_json(r("config"), svc))),
            ))
        health = step("terminal", {"command": f"curl -s http://{svc}.shop.svc:{pr._PORTS[svc]}/healthz"},
                      tool(pr.healthz_json(r("health"), svc, schema=schema, degraded=degraded)), nth=nth)
        logs = step("terminal", {"command": f"docker logs --tail 28 {svc}"}, tool(log_text), nth=nth)
        metrics = step("terminal", {"command": f"curl -s http://{svc}.shop.svc:{pr._PORTS[svc]}/metrics | head -n 42"},
                       tool(pr.metrics_head(r("metrics"), svc)), nth=nth)
        if nth is None:
            rounds.append(rnd(
                f"Health, metrics, logs, history and TODOs of {svc}.", health, metrics, logs,
                step("terminal", {"command": f"git -C {repo} log --oneline -n 24 -- services/{svc}"},
                     tool(pr.git_log_for(r("git"), svc, plant=git_plant))),
                step("terminal", {"command": f"rg -n 'TODO|FIXME' {repo}/services/{svc}"},
                     tool(pr.todo_grep(r("todo"), svc, plant=todo_plant))),
            ))
        else:
            rounds.append(rnd(f"Checking {svc} again.", health, metrics, logs,
                              step("terminal", {"command": f"curl -s http://{svc}.shop.svc:{pr._PORTS[svc]}/debug/config"},
                                   tool(pr.config_dump_json(r("config"), svc)), nth=nth)))
        if failed or nth is None:
            text, perrs = pr.pytest_summary(r("pytest"), svc, passed=38 + len(svc), failed=failed)
            errs = errs + perrs
            rounds.append(rnd(f"Running the tests of {svc}.",
                              step("terminal", {"command": f"cd {repo}/services/{svc} && pytest -q"}, tool(text, exit_code=1 if failed else 0), nth=nth)))
        return rounds, errs

    # ---- T1 ---------------------------------------------------------------------------------------
    t1 = kit.turn(
        "We are auditing every service before the 2025.04 base image upgrade. Skip mailer and notify, they are being retired. "
        "Python 3.12 only, no 3.13 yet. The upgrade starts on 2025-04-07. Start with the cluster view.",
        [rnd("Listing the releases and the load.",
             step("terminal", {"command": "helm -n shop list"}, tool(pr.helm_list(kit.rng("helm")))),
             step("terminal", {"command": "kubectl -n shop top pods"}, tool(pr.top_pods_services(kit.rng("top1")))),
             step("terminal", {"command": f"ls {repo}/services"}, tool("\n".join(pr.SERVICES) + "\n")))],
        "Fourteen releases are deployed; I will audit twelve of them and leave out mailer and notify.",
    )
    kit.fact("D1", "decision", "Skip mailer and notify, they are being retired", t1, "user", True)
    kit.fact("D2", "decision", "Python 3.12 only", t1, "user", True)
    kit.fact("C1", "continuation", "starts on 2025-04-07", t1, "user", True)

    def service_turn(svc: str, user: str, final: str, **kw):
        rounds, errs = service_round(svc, **kw)
        t = kit.turn(user, rounds, final)
        kit.errors(t, errs)
        return t

    # ---- T2-T13: the first pass ------------------------------------------------------------------------
    service_turn("auth", "Audit auth.", "auth is on the shared pins with no health warnings; its logs show two upstream timeouts.")
    t = service_turn("billing", "billing next.", "billing runs the biggest pool of the stack; no failing tests.",
                     yaml_plant={"DB_POOL_MAX": "47"})
    kit.fact("R1", "recall", "DB_POOL_MAX: 47", t, "tool", True, f"terminal:cat {repo}/services/billing/service.yaml")
    service_turn("catalog", "catalog.", "catalog is clean apart from old TODOs.")
    t = service_turn("cart", "cart.", "cart has had an active week.",
                     git_plant={0: "e9b1c0d cart: switch hold writes to optimistic locking (#2288)"})
    kit.fact("R2", "recall", "optimistic locking (#2288)", t, "tool", True, f"terminal:git -C {repo} log --oneline -n 24 -- services/cart")
    t = service_turn("checkout", "checkout.", "checkout logs an expired hold; worth a closer look later.", n_errors=4,
                     log_plants={9: ("ERROR", "checkout.holds", "hold h-5521773 expired before capture (order=ORD-{o})")})
    kit.fact("R3", "recall", "hold h-5521773", t, "tool", True, "terminal:docker logs --tail 28 checkout")
    t = service_turn("inventory", "inventory.", "inventory has one failing test; the rest is fine.",
                     failed=("test_reserve_is_idempotent",))
    kit.fact("A1", "artifact", "test_reserve_is_idempotent", t, "tool", True, f"terminal:cd {repo}/services/inventory && pytest -q")
    t = service_turn("ledger", "ledger. Do not bump its base image until its schema migration has shipped.",
                     "ledger reports its schema version in the health document; I noted it.", schema="2025.03.2-lg-81")
    kit.fact("D3", "decision", "Do not bump its base image until its schema migration has shipped", t, "user", True)
    kit.fact("R4", "recall", "2025.03.2-lg-81", t, "tool", True, f"terminal:curl -s http://ledger.shop.svc:{pr._PORTS['ledger']}/healthz")
    service_turn("orders", "orders.", "orders is on the shared pins.")
    t = service_turn("payments", "payments.", "payments pins cryptography below the shared version on purpose.",
                     req_plant={14: "cryptography==41.0.3  # pinned: workaround for CVE-2023-49083, see SHOP-5337"})
    kit.fact("R5", "recall", "CVE-2023-49083", t, "tool", True, f"terminal:cat {repo}/services/payments/requirements.txt")
    t = service_turn("search", "search.", "search has a long request timeout.", yaml_plant={"REQUEST_TIMEOUT_MS": "4200"})
    kit.fact("R6", "recall", "REQUEST_TIMEOUT_MS: 4200", t, "tool", True, f"terminal:cat {repo}/services/search/service.yaml")
    service_turn("shipping", "shipping.", "shipping is clean.")
    t = service_turn("tax", "tax. Then we compare pins across the stack.", "tax keeps a VAT fallback with a removal date in a TODO.",
                     todo_plant={3: "services/tax/service.py:212:    # TODO(SHOP-5521): remove the vat fallback after 2025-06-30"})
    kit.fact("R7", "recall", "SHOP-5521", t, "tool", True, f"terminal:rg -n 'TODO|FIXME' {repo}/services/tax")

    # ---- T14-T19: cross-cutting pin comparisons ------------------------------------------------------------------
    def versions_turn(lib: str, user: str, final: str, plant: bool = False):
        text = pr.versions_grep(kit.rng("versions:" + lib), lib)
        if plant:
            text = text.replace(next(l for l in text.splitlines() if "/orders/" in l), "services/orders/requirements.txt:12:sqlalchemy==1.4.52")
        return kit.turn(user, [rnd(f"Comparing {lib} pins.",
                                    step("terminal", {"command": f"rg -n '^{lib}==' {repo}/services/*/requirements.txt"}, tool(text)))], final)

    t = versions_turn("sqlalchemy", "Which sqlalchemy versions are pinned?", "Everything is on 2.0 except one service.", plant=True)
    kit.fact("R8", "recall", "sqlalchemy==1.4.52", t, "tool", True, f"terminal:rg -n '^sqlalchemy==' {repo}/services/*/requirements.txt")
    versions_turn("pydantic", "And pydantic?", "pydantic is on 2.6 everywhere.")
    versions_turn("fastapi", "fastapi?", "fastapi differs by a patch release.")
    versions_turn("redis", "redis client?", "Same story.")
    kit.turn("Decide the order for me.", [], "Decision D-21: upgrade auth first, then payments, then the rest in alphabetical order.")
    kit.fact("D4", "decision", "Decision D-21", 18, "assistant", True)

    # ---- T19-T30: second look at the services that need one ------------------------------------------------------------
    for svc, user in (("checkout", "Check checkout again."), ("payments", "payments again."), ("inventory", "inventory once more."),
                      ("ledger", "ledger again."), ("orders", "orders again."), ("search", "search again."),
                      ("checkout", "checkout, third look."), ("payments", "payments, third look."),
                      ("ledger", "ledger, third look."), ("orders", "orders, third look."), ("search", "search, third look.")):
        nth = 2 if user.endswith("again.") or user.endswith("more.") else 3
        rounds, errs = service_round(svc, nth=nth, n_errors=2, degraded="inventory" if svc == "checkout" else None,
                                     failed=("test_reserve_is_idempotent",) if svc == "inventory" else ())
        t = kit.turn(user, rounds, f"{svc}: no change worth noting.")
        kit.errors(t, errs)
    kit.turn("Cluster load now?", [rnd("Looking at the load.", step("terminal", {"command": "kubectl -n shop top pods"}, tool(pr.top_pods_services(kit.rng("top2")), ), nth=2))],
             "Load is normal.")
    kit.turn("And the releases?", [rnd("Listing the releases.", step("terminal", {"command": "helm -n shop list"}, tool(pr.helm_list(kit.rng("helm2"))), nth=2))],
             "No release changed since the start of the audit.")
    kit.turn("Pods in the shop namespace.", [rnd("Listing pods.", step("terminal", {"command": "kubectl -n shop get pods -o wide"}, tool(roster.get_pods("stable", 90))))],
             "All pods are ready.")
    kit.turn("Anything noisy in the sandbox clusters?", [rnd("Checking the clusters.",
             step("terminal", {"command": "kubectl --context sandbox-1 -n shop top pods"}, tool(pr.top_pods_services(kit.rng("top3")))),
             step("terminal", {"command": "helm --kube-context sandbox-1 -n shop list"}, tool(pr.helm_list(kit.rng("helm3")))))],
             "The sandbox mirrors production.")

    # ---- T31-T37: write-up --------------------------------------------------------------------------------------------------
    t = kit.turn(
        "Put the findings into docs/audit/2025-04-base-image.md and, when it is done, ping @platform-oncall in #platform-upgrade.",
        [], "I will write the findings into the audit document. TODO(next): re-run the inventory tests after SHOP-5460 lands.")
    kit.fact("A2", "artifact", "docs/audit/2025-04-base-image.md", t, "user", True)
    kit.fact("C2", "continuation", "@platform-oncall in #platform-upgrade", t, "user", True)
    kit.fact("C3", "continuation", "re-run the inventory tests after SHOP-5460 lands", t, "assistant", True)
    audit_doc = (
        "# 2025-04 base image audit\n\n" + "\n".join(f"- {s}: reviewed, see findings below" for s in _AUDIT)
        + "\n\n## Findings\n\n- sqlalchemy 1.4 pin in orders\n- cryptography pin in payments (CVE workaround)\n- tax VAT fallback\n"
        + "- inventory: one failing idempotency test\n"
    )
    t = kit.turn(
        "Write it.",
        [rnd("Writing the audit document.",
             step("write_file", {"path": "docs/audit/2025-04-base-image.md", "content": audit_doc}, pe.write_file_result(audit_doc, dirs_created=True)))],
        "The audit document is written.",
    )
    t = kit.turn("Let me know what else would help.", [], "I could script the pin comparison as scripts/audit_pins.sh so the next audit is one command.")
    kit.fact("A3", "artifact", "scripts/audit_pins.sh", t, "assistant", False)
    kit.turn("Good idea, but later.", [], "Noted.")
    kit.turn("Run the whole audit tests once more for the record.",
             [rnd("Running the tests of two services.",
                  step("terminal", {"command": f"cd {repo}/services/auth && pytest -q"}, tool(pr.pytest_summary(kit.rng("final-auth"), "auth", passed=44)[0]), nth=2),
                  step("terminal", {"command": f"cd {repo}/services/billing && pytest -q"}, tool(pr.pytest_summary(kit.rng("final-billing"), "billing", passed=51)[0]), nth=2))],
             "Both pass.")
    kit.turn("I think the audit is complete.", [], "Agreed.")
    return kit.finish(SMALLROWS_FINAL_QUESTION, turns, natural=SMALLROWS_TURNS, log_turn=3)


# =================================================================================================
# i18n
# =================================================================================================
I18N_TURNS = 32

I18N_FINAL_QUESTION = (
    "Write the handoff for the regional support leads. I need: the incident id, the first order that failed in the "
    "Chinese console log, the Arabic ticket about the missing order, the Russian error about the payment gateway "
    "that kept repeating, which catalog string we corrected and in which file, how many tickets were still open, "
    "what we decided about the Korean refund template and about the Arabic right-to-left layout, the Japanese "
    "carrier we contact, and the next steps (who to notify, which review to wait for)."
)

_CATALOG_KEYS = ("checkout.confirm", "checkout.address_error", "checkout.payment_failed", "order.shipped", "order.cancelled",
                 "refund.requested", "refund.approved", "cart.empty", "cart.item_removed", "account.locked")
_CATALOG_TEXT = {
    "ko": ["주문이 접수되었습니다", "배송 주소를 확인해 주세요", "결제에 실패했습니다. 다시 시도해 주세요", "상품이 발송되었습니다", "주문이 취소되었습니다",
           "환불이 요청되었습니다", "환불이 승인되었습니다", "장바구니가 비어 있습니다", "상품이 삭제되었습니다", "계정이 잠겼습니다"],
    "ru": ["Заказ принят", "Проверьте адрес доставки", "Платёж не прошёл, попробуйте ещё раз", "Товар отправлен", "Заказ отменён",
           "Запрошен возврат средств", "Возврат средств одобрен", "Корзина пуста", "Товар удалён из корзины", "Учётная запись заблокирована"],
    "ar": ["تم استلام طلبك", "يرجى التحقق من عنوان الشحن", "فشل الدفع، يرجى المحاولة مرة أخرى", "تم شحن المنتج", "تم إلغاء الطلب",
           "تم طلب استرداد المبلغ", "تمت الموافقة على الاسترداد", "سلة التسوق فارغة", "تمت إزالة المنتج", "تم قفل الحساب"],
}


def _catalog(rng: random.Random, lang: str, *, n: int = 140, fix: bool = False) -> str:
    """``locales/<lang>.json``: message keys to localized strings (``n`` entries, one per line)."""
    texts = _CATALOG_TEXT[lang]
    entries = {}
    for i in range(n):
        base = i % len(_CATALOG_KEYS)
        entries[f"{_CATALOG_KEYS[base]}.v{i // len(_CATALOG_KEYS) + 1}"] = texts[base] + (f" ({rng.randint(100, 999)})" if i % 7 == 0 else "")
    bad = "refund.approved.v3"
    if lang == "ko":
        entries[bad] = "환불이 승인되었습니다" if fix else "환불이 거부되었습니다"  # "refund rejected" where "approved" is meant
    return json.dumps(entries, ensure_ascii=False, indent=2)


def build_i18n(seed: int = 0, turns: int = I18N_TURNS) -> Scenario:
    kit = Kit("i18n", seed)
    step, rnd, tool = kit.step, kit.rnd, hp.terminal_result

    def tail(lang: str, name: str, window: tuple[int, int], log: pe.Log, nth: int | None = None):
        text, errs = log.window(*window)
        return step("terminal", {"command": f"tail -n {window[1] - window[0]} /var/log/support/{name}.log"}, tool(text), nth=nth), errs

    sec0 = 2 * 3600 + 5 * 60
    zh = pr.i18n_log(kit.rng("zh"), "zh", n_lines=330, start_sec=sec0, n_errors=26,
                     plants={52: ("ERROR", "库存", "库存预留失败: 订单 ORD-5530917, SKU-48213 库存不足 (剩余=0, 需要=2)")})
    ja = pr.i18n_log(kit.rng("ja"), "ja", n_lines=300, start_sec=sec0, n_errors=20)
    ko = pr.i18n_log(kit.rng("ko"), "ko", n_lines=300, start_sec=sec0, n_errors=20)
    ar = pr.i18n_log(kit.rng("ar"), "ar", n_lines=260, start_sec=sec0, n_errors=18)
    ru = pr.i18n_log(kit.rng("ru"), "ru", n_lines=520, start_sec=sec0, n_errors=40,
                     plants={77: ("ERROR", "оплата", "Таймаут платёжного шлюза: заказ ORD-6610284 (попытка 3)")})
    ru_win = [(0, 160), (120, 280), (240, 400), (360, 520)]
    ko_catalog_old, ko_catalog_new = _catalog(kit.rng("cat-ko"), "ko"), _catalog(kit.rng("cat-ko"), "ko", fix=True)
    ar_catalog = _catalog(kit.rng("cat-ar"), "ar")
    assert ko_catalog_old != ko_catalog_new

    # ---- T1 ---------------------------------------------------------------------------------------
    t1 = kit.turn(
        "Incident INC-20250314-0522: orders from the Asian, Arabic and Russian storefronts fail at the stock reservation. "
        "I am on call. Start with the Chinese console log. The review checklist for catalog changes is docs/i18n/review-checklist.md.",
        [rnd("Tailing the Chinese console log.", tail("zh", "console-zh", (0, 330), zh)[0])],
        "The Chinese log shows stock reservation failures followed by payment timeouts.",
    )
    kit.errors(t1, zh.window(0, 330)[1])
    kit.fact("R1", "recall", "INC-20250314-0522", t1, "user", True)
    kit.fact("A2", "artifact", "docs/i18n/review-checklist.md", t1, "user", True)
    kit.fact("R2", "recall", "ORD-5530917", t1, "tool", True, "terminal:tail -n 330 /var/log/support/console-zh.log")
    # ---- T2-T5: the other languages ------------------------------------------------------------------------------
    for i, (lang, name, log, window, user) in enumerate((("ja", "console-ja", ja, (0, 300), "Same for Japanese."),
                                                          ("ko", "console-ko", ko, (0, 300), "Korean."),
                                                          ("ar", "console-ar", ar, (0, 260), "Arabic."))):
        step_, errs = tail(lang, name, window, log)
        t = kit.turn(user, [rnd(f"Tailing the {lang} console log.", step_)], f"The {lang} log shows the same reservation pattern.")
        kit.errors(t, errs)
    # ---- T5: Russian, first tail ------------------------------------------------------------------------------------
    step_, errs = tail("ru", "console-ru", ru_win[0], ru, nth=1)
    t = kit.turn("And Russian.", [rnd("Tailing the Russian console log.", step_)], "Payment gateway timeouts dominate the Russian log.")
    kit.errors(t, errs)
    kit.fact("R3", "recall", "ORD-6610284", t, "tool", True, "terminal:tail -n 160 /var/log/support/console-ru.log#1")
    # ---- T6: decisions in the user's own scripts ---------------------------------------------------------------------------
    t6 = kit.turn(
        "韓国語の返金テンプレートは今回は変更しない。アラビア語は右から左のレイアウトを維持すること。",
        [], "Decision D-31: the Korean refund template stays as it is in this fix, and the Arabic pages keep their right-to-left layout.")
    kit.fact("D1", "decision", "韓国語の返金テンプレートは今回は変更しない", t6, "user", True)
    kit.fact("D2", "decision", "Decision D-31", t6, "assistant", True)
    # ---- T7: tickets -------------------------------------------------------------------------------------------------------------
    ticket_cmd = "curl -s 'http://support.shop.svc/api/v2/tickets?status=open,pending&limit=60'"
    open_tickets = pr.i18n_messages_json(kit.rng("tickets1"), n=60, plants={7: "لم يصل طلبي ORD-5530944 منذ ثلاثة أيام، أحتاج إلى رد عاجل."})
    t7 = kit.turn("Pull the open tickets.", [rnd("Fetching the tickets.", step("terminal", {"command": ticket_cmd}, tool(open_tickets), nth=1))],
                  "Sixty tickets in five languages; the Arabic ones mention missing orders.")
    kit.fact("R4", "recall", "لم يصل طلبي ORD-5530944 منذ ثلاثة أيام", t7, "tool", True, "terminal:" + ticket_cmd + "#1")
    # ---- T8: glossary -------------------------------------------------------------------------------------------------------------
    t = kit.turn("Show the status labels config.",
                 [rnd("Reading the label config.", step("terminal", {"command": "cat /srv/shop/config/i18n/status_labels.yaml"}, tool(pr.i18n_glossary_yaml(kit.rng("gloss"))), nth=1))],
                 "The status labels are in six languages and version 7.")
    kit.fact("A3", "artifact", "tools/i18n/export_labels.py", t, "tool", False, "terminal:cat /srv/shop/config/i18n/status_labels.yaml#1")
    # ---- T9-T11: Korean catalog read -> patch -> re-read -----------------------------------------------------------------------------
    kit.turn("Read the Korean message catalog.", [rnd("Reading locales/ko.json.", step("read_file", {"path": "locales/ko.json"}, hp.read_file_result(ko_catalog_old), nth=1))],
             "One string looks wrong: refund.approved.v3 says rejected where it means approved.")
    old_s, new_s = '"refund.approved.v3": "환불이 거부되었습니다"', '"refund.approved.v3": "환불이 승인되었습니다"'
    assert old_s in ko_catalog_old
    diff = pe.unified_diff("locales/ko.json", ko_catalog_old, ko_catalog_new)
    t = kit.turn(
        "Fix that string and read the file again.",
        [rnd("Correcting the string.", step("patch", {"path": "locales/ko.json", "old_string": old_s, "new_string": new_s}, hp.patch_result(diff, ["locales/ko.json"]))),
         rnd("Re-reading the catalog.", step("read_file", {"path": "locales/ko.json"}, hp.read_file_result(ko_catalog_new), nth=2))],
        "The Korean catalog is corrected: refund.approved.v3 now says approved.",
    )
    kit.fact("A1", "artifact", "locales/ko.json", t, "assistant", True)
    kit.fact("R5", "recall", "환불이 승인되었습니다", t, "tool", False, "read_file:locales/ko.json#2")
    kit.turn("And the Arabic catalog, I want to compare.", [rnd("Reading locales/ar.json.", step("read_file", {"path": "locales/ar.json"}, hp.read_file_result(ar_catalog)))],
             "The Arabic catalog is complete.")
    # ---- T12-T14: second looks ---------------------------------------------------------------------------------------------------------
    for k in (1, 2, 3):
        step_, errs = tail("ru", "console-ru", ru_win[k], ru, nth=k + 1)
        t = kit.turn("Tail the Russian log again.", [rnd("Tailing the Russian console log.", step_)], "The Russian log keeps growing; the gateway timeouts continue." if k < 3 else "The Russian gateway timeouts have stopped.")
        kit.errors(t, errs)
    # ---- T15: grep over templates -------------------------------------------------------------------------------------------------------
    rows = "\n".join(
        f"templates/{n}.html:{ln}:    <p>{txt}</p>" for n, ln, txt in sorted(
            (kit.rng("tpl").choice(["checkout", "order", "refund", "cart"]), kit.rng(f"tpl{i}").randint(4, 90),
             kit.rng(f"tpl{i}").choice(["注文を確認してください", "请确认您的订单", "الرجاء تأكيد الطلب", "Пожалуйста, подтвердите заказ", "주문을 확인해 주세요"]))
            for i in range(40)))
    kit.turn("Where are hard-coded strings in the templates?",
             [rnd("Searching the templates.", step("terminal", {"command": "rg -n '<p>[^<{]*[^\\x00-\\x7F]' templates"}, tool(rows + "\n")))],
             "Forty hard-coded strings in four templates; they should move to the catalogs.")
    # ---- T16-T17: more tickets -----------------------------------------------------------------------------------------------------------
    solved = pr.i18n_messages_json(kit.rng("tickets2"), n=60)
    kit.turn("Pull the tickets again, I want to see what changed.",
             [rnd("Fetching the tickets.", step("terminal", {"command": ticket_cmd}, tool(solved), nth=2))], "A few tickets were solved in the meantime.")
    count_cmd = "curl -s 'http://support.shop.svc/api/v2/tickets/count?status=open'"
    t = kit.turn("How many are still open?",
                 [rnd("Counting.", step("terminal", {"command": count_cmd}, tool(json.dumps({"status": "open", "summary": "214 open tickets across 5 languages", "by_lang": {"zh": 51, "ja": 38, "ko": 29, "ar": 57, "ru": 39}}, ensure_ascii=False))))],
                 "The count is in the response.")
    kit.fact("R6", "recall", "214 open tickets across 5 languages", t, "tool", True, "terminal:" + count_cmd)
    # ---- T18-T20: carrier and review ------------------------------------------------------------------------------------------------------
    t = kit.turn(
        "The Japanese carrier is ヤマト運輸; contact them if the shipments are late. Wait for the review from @i18n-review before shipping the catalog fix, "
        "and notify the leads in #support-apac.",
        [], "Noted.")
    kit.fact("D3", "decision", "Wait for the review from @i18n-review", t, "user", True)
    kit.fact("C1", "continuation", "notify the leads in #support-apac", t, "user", True)
    kit.fact("R7", "recall", "ヤマト運輸", t, "user", True)
    ja_step, errs = tail("ja", "console-ja", (150, 300), ja, nth=2)
    t = kit.turn("Look at the Japanese log again.", [rnd("Tailing the Japanese console log.", ja_step)], "Fewer reservation failures in the second half.")
    kit.errors(t, errs)
    zh_step, errs = tail("zh", "console-zh", (160, 330), zh, nth=2)
    t = kit.turn("And Chinese.", [rnd("Tailing the Chinese console log.", zh_step)], "The burst is over in the Chinese log.")
    kit.errors(t, errs)
    kit.turn("Pull the tickets by language for the leads.",
             [rnd("Fetching.", step("terminal", {"command": ticket_cmd}, tool(pr.i18n_messages_json(kit.rng("tickets3"), n=40)), nth=3))],
             "Forty tickets in the by-language view.")
    # ---- T22-T30 --------------------------------------------------------------------------------------------------------------------------------
    t = kit.turn("Write the TODO list.", [], "TODO(next): ask @i18n-review to approve locales/ko.json, then deploy the catalog fix to the Korean storefront.")
    kit.fact("C2", "continuation", "ask @i18n-review to approve locales/ko.json", t, "assistant", False)
    kit.turn("Show me the status labels again.",
             [rnd("Reading the label config.", step("terminal", {"command": "cat /srv/shop/config/i18n/status_labels.yaml"}, tool(pr.i18n_glossary_yaml(kit.rng("gloss"))), nth=2))],
             "Unchanged.")
    ar_step, errs = tail("ar", "console-ar", (100, 260), ar, nth=2)
    t = kit.turn("Arabic log, second half.", [rnd("Tailing the Arabic console log.", ar_step)], "Reservation failures are fading.")
    kit.errors(t, errs)
    ko_step, errs = tail("ko", "console-ko", (150, 300), ko, nth=2)
    t = kit.turn("Korean log, second half.", [rnd("Tailing the Korean console log.", ko_step)], "Same.")
    kit.errors(t, errs)
    kit.turn("Print the Korean catalog once more for the review request.", [rnd("Re-reading locales/ko.json.", step("read_file", {"path": "locales/ko.json"}, hp.read_file_result(ko_catalog_new), nth=3))],
             "Same content as before.")
    kit.turn("Read the Russian catalog too.",
             [rnd("Reading locales/ru.json.", step("read_file", {"path": "locales/ru.json"}, hp.read_file_result(_catalog(kit.rng("cat-ru"), "ru"))))],
             "The Russian catalog is complete.")
    kit.turn("Show me the diff of the locales.",
             [rnd("Diffing.", step("terminal", {"command": "git -C /srv/shop diff -- locales"}, tool(
                 "diff --git a/locales/ko.json b/locales/ko.json\nindex 41c7d02..9be3a18 100644\n"
                 + pe.unified_diff("locales/ko.json", ko_catalog_old, ko_catalog_new, context=3) + "\n")))],
             "One line changed in the Korean catalog.")
    kit.turn("Any other config with strings?", [rnd("Looking.", step("terminal", {"command": "ls -la /srv/shop/locales"}, tool("total 52\n-rw-r--r-- 1 deploy deploy 6120 Mar 14 01:12 ar.json\n-rw-r--r-- 1 deploy deploy 5410 Mar 14 02:41 ko.json\n-rw-r--r-- 1 deploy deploy 5322 Mar 12 09:30 ja.json\n-rw-r--r-- 1 deploy deploy 4960 Mar 12 09:30 zh.json\n-rw-r--r-- 1 deploy deploy 6088 Mar 12 09:30 ru.json\n")))],
             "Five catalogs, one per language.")
    kit.turn("Good.", [], "Anything else?")
    kit.turn("No, I think we are done.", [], "Agreed.")
    return kit.finish(I18N_FINAL_QUESTION, turns, natural=I18N_TURNS, log_turn=1)


SMALLROWS_SPEC = ScenarioSpec(
    "smallrows", build_smallrows, SMALLROWS_TURNS, 4,
    "40 turns, many mid-size results (800-3,000 chars): per-service manifests, pins, health, short logs, git log, TODO greps",
)
I18N_SPEC = ScenarioSpec(
    "i18n", build_i18n, I18N_TURNS, 4,
    "32 turns of CJK / Arabic / Russian logs, ticket JSON and message catalogs (ensure_ascii=False); user text in Japanese",
)
