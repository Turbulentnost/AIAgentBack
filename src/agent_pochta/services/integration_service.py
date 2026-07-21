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

    def attach_files_to_incoming_correspondence(
        self,
        *,
        document_ref_key: str,
        files: list,
    ) -> list[dict]:
        """Прикрепляет файлы к уже созданному Document_ТД_ВходящаяКорреспонденция.

        По умолчанию не реализовано (заглушка / HTTP-режим). OData — в подклассе.
        """
        raise NotImplementedError("attach_files_to_incoming_correspondence is not configured")


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
            "erp_document_id": f"11111111-1111-1111-1111-{n:012d}"[:36],
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

    def attach_files_to_incoming_correspondence(
        self,
        *,
        document_ref_key: str,
        files: list,
    ) -> list[dict]:
        StubIntegrationService._counter += 1
        base = StubIntegrationService._counter
        results: list[dict] = []
        for index, item in enumerate(files, start=1):
            filename = getattr(item, "filename", None) or f"file-{index}"
            size = len(getattr(item, "content", b"") or b"")
            results.append(
                {
                    "ref_key": f"00000000-0000-0000-0000-{base:012d}{index:04d}"[-36:],
                    "filename": filename,
                    "size_bytes": size,
                    "entity": "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
                }
            )
        return results
