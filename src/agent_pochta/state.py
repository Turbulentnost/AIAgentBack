"""Состояние графа агента (LangGraph). Передаётся между узлами 1–8."""

from typing import Any

from typing_extensions import TypedDict

from agent_pochta.schemas import (
    EmailMessage,
    ErpTaskResult,
    ProcessingStatus,
    RoutingResult,
    SenderIdentity,
    SpamResult,
)


class AgentState(TypedDict, total=False):
    """Единое состояние обработки одного письма.

    `total=False` — узлы заполняют свои поля по мере прохождения графа.
    """

    # Узел 1 — вход
    email: EmailMessage

    # Узел 2 — спам
    spam: SpamResult

    # Узел 3 — отправитель
    sender: SenderIdentity

    # Узел 4 — содержимое (объединённый текст письма + вложений)
    combined_text: str
    attachments_text: str

    # Узел 5 — маршрутизация
    routing: RoutingResult

    # Узел 6 — обзор
    summary_ru: str

    # Узел 7 — задача в 1С
    erp: ErpTaskResult

    # Сквозные поля
    status: ProcessingStatus
    human_review: bool          # требуется участие человека
    escalation_reason: str      # причина эскалации (для уведомления)
    errors: list[str]           # накопленные ошибки узлов
    trace: list[str]            # порядок пройденных узлов (для отладки/логов)
    meta: dict[str, Any]        # произвольные метаданные
