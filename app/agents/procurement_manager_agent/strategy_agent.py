"""Qwen strategy nodes for queue-level supply policy.

Roles: plan_waves / propose_supplier_policy / explain_tradeoffs.
Numbers come only from tool results; on LLM failure → deterministic fallback.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Awaitable, Callable, Mapping, Protocol

from app.agents.procurement_manager_agent.web_qwen import (
    parse_json_object,
    resolve_qwen_gateway_url,
    resolve_qwen_model,
    strip_think_blocks,
)
from app.agents.procurement_manager_agent.waves import (
    WAVE_LABELS_RU,
    bucket_urgency_waves,
    wave_mode_for_label,
)

ChatFn = Callable[..., Awaitable[dict[str, Any]]]

_STRATEGY_SYSTEM = (
    "Ты стратег товарно-поставочной политики для менеджера по закупкам. "
    "Отвечай ТОЛЬКО одним JSON-объектом без markdown. "
    "Используй только данные из user JSON (банк, аллокация, офферы, веб). "
    "Не выдумывай цены, URL, сроки и количества. "
    "Оплата, отправка заказа и запись в 1С запрещены — только черновики и рекомендации. "
    "Волны срочности: critical (срочно) → medium → late (можно дешевле/другой поставщик)."
)

_PLAN_WAVES_SCHEMA = (
    "Схема: {"
    '"waves": [{"wave_id": string, "label": "critical"|"medium"|"late", '
    '"mode": "urgent"|"economy", "case_ids": [string], "reason": string}], '
    '"rationale": string'
    "}"
)

_POLICY_SCHEMA = (
    "Схема: {"
    '"assignments": [{"case_id": string, "line_id": string, '
    '"supplier_id": string|null, "supplier_name": string|null, '
    '"wave_id": string|null, "wave_mode": "urgent"|"economy"|null, '
    '"split": boolean, "reason": string}], '
    '"shortlist_supplier_ids": [string], '
    '"reserve_cheaper_for_late": [{"nomenclature_id": string, '
    '"keep_supplier_id": string, "for_case_ids": [string], "reason": string}], '
    '"policy_text": string'
    "}"
)

_EXPLAIN_SCHEMA = (
    "Схема: {"
    '"summary": string, '
    '"wave_explanations": [{"wave_id": string, "text": string}], '
    '"tradeoffs": [string], '
    '"warnings": [string]'
    "}"
)


class SupportsChat(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


def strategy_qwen_enabled() -> bool:
    """Opt-in Qwen strategist; default off → deterministic optimize fallback."""
    raw = os.environ.get("PROCUREMENT_STRATEGY_USE_QWEN")
    if raw is not None and str(raw).strip():
        return str(raw).strip().casefold() in {"1", "true", "yes", "on"}
    try:
        from app.core.config import settings

        return bool(getattr(settings, "PROCUREMENT_STRATEGY_USE_QWEN", False))
    except Exception:
        return False


async def _chat_json(
    system: str,
    user_payload: dict[str, Any],
    *,
    chat_fn: ChatFn | SupportsChat | None = None,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    if not strategy_qwen_enabled():
        return None
    if chat_fn is None and not resolve_qwen_gateway_url():
        return None

    from app.agents.procurement_manager_agent.web_qwen import _chat_content

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, default=str),
        },
    ]
    try:
        content = await _chat_content(messages, chat_fn=chat_fn, timeout=timeout)
    except Exception:
        return None
    parsed = parse_json_object(content)
    return parsed or None


def _compact_orders(cases: list[Any]) -> list[dict[str, Any]]:
    from app.agents.procurement_manager_agent.waves import case_required_date

    rows: list[dict[str, Any]] = []
    for case in cases:
        if isinstance(case, dict):
            case_id = str(case.get("id") or case.get("case_id") or "")
            positions = case.get("positions") or []
            number = case.get("source_number") or case.get("case_number")
        else:
            case_id = str(getattr(case, "id", "") or "")
            positions = getattr(case, "positions", None) or []
            number = getattr(case, "source_number", None)
        if not case_id:
            continue
        lines = []
        for position in positions:
            if isinstance(position, dict):
                if position.get("cancelled"):
                    continue
                lines.append(
                    {
                        "line_id": str(position.get("line_id") or position.get("id") or ""),
                        "nomenclature_id": position.get("nomenclature_id"),
                        "nomenclature_name": position.get("nomenclature_name"),
                        "quantity": str(position.get("quantity") or 0),
                        "required_date": position.get("required_date"),
                    }
                )
            else:
                if getattr(position, "cancelled", False):
                    continue
                req = getattr(position, "required_date", None)
                lines.append(
                    {
                        "line_id": str(
                            getattr(position, "line_id", "") or getattr(position, "id", "") or ""
                        ),
                        "nomenclature_id": getattr(position, "nomenclature_id", None),
                        "nomenclature_name": getattr(position, "nomenclature_name", None),
                        "quantity": str(getattr(position, "quantity", 0) or 0),
                        "required_date": req.isoformat() if hasattr(req, "isoformat") else req,
                    }
                )
        req = case_required_date(case)
        rows.append(
            {
                "case_id": case_id,
                "case_number": number,
                "required_date": req.isoformat() if req else None,
                "positions": lines,
            }
        )
    return rows


def deterministic_plan_waves(
    cases: list[Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    plan = bucket_urgency_waves(cases, today=today)
    return {
        "waves": [
            {
                "wave_id": w["wave_id"],
                "label": w["label"],
                "mode": w["mode"],
                "case_ids": list(w.get("case_ids") or []),
                "reason": w.get("reason") or WAVE_LABELS_RU.get(w["label"], w["label"]),
            }
            for w in plan.get("waves") or []
        ],
        "rationale": (
            "Детерминированные бакеты по required_date: "
            "критично / средне / поздно (null в конец)."
        ),
        "case_wave": dict(plan.get("case_wave") or {}),
        "summary": dict(plan.get("summary") or {}),
        "today": plan.get("today"),
        "source": "deterministic_fallback",
        "raw_waves": plan,
    }


def _merge_qwen_waves(
    qwen: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Accept Qwen wave labels/reasons only if case_ids ⊆ known queue cases."""
    known = set(fallback.get("case_wave") or {})
    raw_waves = qwen.get("waves")
    if not isinstance(raw_waves, list) or not raw_waves:
        return fallback
    merged_waves: list[dict[str, Any]] = []
    case_wave: dict[str, str] = {}
    for index, item in enumerate(raw_waves, start=1):
        if not isinstance(item, dict):
            continue
        case_ids = [
            str(cid)
            for cid in (item.get("case_ids") or [])
            if str(cid) in known
        ]
        if not case_ids:
            continue
        label = str(item.get("label") or "late")
        if label not in {"critical", "medium", "late"}:
            label = "late"
        mode = str(item.get("mode") or wave_mode_for_label(label))  # type: ignore[arg-type]
        if mode not in {"urgent", "economy"}:
            mode = wave_mode_for_label(label)  # type: ignore[arg-type]
        wave_id = str(item.get("wave_id") or f"wave-{index}-{label}")
        for cid in case_ids:
            case_wave[cid] = wave_id
        merged_waves.append(
            {
                "wave_id": wave_id,
                "wave_index": index,
                "label": label,
                "label_ru": WAVE_LABELS_RU.get(label, label),  # type: ignore[arg-type]
                "mode": mode,
                "case_ids": case_ids,
                "reason": str(item.get("reason") or fallback.get("rationale") or ""),
            }
        )
    if not merged_waves:
        return fallback
    # Attach any known cases missing from Qwen into late wave.
    missing = [cid for cid in known if cid not in case_wave]
    if missing:
        late = next((w for w in merged_waves if w["label"] == "late"), None)
        if late is None:
            late = {
                "wave_id": f"wave-{len(merged_waves) + 1}-late",
                "wave_index": len(merged_waves) + 1,
                "label": "late",
                "label_ru": WAVE_LABELS_RU["late"],
                "mode": "economy",
                "case_ids": [],
                "reason": "Не размещены моделью — в позднюю волну",
            }
            merged_waves.append(late)
        late["case_ids"] = list(dict.fromkeys([*late["case_ids"], *missing]))
        for cid in missing:
            case_wave[cid] = late["wave_id"]
    return {
        "waves": merged_waves,
        "rationale": str(qwen.get("rationale") or fallback.get("rationale") or ""),
        "case_wave": case_wave,
        "summary": {
            "waves_count": len(merged_waves),
            "cases_count": len(case_wave),
            "critical_count": sum(1 for w in merged_waves if w["label"] == "critical"),
            "medium_count": sum(1 for w in merged_waves if w["label"] == "medium"),
            "late_count": sum(1 for w in merged_waves if w["label"] == "late"),
        },
        "today": fallback.get("today"),
        "source": "qwen",
        "raw_waves": {
            "waves": merged_waves,
            "case_wave": case_wave,
            "summary": {
                "waves_count": len(merged_waves),
                "cases_count": len(case_wave),
            },
        },
    }


