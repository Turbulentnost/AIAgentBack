"""Integration Service — создание документов и задач в 1С:ERP (узел 7, раздел 5.2 ТЗ).

ВАЖНО: прямой доступ агента к 1С ЗАПРЕЩЁН. Только через этот сервис платформы.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent_pochta.schemas import EmailMessage, RoutingResult


class IntegrationService(ABC):
    @abstractmethod
    def create_incoming_correspondence(
        self,
        email: EmailMessage,
        routing: RoutingResult,
        summary_ru: str,
        *,
        xml_document: str | None = None,
    ) -> dict:
        """Создаёт документ «Входящая корреспонденция» в 1С (OData POST).

        Задачи в Документообороте не создаются — только карточка документа.
        Возвращает: {erp_document_number, erp_document_id, erp_task_id}.
        Бросает исключение при сбое (повтор — узел 7 / Celery retry_erp).
        """


class StubIntegrationService(IntegrationService):
    """Заглушка: имитирует успешное создание документа в тест-контуре 1С."""

    _counter = 0

    def create_incoming_correspondence(
        self,
        email: EmailMessage,
        routing: RoutingResult,
        summary_ru: str,
        *,
        xml_document: str | None = None,
    ) -> dict:
        StubIntegrationService._counter += 1
        n = StubIntegrationService._counter
        # Маппинг полей 1С:ERP — см. таблицу узла 7 ТЗ.
        return {
            "erp_document_number": f"ВК-СТУБ-{n:06d}",
            "erp_task_id": f"TASK-STUB-{n:06d}",
            "fields": {
                "Дата": email.received_at.isoformat(),
                "Автор": "ИИ-агент (системная УЗ)",
                "Кому": f"{routing.department_id} — {routing.department_name}",
                "Тема": email.subject,
                "EmailОтправителя": email.sender_email,
                "Источник": "E-MAIL",
                "Содержание": summary_ru,
                "Приоритет": routing.priority.value,
                "Статус": "Передано на исполнение",
                "XMLРезультат": xml_document or "",
            },
        }
