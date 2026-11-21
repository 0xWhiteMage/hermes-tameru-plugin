"""The ``plan`` policy's pass machinery (adapter side of ``tameru.plan``, compact mode).

At a Hermes cache-broken moment — its compaction, or a prune Hermes itself fires — the pass builds a
``PlanInput`` over the rows Hermes would demote, asks the engine's ``plan()`` for the per-row rung,
and the ladder renders the choice. Only trigger events plan (``H = 0``, hysteresis ``K = 3``); the
adaptive policy's activation/target/runway constants do not apply. Every host number — Hermes'
threshold, the scaled retained budget, the growth-derived ``n_eff`` — is computed here in the
adapter; ranking and vetoes are the engine's.
"""
from __future__ import annotations

from dataclasses import replace as dataclass_replace

from .config import TameruSettings
from .hermes_compat import (
    SKILL_PRUNED_MARKER_PREFIX,
    is_persisted_block,
    message_budget_tokens,
    refused_summary,
)
from .payload import content_hint, parse_payload
from .query import _is_scaffolding, build_query, usable_query
from .render import classify_render, make_ref
from .supersede import build_index, superseded_by
from .tameru.compress_context import _protected_facts, error_fingerprints
from .tameru.decide import EVPolicy, HERMES_POLICY
from .tameru.plan import CacheState, Candidate, PlanInput, PlanItem, plan as engine_plan
from .tameru.transcript import build_selector_query

PLAN_K = 3                  # engine-side hysteresis between non-trigger acts
PLAN_H = 0                  # forecast horizon: no proactive warm-step acts
PLAN_LOSS_PROFILE = "replay-2026-11-21"
PLAN_EV_STRENGTH = {"save_space": "less", "balanced": "normal", "preserve_detail": "more"}
PLAN_DEFAULT_STEP_TOKENS = 4000   # n_eff divisor until per-step growth is observed
RISK_CEILINGS = ("low", "medium", "high")


def ev_strength(settings: TameruSettings) -> str:
    return PLAN_EV_STRENGTH.get(settings.preference, "normal")


def observe_body(engine, body: int) -> None:
    """Per-request body sizes feed the mean step growth behind n_eff."""
    last = getattr(engine, "_plan_last_body", None)
    if last is not None and body > last:
        engine._plan_growth.append(body - last)
    elif last is not None and body < last:
        engine._plan_growth.clear()
    engine._plan_last_body = body


