"""Состояние процесса агента сотрудника службы безопасности (раздел 6.1 ТЗ-АГТ-СБ-001).

Расширяет базовые платформенные поля (``app.platform_sdk.state.BaseAgentState``)
предметными полями роли: карточка контрагента, внутренние/внешние сведения, история,
связанность, покрытие критериев, уровень риска, вердикт и условия допуска.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from app.platform_sdk.state import merge_list


class SecurityOfficerState(TypedDict, total=False):
    correlation_id: str            # сквозной идентификатор кейса (аудит)
    tenant_id: str
    task_type: str
    requested_by: str
    counterparty: dict             # ИНН / ОГРН / наименование
    counterparty_id: str | None    # карточка 1С после идентификации
    is_new: bool
    _ambiguous: bool               # неоднозначная идентификация контрагента
    registry_data: dict | None     # внутренние сведения 1С
    external_data: dict | None     # официальные реестры + статус источника
    supply_history: dict | None    # исполнение договоров, претензии
    affiliation: dict | None       # связанность, конфликт интересов
    criteria: Annotated[list, merge_list]  # обязательные критерии + покрытие
    coverage: float                # покрытие критериев, %
    gaps: Annotated[list, merge_list]      # недостающие сведения
    risk_level: Literal["low", "medium", "high"] | None
    risk_factors: Annotated[list, merge_list]
    verdict: Literal["approve", "conditional", "reject"] | None
    conditions: Annotated[list, merge_list]  # условия допуска
    source_references: Annotated[list, merge_list]
    findings: Annotated[list, merge_list]
    data_confidence: Literal["high", "medium", "low"]
    requires_human_review: bool
    errors: Annotated[list, merge_list]
    audit: Annotated[list, merge_list]
    result: dict                   # итог emit_result (п. 4.8)
