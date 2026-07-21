"""Узлы проверок и расчётов: дубли, связывание кейса, специализация, загрузка, уверенность."""

from __future__ import annotations

from typing import Any

from app.platform_sdk import assess_confidence, call_tool, source_ref

from .. import AGENT_ID


def _cid(state: dict[str, Any]) -> str:
    return state.get("correlation_id", "")


def check_duplicates(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Поиск открытых заказов и существующих кейсов на ту же потребность (тип «правила»).

    Ищет открытый заказ поставщику на ту же номенклатуру и дату потребности и ранее
    сформированные кейсы контура №3. При выявлении дубля назначение не создаётся —
    позиция связывается с существующим кейсом (link_to_existing_case).
    """

    call_tool(AGENT_ID, "erp.read_open_orders", _cid(state))
    # Мок: дублей нет. При наличии — заполняется список duplicates и finding duplicate_demand.
    duplicates: list[dict[str, Any]] = []
    return {"duplicates": duplicates}


def link_to_existing_case(state: dict[str, Any]) -> dict[str, Any]:
    """Ф2. Связывание позиции с существующим кейсом при выявленном дубле (тип «обработка»).

    Новое назначение НЕ создаётся; в findings фиксируется наблюдение типа
    ``duplicate_demand`` (critical). Подтверждение связывания — за начальником ОМТО
    (hitl.yaml). В закупку дублирующая потребность не передаётся (K5, блокирующий).
    """

    dup = (state.get("duplicates") or [{}])[0]
    findings = [{
        "type": "duplicate_demand",
        "severity": "critical",
        "description": "На ту же номенклатуру и дату потребности существует "
                       f"открытый заказ/кейс {dup.get('order_id', 'ЗП-000377')}",
        "source": f"Заказ поставщику {dup.get('order_id', 'ЗП-000377')}; "
                  "check_duplicates + сверка с 1С",
        "recommendation": "Связать с существующим кейсом; в закупку не передавать",
    }]
    refs = [source_ref("СТО-28-020", section="п. 6.2", clause="контроль дублей",
                       version="07", accessed_at=state.get("_today", ""),
                       status="проверенный")]
    return {"findings": findings, "source_references": refs, "requires_human_review": True}


def match_specialization(state: dict[str, Any]) -> dict[str, Any]:
    """Ф3. Сопоставление номенклатурной группы и компетенции менеджера (тип «правила»).

    Сопоставляет группу позиции с матрицей специализаций менеджеров. При отсутствии
    однозначной специализации/кандидата уверенность понижается, и позиция эскалируется
    начальнику ОМТО (escalate_to_head).
    """

    group = (state.get("classification") or {}).get("item_group", "")
    candidates = [
        m for m in (state.get("managers") or [])
        if group in (m.get("specializations") or []) and m.get("available")
    ]
    refs = [source_ref("Матрица специализаций менеджеров", section="v3",
                       clause="сопоставление группы и компетенции", version="v3",
                       accessed_at=state.get("_today", ""), status="проверенный")]
    return {
        "source_references": refs,
        "classification": {**(state.get("classification") or {}),
                           "has_candidate": bool(candidates)},
    }


def balance_workload(state: dict[str, Any]) -> dict[str, Any]:
    """Ф4. Минимизация отклонения загрузки от среднего при сопоставимой специализации (≤20%).

    Тип «правила». Рассчитывает целевое назначение с минимизацией отклонения загрузки
    (K4) и формирует предложение по перераспределению нагрузки при систематическом
    перекосе (rebalance_proposal).
    """

    group = (state.get("classification") or {}).get("item_group", "")
    candidates = [
        m for m in (state.get("managers") or [])
        if group in (m.get("specializations") or []) and m.get("available")
    ] or list(state.get("managers") or [])
    # Целевой менеджер — с наименьшей загрузкой среди сопоставимых по специализации.
    target = min(candidates, key=lambda m: m.get("workload_pct", 100), default=None)

    rebalance_proposal = None
    if state.get("task_type") == "rebalance_workload":
        rebalance_proposal = {
            "kind": "workload_rebalance",
            "target_deviation_pct": 20,
            "note": "Проект перераспределения нагрузки при сопоставимой специализации",
        }
    return {"classification": {**(state.get("classification") or {}),
                               "target_manager": target},
            "rebalance_proposal": rebalance_proposal}


def assess_confidence_node(state: dict[str, Any]) -> dict[str, Any]:
    """Узел assess_confidence: расчёт data_confidence и requires_human_review (правила).

    Критическая позиция для производственного плана ограничивает уверенность уровнем
    medium (раздел 8.2 ТЗ) сверх общей платформенной логики (errors/critical-findings).
    """

    out = assess_confidence(state)
    classification = state.get("classification") or {}
    if classification.get("critical_for_plan") and out.get("data_confidence") == "high":
        out["data_confidence"] = "medium"
        out["requires_human_review"] = True
    return out
