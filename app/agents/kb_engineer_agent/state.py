"""Состояние процесса агента инженера КБ / ГСПП (раздел 6.1 ТЗ-АГТ-КБ-001).

Расширяет базовые платформенные поля (``app.platform_sdk.state.BaseAgentState``)
предметными полями роли: КД/ТД и её актуальность, обязательные требования, данные
аналога, таблица соответствия, покрытие критериев, риск, вердикт и условия применения.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from app.platform_sdk.state import merge_list


class KbEngineerState(TypedDict, total=False):
    correlation_id: str            # сквозной идентификатор кейса (аудит)
    tenant_id: str
    task_type: str
    requested_by: str
    position_id: str

    kd: dict | None                # КД/ТД: обозначение, версия, статус
    kd_actual: bool                # подтверждена ли актуальность
    requirements: Annotated[list, merge_list]  # обязательные характеристики + source_ref
    analog: dict | None            # характеристики предлагаемого аналога
    missing_data: Annotated[list, merge_list]  # недостающие сведения по аналогу
    comparison: Annotated[list, merge_list]    # таблица соответствия требование ↔ аналог
    coverage: float                # покрытие обязательных критериев, %
    risk: dict                     # влияние на изделие и производство
    verdict: Literal["allow", "deny", "undecided"] | None
    conditions: Annotated[list, merge_list]    # обязательные условия применения
    kd_changes: Annotated[list, merge_list]    # требуемые изменения КД

    source_references: Annotated[list, merge_list]
    findings: Annotated[list, merge_list]
    data_confidence: Literal["high", "medium", "low"]
    requires_human_review: bool
    errors: Annotated[list, merge_list]
    audit: Annotated[list, merge_list]
    result: dict                   # итог emit_result (п. 4.8)
