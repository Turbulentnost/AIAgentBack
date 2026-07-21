"""Состояние процесса агента заместителя начальника ОМТО (раздел 6.1 ТЗ-АГТ-ОМТО-ЗАМ-001).

Расширяет базовые платформенные поля (``app.platform_sdk.state.BaseAgentState``)
предметными полями роли: очередь нераспределённых позиций, дубли потребности, профиль
менеджеров, классификация позиции, проект назначения и предложение по перераспределению.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from app.platform_sdk.state import merge_list


class OmtoDeputyState(TypedDict, total=False):
    correlation_id: str            # сквозной идентификатор кейса (аудит)
    tenant_id: str
    task_type: str
    requested_by: str
    priority_hint: str             # подсказка приоритета: critical|high|normal

    position_ids: Annotated[list, merge_list]  # позиции к распределению
    queue: Annotated[list, merge_list]         # очередь нераспределённых позиций
    duplicates: Annotated[list, merge_list]    # выявленные дубли потребности
    managers: Annotated[list, merge_list]      # профиль: специализация, загрузка
    classification: dict           # номенклатурная группа, срочность, приоритет
    assignment_draft: dict | None  # проект назначения + обоснование
    rebalance_proposal: dict | None  # проект предложения по перераспределению нагрузки

    source_references: Annotated[list, merge_list]
    findings: Annotated[list, merge_list]
    data_confidence: Literal["high", "medium", "low"]
    requires_human_review: bool
    errors: Annotated[list, merge_list]
    audit: Annotated[list, merge_list]
    result: dict                   # итог emit_result (п. 4.8)
