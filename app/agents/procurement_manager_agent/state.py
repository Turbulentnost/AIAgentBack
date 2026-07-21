"""Состояние процесса агента менеджера по закупкам (раздел 6.1 ТЗ-АГТ-ЗАКУП-001).

Расширяет базовые платформенные поля (``app.platform_sdk.state.BaseAgentState``)
предметными полями роли: спецификация, поставщики, срок, альтернативы, заключения
КБ/СБ, проекты RFQ/заказа/договора/претензии, сравнительная таблица.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from app.platform_sdk.state import merge_list


class ProcurementManagerState(TypedDict, total=False):
    correlation_id: str            # сквозной идентификатор кейса (аудит)
    tenant_id: str
    task_type: str
    requested_by: str
    position_id: str
    need_date: str                 # дата потребности
    quantity: float

    spec: dict                     # валидированная спецификация
    spec_gaps: Annotated[list, merge_list]   # недостающие поля (цикл уточнения)
    suppliers: Annotated[list, merge_list]   # short-list + рейтинг + договор + история
    lead_time: dict                # прогноз срока vs дата потребности
    alternatives: Annotated[list, merge_list]  # аналог | изменение КД | др. поставщик | срок
    kd_deviation: dict | None      # заключение агента КБ/ГСПП
    security_verdict: dict | None  # заключение агента СБ
    rfq_draft: dict | None         # проект RFQ (отправляет человек)
    quotes: Annotated[list, merge_list]  # полученные КП
    comparison: dict | None        # нормализованная сравнительная таблица
    order_draft: dict | None
    contract_draft: dict | None
    claim_draft: dict | None

    source_references: Annotated[list, merge_list]
    findings: Annotated[list, merge_list]
    data_confidence: Literal["high", "medium", "low"]
    requires_human_review: bool
    errors: Annotated[list, merge_list]
    audit: Annotated[list, merge_list]
    result: dict                   # итог emit_result (п. 4.8)
