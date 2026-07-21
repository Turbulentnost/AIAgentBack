"""Состояние процесса агента начальника ОМТО (раздел 6.1 ТЗ-АГТ-ОМТО-НАЧ-001).

Расширяет базовые платформенные поля (``app.platform_sdk.state.BaseAgentState``)
надзорными полями роли: снимок 1С:ERP по кейсам контура, свод отклонений, карта
критичности, проекты решения/эскалации и ежедневного отчёта.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from app.platform_sdk.state import merge_list


class OmtoHeadState(TypedDict, total=False):
    correlation_id: str            # сквозной идентификатор кейса (аудит)
    case_ids: Annotated[list, merge_list]  # закупочные кейсы в области проверки
    tenant_id: str                 # арендатор / контур-владелец
    task_type: str                 # тип входной задачи
    requested_by: str              # инициатор
    period: dict                   # окно контроля {from, to}

    erp_snapshot: dict             # нормализованные данные 1С:ERP
    findings: Annotated[list, merge_list]  # выявленные отклонения
    severity_map: dict             # critical / major / minor
    source_references: Annotated[list, merge_list]  # документ, пункт, версия, дата
    data_confidence: Literal["high", "medium", "low"]
    requires_human_review: bool
    decision_card: dict | None     # проект решения / корректирующего действия
    escalation: dict | None        # проект эскалации
    daily_report: dict | None      # проект ежедневного отчёта / сводки / карточки рисков
    errors: Annotated[list, merge_list]
    audit: Annotated[list, merge_list]
    result: dict                   # итог emit_result (п. 4.8)