def step_tokens(engine) -> int:
    samples = engine._plan_growth
    if samples:
        ordered = sorted(samples)
        return max(1, ordered[len(ordered) // 2])
    return PLAN_DEFAULT_STEP_TOKENS


def _spill_note(content: str) -> str:
    from .engine import _spill_note as note   # late: sibling module (engine delegates here)
    return note(content)


def _digest(text: str) -> str:
    from .engine import _digest as d          # late: same reason
    return d(text)


def _view(scope, result):
    from .engine import _view as v            # late: module helper
    return v(scope, result)


def _json_args(raw):
    from .engine import _json_args as j       # late: module helper
    return j(raw)


def plan_rows(engine, result, call_id_to_tool, min_prune_chars, rest, kw, scope) -> None:
    """Build the compact-mode ``PlanInput`` for this pass and store the engine's per-row actions."""
    settings = scope.settings or engine._settings
    scope.plan_entries, scope.plan_actions, scope.plan_rendered = {}, {}, {}
    scope.plan_blocked = 0
    scope.plan_room = engine._retained_budget()
    scope.err_room = engine._retained_error_budget()   # the errors rung still gets its own budget
    view = _view(scope, result)
    boundary = scope.boundary if scope.boundary is not None else len(result)
    parent = super(type(engine), engine)._demote_tool_result_at
    supersede = build_index(view, call_id_to_tool) if settings.supersession else None
    fact_cache: dict[int, frozenset] = {}

    def facts_of(i: int) -> frozenset:
        found = fact_cache.get(i)
        if found is None:
            row_content = result[i].get("content")
            inner = parse_payload(row_content).inner if isinstance(row_content, str) else ""
            found = frozenset((k, v) for _x, k, v, _ln in _protected_facts(inner))
            fact_cache[i] = found
        return found

    items: list[PlanItem] = []
    demote_chars = 0
    for idx, msg in enumerate(result):
        content = msg.get("content")
        chars = len(content) if isinstance(content, str) else 0
        tokens = max(1, message_budget_tokens([msg])) if chars else 0
        base = dict(id=str(idx), position=idx, tokens=tokens, chars=chars,
                    age_steps=max(0, len(result) - 1 - idx), stratum="hermes")
        keep = PlanItem(stock_action="keep", stock_kept_chars=chars, protected=True, **base)
        if msg.get("role") != "tool" or not isinstance(content, str) or idx >= boundary:
            items.append(keep)
            continue
        tool, raw_args = call_id_to_tool.get(msg.get("tool_call_id") or "", ("unknown", ""))
        if (
            (not rest and not scope.full and tool in engine._exempt)
            or engine._known_render(msg) is not None
            or classify_render(content) is not None
        ):
            items.append(keep)
            continue
        shadow = [dict(msg)]
        if not parent(shadow, 0, call_id_to_tool, min_prune_chars, *rest, **kw):
            items.append(keep)
            continue
        shadow_row = shadow[0]
        line = shadow_row.get("content")
        args = _json_args(raw_args)
        spill = _spill_note(content)
        if (
            not isinstance(line, str) or tool in engine._exempt
            or refused_summary(tool, args, content) is not None
            or SKILL_PRUNED_MARKER_PREFIX in content
            or len(content) < settings.min_tool_chars
            or (not spill and is_persisted_block(content))
        ):
            items.append(keep)
            continue
        if scope.budget < len(content):
            engine._telemetry.count("budget_skip", tool=tool)
            items.append(keep)
            continue
        scope.budget -= len(content)

        pl = parse_payload(content)
        line = engine._terminal_line(line, pl)
        ref = make_ref(msg, content)
        sel_query = build_selector_query(view, idx, focus=scope.focus, skip_user=_is_scaffolding)
        focus = " ".join(scope.focus.split()) if isinstance(scope.focus, str) else ""
        query = [
            part for part in usable_query(
                build_query(view, idx, focus=scope.focus, cache=scope.query_cache), pl.inner,
            )
            if part != tool or part == focus
        ]
        candidate = rendered = None
        if not spill:
            hint = content_hint(tool, args, pl.inner)
            out = engine._compress(pl.inner, query, hint, None, selector_query=sel_query)
            res = scope.compress_results.get(_digest(pl.inner))
            if out is not None and res is not None:
                rendered = engine._body_row(line, pl, ref, "extract", out.text)
                fp_before = error_fingerprints(pl.inner, status_rows=False)
                fp_after = error_fingerprints(out.text, status_rows=False)
                candidate = Candidate(
                    compressed_chars=len(rendered),
                    tokens_saved=max(1, (len(pl.inner) - len(out.text)) // 4),
                    confidence=getattr(res, "confidence", None),
                    fail_open=out.fail_open,
                    risk=out.risk or "high",
                    fingerprints_ok=set(fp_before) <= set(fp_after),
                    fact_lines_dropped=getattr(res, "fact_lines_dropped", 0),
                )
        superseded_by_id, succ_idx, succ_kind, covered = None, None, None, False
        if supersede is not None:
            hit = superseded_by(supersede, idx)
            if hit is not None:
                superseded_by_id, succ_idx, succ_kind = str(hit[0]), hit[0], hit[1]
                covered = facts_of(idx) <= facts_of(hit[0])
                if not covered:
                    scope.plan_blocked += 1
        scope.plan_entries[idx] = {
            "shadow_row": shadow_row, "line": line, "pl": pl, "ref": ref,
            "query": query, "tool": tool, "spill": spill,
            "succ_idx": succ_idx, "succ_kind": succ_kind,
        }
        if rendered is not None:
            scope.plan_rendered[idx] = rendered
        demote_chars += chars
        items.append(PlanItem(
            stock_action="demote",
            stock_kept_chars=len(line) + 1,
            candidate=candidate,
            fact_lines=len(facts_of(idx)),
            superseded_by=superseded_by_id,
            facts_covered_by_successor=covered,
            **base,
        ))

    ceiling = settings.max_risk if settings.max_risk in RISK_CEILINGS else "medium"
    trigger = engine._policy_threshold_tokens()
    body_tokens = message_budget_tokens(result)
    step = step_tokens(engine)
    projected = max(0, body_tokens - max(0, demote_chars - scope.plan_room) // 4)
    n_eff = min(200.0, max(0.0, (trigger - projected) / step)) if trigger else 0.0
    steps_since = (
        None if getattr(engine, "_plan_last_act", None) is None
        else max(0, getattr(engine, "_responses", 0) - engine._plan_last_act)
    )
    try:
        policy = dataclass_replace(HERMES_POLICY, max_risk=ceiling, ev=EVPolicy(
            price_profile=settings.price_profile,
            loss_profile=PLAN_LOSS_PROFILE,
            strength=ev_strength(settings),
        ))
        scope.plan_input = PlanInput(
            event="trigger", host_mode="compact",
            prompt_tokens=body_tokens, trigger_tokens=trigger,
            items=tuple(items),
            cache=CacheState(warm=False, n_eff=n_eff),
            policy=policy,
            steps_since_last_act=steps_since,
            K=PLAN_K, H=PLAN_H,
            steps_to_trigger=(max(0, int((trigger - body_tokens) / step)) if trigger else None),
            retained_budget_chars=scope.plan_room,
        )
        scope.plan_obj = engine_plan(scope.plan_input)
        scope.plan_actions = {int(a.id): a for a in scope.plan_obj.actions}
        engine._telemetry.count(
            "plan", acted=scope.plan_obj.acted, gate=scope.plan_obj.gate,
            blocked=scope.plan_blocked, items=len(items),
        )
        engine._telemetry.count("plan_supersession_blocked", scope.plan_blocked)
    except Exception:
        engine._telemetry.count("error", where="plan")
        scope.plan_entries = {}          # the plan failed: every row below keeps its parent line


def apply_plan_row(engine, result, idx, entry, scope, settings) -> bool:
    """Render the rung ``plan`` chose for ``idx`` through the existing ladder mechanics."""
    msg = result[idx]
    content = msg.get("content")
    tool = entry["tool"]
    action = scope.plan_actions.get(idx)
    rung = getattr(action, "action", None) or "header"
    if rung == "keep":
        return engine._keep_parent(result, idx, entry["shadow_row"], tool)
    line, pl, ref, query = entry["line"], entry["pl"], entry["ref"], entry["query"]
    if entry["spill"]:
        return engine._commit(
            result, idx, content, tool, ref, pl, scope, "header",
            engine._header_row(line, pl, ref, "header", entry["spill"]),
        )
    if scope.degrade:
        return engine._commit_header_or_errors(result, idx, content, tool, ref, pl, scope, line)
    if rung == "extract" and idx in scope.plan_rendered:
        return engine._commit(
            result, idx, content, tool, ref, pl, scope, "extract", scope.plan_rendered[idx])
    if rung == "brief":
        brief = engine._brief_row(
            line, pl, ref, query, min(settings.max_extract_chars, scope.plan_room))
        if brief is not None:
            scope.plan_room = max(0, scope.plan_room - len(brief))
            return engine._commit(result, idx, content, tool, ref, pl, scope, "brief", brief)
        return engine._commit_header_or_errors(result, idx, content, tool, ref, pl, scope, line)
    if rung == "superseded":
        return engine._commit(
            result, idx, content, tool, ref, pl, scope, "superseded",
            engine._header_row(line, pl, ref, "superseded",
                               f"superseded by msg {entry['succ_idx']} ({entry['succ_kind']})"),
        )
    return engine._commit_header_or_errors(result, idx, content, tool, ref, pl, scope, line)
