"""LLM Gateway — единая точка обращения к языковой модели (раздел 1.4 ТЗ).

Узлы агента НЕ обращаются к LLM напрямую — только через этот интерфейс.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent_pochta.schemas import EmailMessage, SpamResult


class LLMGateway(ABC):
    """Контракт обращения к LLM для всех узлов агента."""

    @abstractmethod
    def classify_spam(self, email: EmailMessage) -> SpamResult:
        """Этап 2.2 — LLM-классификатор спама."""

    @abstractmethod
    def choose_department(
        self, email_text: str, candidates: list[dict]
    ) -> dict:
        """Узел 5 — выбор ровно одного отдела из кандидатов.

        Возвращает: {department_id, department_name, confidence, reasoning}.
        """

    @abstractmethod
    def summarize_ru(self, email: EmailMessage, combined_text: str) -> str:
        """Узел 6 — краткий русскоязычный обзор (3–5 предложений)."""


class StubLLMGateway(LLMGateway):
    """Детерминированная заглушка для автономного запуска и тестов."""

    def classify_spam(self, email: EmailMessage) -> SpamResult:
        text = f"{email.subject} {email.body_text}".lower()
        spammy = any(w in text for w in ("реклама", "акция", "семинар", "распродажа"))
        if spammy:
            return SpamResult(is_spam=True, confidence=0.97, reason="Рекламные маркеры в тексте")
        return SpamResult(is_spam=False, confidence=0.05, reason="Признаков спама не обнаружено")

    def choose_department(self, email_text: str, candidates: list[dict]) -> dict:
        if not candidates:
            return {
                "department_id": "",
                "department_name": "",
                "confidence": 0.0,
                "reasoning": "Кандидаты не найдены",
            }
        top = candidates[0]
        return {
            "department_id": top["department_id"],
            "department_name": top["department_name"],
            "confidence": 0.88,
            "reasoning": f"Совпадение по ключевым словам отдела «{top['department_name']}»",
        }

    def summarize_ru(self, email: EmailMessage, combined_text: str) -> str:
        sender = email.sender_name or email.sender_email
        return (
            f"Письмо от {sender} по теме «{email.subject}». "
            f"Отправитель обращается с запросом, требующим рассмотрения профильным отделом. "
            f"Вложений: {len(email.attachments)}. "
            f"[Заглушка LLM — реальный обзор будет сгенерирован через LLM Gateway.]"
        )
