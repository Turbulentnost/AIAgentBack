"""LLM Gateway — единая точка обращения к языковой модели (раздел 1.4 ТЗ).

Узлы агента НЕ обращаются к LLM напрямую — только через этот интерфейс.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent_pochta.schemas import EmailMessage, RoutingResult, SenderIdentity, SpamResult
from agent_pochta.services.llm_analyze import IncomingEmailAnalysis


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
    def summarize_ru(
        self,
        email: EmailMessage,
        combined_text: str,
        *,
        routing: RoutingResult | None = None,
        sender: SenderIdentity | None = None,
        attachments_text: str = "",
    ) -> str:
        """Узел 6 / HITL — краткий русскоязычный обзор (3–5 предложений)."""

    @abstractmethod
    def analyze_incoming(
        self,
        email: EmailMessage,
        combined_text: str,
        candidates: list[dict],
        *,
        sender: SenderIdentity | None = None,
        skip_spam_check: bool = False,
        attachments_text: str = "",
        claim: bool = False,
    ) -> IncomingEmailAnalysis:
        """Узел 5 — один LLM-вызов: спам + отдел + summary_ru."""


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

    def summarize_ru(
        self,
        email: EmailMessage,
        combined_text: str,
        *,
        routing: RoutingResult | None = None,
        sender: SenderIdentity | None = None,
        attachments_text: str = "",
    ) -> str:
        from agent_pochta.config import get_settings
        from agent_pochta.services.summary import clamp_summary

        settings = get_settings()
        sender_label = email.sender_name or email.sender_email
        attachments = len(email.attachments)
        dept = routing.department_name if routing else "профильный отдел"
        summary = (
            f"Письмо от {sender_label} ({email.sender_email}) по теме «{email.subject}». "
            f"Суть: {email.body_text[:200].strip() or 'см. текст письма'}. "
            f"Требуется обработка отделом «{dept}». "
            f"Вложений: {attachments}."
        )
        return clamp_summary(
            summary,
            max_sentences=settings.summary_max_sentences,
            max_chars=settings.summary_max_chars,
        )

    def analyze_incoming(
        self,
        email: EmailMessage,
        combined_text: str,
        candidates: list[dict],
        *,
        sender: SenderIdentity | None = None,
        skip_spam_check: bool = False,
        attachments_text: str = "",
        claim: bool = False,
    ) -> IncomingEmailAnalysis:
        from agent_pochta.schemas import RoutingResult
        from agent_pochta.routing.process_type import resolve_process_type
        from agent_pochta.routing.xml_builder import build_subject_xml_theme
        from agent_pochta.services.llm_analyze import resolve_partner_name

        if skip_spam_check:
            spam = SpamResult(
                is_spam=False,
                confidence=0.05,
                reason="Доверенный корпоративный отправитель",
                rule_hit="trusted_sender",
            )
        else:
            spam = self.classify_spam(email)

        choice = self.choose_department(combined_text, candidates)
        summary = self.summarize_ru(
            email,
            combined_text,
            routing=RoutingResult(
                department_id=choice["department_id"],
                department_name=choice["department_name"],
                confidence=choice["confidence"],
                reasoning=choice["reasoning"],
            ),
            sender=sender,
        )
        return IncomingEmailAnalysis(
            spam=spam,
            routing=RoutingResult(
                department_id=choice["department_id"],
                department_name=choice["department_name"],
                confidence=choice["confidence"],
                reasoning=choice["reasoning"],
            ),
            summary_ru=summary,
            xml_theme=build_subject_xml_theme(
                email.subject or "",
                combined_text=combined_text,
                claim=claim,
            ),
            partner_name=resolve_partner_name(
                llm_partner=None,
                rag_partner=sender.contractor.name if sender and sender.contractor else None,
                email=email,
                body_text=combined_text or email.body_text,
                summary_ru=summary,
            ),
            process_type=resolve_process_type(
                llm_process=None,
                subject=email.subject or "",
                combined_text=combined_text,
                claim=claim,
            ),
        )