async def plan_waves(
    cases: list[Any],
    *,
    today: date | None = None,
    chat_fn: ChatFn | SupportsChat | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Qwen + deterministic sort → urgency wave buckets."""
    fallback = deterministic_plan_waves(cases, today=today)
    user_payload = {
        "orders": _compact_orders(cases),
        "today": (today or date.today()).isoformat(),
        "hint": "Сгруппируй заказы в волны critical/medium/late по срочности.",
    }
    system = f"{_STRATEGY_SYSTEM} {_PLAN_WAVES_SCHEMA}"
    qwen = await _chat_json(system, user_payload, chat_fn=chat_fn, timeout=timeout)
    if not qwen:
        return fallback
    return _merge_qwen_waves(qwen, fallback)


def deterministic_supplier_policy(
    queue_plan: Mapping[str, Any] | dict[str, Any],
) -> dict[str, Any]:
    """Build policy table purely from optimize_queue_coverage output."""
    assignments: list[dict[str, Any]] = []
    shortlist: list[str] = []
    reserve: list[dict[str, Any]] = []
    for line in queue_plan.get("lines") or []:
        if not isinstance(line, dict):
            continue
        sid = line.get("recommended_supplier_id")
        if sid:
            shortlist.append(str(sid))
        parts = line.get("supplier_parts") or []
        assignments.append(
            {
                "case_id": line.get("case_id"),
                "line_id": line.get("line_id"),
                "supplier_id": sid,
                "supplier_name": (parts[0].get("supplier_name") if parts else None),
                "wave_id": line.get("wave_id"),
                "wave_mode": line.get("wave_mode"),
                "split": len(parts) > 1,
                "from_warehouse": line.get("from_warehouse"),
                "supplier_parts": parts,
                "reason": line.get("optimization_reason") or "",
            }
        )
    for row in queue_plan.get("supplier_diversity") or []:
        if not isinstance(row, dict):
            continue
        reserve.append(
            {
                "nomenclature_id": row.get("nomenclature_id"),
                "keep_supplier_id": row.get("economy_supplier_id"),
                "urgent_supplier_id": row.get("urgent_supplier_id"),
                "for_case_ids": [row.get("case_id")] if row.get("case_id") else [],
                "reason": row.get("reason")
                or "оставить на более дешёвого для позднего заказа",
            }
        )
    shortlist = list(dict.fromkeys(shortlist))
    diversity_n = len(queue_plan.get("supplier_diversity") or [])
    policy_text = (
        f"Политика поставок: {len(assignments)} позиций, "
        f"shortlist {len(shortlist)} поставщиков. "
        f"Для поздних заказов альтернатив дешевле: {diversity_n}."
    )
    return {
        "assignments": assignments,
        "shortlist_supplier_ids": shortlist,
        "reserve_cheaper_for_late": reserve,
        "policy_text": policy_text,
        "supplier_diversity": list(queue_plan.get("supplier_diversity") or []),
        "source": "deterministic_fallback",
    }


def _sanitize_policy(
    qwen: dict[str, Any],
    fallback: dict[str, Any],
    *,
    known_suppliers: set[str],
) -> dict[str, Any]:
    """Keep Qwen text/reasons; assignments must reference tool suppliers when set."""
    fb_by_key = {
        f"{a.get('case_id')}:{a.get('line_id')}": a
        for a in fallback.get("assignments") or []
        if isinstance(a, dict)
    }
    merged: list[dict[str, Any]] = []
    for item in qwen.get("assignments") or []:
        if not isinstance(item, dict):
            continue
        key = f"{item.get('case_id')}:{item.get('line_id')}"
        base = dict(fb_by_key.get(key) or {})
        sid = item.get("supplier_id")
        if sid and known_suppliers and str(sid) not in known_suppliers:
            sid = base.get("supplier_id")
        merged.append(
            {
                **base,
                "case_id": item.get("case_id") or base.get("case_id"),
                "line_id": item.get("line_id") or base.get("line_id"),
                "supplier_id": sid if sid is not None else base.get("supplier_id"),
                "supplier_name": item.get("supplier_name") or base.get("supplier_name"),
                "wave_id": item.get("wave_id") or base.get("wave_id"),
                "wave_mode": item.get("wave_mode") or base.get("wave_mode"),
                "split": bool(item.get("split", base.get("split"))),
                "reason": str(item.get("reason") or base.get("reason") or ""),
            }
        )
    if not merged:
        merged = list(fallback.get("assignments") or [])

    shortlist = [
        str(sid)
        for sid in (qwen.get("shortlist_supplier_ids") or fallback.get("shortlist_supplier_ids") or [])
        if sid and (not known_suppliers or str(sid) in known_suppliers)
    ]
    if not shortlist:
        shortlist = list(fallback.get("shortlist_supplier_ids") or [])
    shortlist = list(dict.fromkeys(shortlist))

    return {
        "assignments": merged,
        "shortlist_supplier_ids": shortlist,
        "reserve_cheaper_for_late": (
            qwen.get("reserve_cheaper_for_late")
            if isinstance(qwen.get("reserve_cheaper_for_late"), list)
            else fallback.get("reserve_cheaper_for_late")
        )
        or fallback.get("reserve_cheaper_for_late")
        or [],
        "policy_text": str(
            qwen.get("policy_text") or fallback.get("policy_text") or ""
        ),
        "supplier_diversity": list(fallback.get("supplier_diversity") or []),
        "source": "qwen",
    }


async def propose_supplier_policy(
    queue_plan: Mapping[str, Any] | dict[str, Any],
    *,
    allocation: Mapping[str, Any] | None = None,
    web_candidates: list[dict[str, Any]] | None = None,
    chat_fn: ChatFn | SupportsChat | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Qwen policy over optimize_queue_coverage; fallback = deterministic table."""
    fallback = deterministic_supplier_policy(queue_plan)
    known = {
        str(p.get("supplier_id"))
        for p in queue_plan.get("picks") or []
        if isinstance(p, dict) and p.get("supplier_id")
    }
    for item in web_candidates or []:
        if isinstance(item, dict) and item.get("supplier_id"):
            known.add(str(item["supplier_id"]))

    user_payload = {
        "queue_lines": [
            {
                "case_id": line.get("case_id"),
                "line_id": line.get("line_id"),
                "nomenclature_id": line.get("nomenclature_id"),
                "wave_mode": line.get("wave_mode"),
                "recommended_supplier_id": line.get("recommended_supplier_id"),
                "top_suppliers": (line.get("top_suppliers") or [])[:3],
                "from_warehouse": line.get("from_warehouse"),
                "supplier_remainder": line.get("supplier_remainder"),
            }
            for line in (queue_plan.get("lines") or [])
            if isinstance(line, dict)
        ],
        "supplier_diversity": queue_plan.get("supplier_diversity") or [],
        "allocation_summary": (allocation or queue_plan.get("allocation") or {}).get(
            "summary"
        ),
        "web_candidates": [
            {
                "supplier_id": item.get("supplier_id"),
                "name": item.get("name") or item.get("supplier_name"),
                "unit_price": item.get("unit_price"),
                "source": item.get("source"),
            }
            for item in (web_candidates or [])[:40]
            if isinstance(item, dict)
        ],
        "hint": (
            "Короткую политику: кого в shortlist, где сплит, "
            "где оставить дешевле для позднего заказа."
        ),
    }
    system = f"{_STRATEGY_SYSTEM} {_POLICY_SCHEMA}"
    qwen = await _chat_json(system, user_payload, chat_fn=chat_fn, timeout=timeout)
    if not qwen:
        return fallback
    return _sanitize_policy(qwen, fallback, known_suppliers=known)


def deterministic_explain(
    policy: Mapping[str, Any],
    *,
    waves: Mapping[str, Any] | None = None,
    cost_estimate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    wave_rows = []
    for wave in (waves or {}).get("waves") or []:
        if not isinstance(wave, dict):
            continue
        label = wave.get("label_ru") or wave.get("label") or ""
        wave_rows.append(
            {
                "wave_id": wave.get("wave_id"),
                "text": (
                    f"Волна {wave.get('wave_index') or '?'} ({label}): "
                    f"режим {wave.get('mode')}, заказов "
                    f"{len(wave.get('case_ids') or [])}."
                ),
            }
        )
    tradeoffs = [
        str(row.get("reason") or "альтернатива для позднего заказа")
        for row in policy.get("supplier_diversity") or policy.get("reserve_cheaper_for_late") or []
        if isinstance(row, dict)
    ]
    total = None
    if cost_estimate:
        total = cost_estimate.get("total_estimated_amount")
    summary = str(policy.get("policy_text") or "Политика поставок по волнам срочности.")
    if total:
        summary = f"{summary} Оценка сметы: {total} RUB."
    return {
        "summary": summary,
        "wave_explanations": wave_rows,
        "tradeoffs": tradeoffs,
        "warnings": [
            "Оплата и отправка в 1С запрещены агенту.",
            "Веб-поставщики в смете только после HITL approve.",
        ],
        "source": "deterministic_fallback",
    }


async def explain_tradeoffs(
    policy: Mapping[str, Any],
    *,
    waves: Mapping[str, Any] | None = None,
    cost_estimate: Mapping[str, Any] | None = None,
    queue_plan: Mapping[str, Any] | None = None,
    chat_fn: ChatFn | SupportsChat | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Human-readable policy explanation for UI."""
    fallback = deterministic_explain(policy, waves=waves, cost_estimate=cost_estimate)
    user_payload = {
        "policy": {
            "policy_text": policy.get("policy_text"),
            "shortlist_supplier_ids": policy.get("shortlist_supplier_ids"),
            "assignments_count": len(policy.get("assignments") or []),
            "reserve_cheaper_for_late": policy.get("reserve_cheaper_for_late"),
        },
        "waves": (waves or {}).get("waves"),
        "cost_estimate_total": (cost_estimate or {}).get("total_estimated_amount"),
        "diversity": (queue_plan or {}).get("supplier_diversity")
        or policy.get("supplier_diversity"),
        "hint": "Кратко объясни компромиссы срок/цена по волнам для менеджера.",
    }
    system = f"{_STRATEGY_SYSTEM} {_EXPLAIN_SCHEMA}"
    qwen = await _chat_json(system, user_payload, chat_fn=chat_fn, timeout=timeout)
    if not qwen:
        return fallback
    return {
        "summary": str(qwen.get("summary") or fallback["summary"]),
        "wave_explanations": (
            qwen.get("wave_explanations")
            if isinstance(qwen.get("wave_explanations"), list)
            else fallback["wave_explanations"]
        ),
        "tradeoffs": (
            qwen.get("tradeoffs")
            if isinstance(qwen.get("tradeoffs"), list)
            else fallback["tradeoffs"]
        ),
        "warnings": (
            qwen.get("warnings")
            if isinstance(qwen.get("warnings"), list)
            else fallback["warnings"]
        ),
        "source": "qwen",
    }


__all__ = [
    "deterministic_explain",
    "deterministic_plan_waves",
    "deterministic_supplier_policy",
    "explain_tradeoffs",
    "plan_waves",
    "propose_supplier_policy",
    "resolve_qwen_model",
    "strategy_qwen_enabled",
    "strip_think_blocks",
]
