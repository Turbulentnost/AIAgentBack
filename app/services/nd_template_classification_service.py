from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.llm.gateway import llm_gateway
from app.models.document import Document
from app.models.enums import (
    NdChangeJournalEventType,
    NdChangeJournalSource,
    NdTemplateClassificationStatus,
    NdTemplateType,
)
from app.models.knowledge_base import KnowledgeBaseSource
from app.models.nd_control_templates import NdControlTemplate, NdControlTemplateDocument
from app.services.nd_change_journal_service import NdChangeJournalService
from app.utils.nd_template_classification import ND_TEMPLATE_TYPE_LABELS, get_template_type_label

LLMChatFn = Callable[..., Awaitable[dict[str, Any]]]

CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.7
CLASSIFICATION_TEXT_MAX_CHARS = 12_000
CLASSIFICATION_HEADINGS_LIMIT = 20


class NdTemplateClassificationServiceError(Exception):
    pass


@dataclass(frozen=True)
class TemplateClassificationResult:
    template_type: NdTemplateType
    confidence: float
    reasoning: str
    source: str


class NdTemplateClassificationService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        kb_access: Any | None = None,
        llm_chat: LLMChatFn | None = None,
        llm_model: str | None = None,
    ) -> None:
        self.db = db
        if kb_access is None:
            from app.agents.nd_control_agent.knowledge_base_access_service import KnowledgeBaseAccessService

            kb_access = KnowledgeBaseAccessService(db)
        self.kb_access = kb_access
        self._llm_chat = llm_chat or llm_gateway.chat
        self._llm_model = (
            llm_model
            or settings.ND_TEMPLATE_CLASSIFICATION_MODEL
            or settings.ND_CONTROL_EXTRACTION_MODEL
            or "openai/gpt-oss-120b"
        )

    async def classify_template_document(
        self,
        template_document_id: uuid.UUID,
    ) -> NdControlTemplateDocument:
        link, template, document = await self._load_link_context(template_document_id)
        link.classification_status = NdTemplateClassificationStatus.PROCESSING
        await self.db.flush()

        context = await self._build_context(link, document)
        heuristic = classify_by_heuristics(context["file_name"], context["sample_text"])
        llm_result: TemplateClassificationResult | None = None
        metadata: dict[str, Any] = {
            "classification_context": {
                "file_name": context["file_name"],
                "text_chars": len(context["sample_text"]),
                "headings": context["headings"],
            },
            "warnings": [],
        }

        try:
            llm_result = await self._classify_with_llm(context)
        except Exception as exc:  # noqa: BLE001 - fallback is expected for local/offline LLM
            metadata["warnings"].append("llm_classification_failed")
            metadata["llm_error"] = str(exc)

        result = llm_result or heuristic
        if result is None:
            result = TemplateClassificationResult(
                template_type=template.template_type,
                confidence=0.0,
                reasoning="Не удалось определить тип шаблона по LLM или эвристикам.",
                source="fallback_empty",
            )

        warnings = list(metadata["warnings"])
        status = NdTemplateClassificationStatus.COMPLETED
        if result.confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD:
            status = NdTemplateClassificationStatus.NEEDS_REVIEW
            warnings.append("low_confidence")
        if result.template_type != template.template_type:
            status = NdTemplateClassificationStatus.NEEDS_REVIEW
            warnings.append("template_type_mismatch")

        link.detected_template_type = result.template_type
        link.classification_confidence = result.confidence
        link.classification_status = status
        link.classified_at = datetime.now(timezone.utc)
        link.classified_by = "system"
        link.metadata_ = {
            **(link.metadata_ or {}),
            **metadata,
            "classification_result": {
                "template_type": result.template_type.value,
                "template_type_label": get_template_type_label(result.template_type),
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "source": result.source,
                "parent_template_type": template.template_type.value,
                "parent_template_type_label": get_template_type_label(template.template_type),
            },
            "warnings": sorted(set(warnings)),
        }
        await self.db.flush()
        await self._log_classification_event(link, document)
        return link

    async def _log_classification_event(self, link: NdControlTemplateDocument, document: Document) -> None:
        await NdChangeJournalService(self.db).log_event(
            event_type=NdChangeJournalEventType.TEMPLATE_DOCUMENT_CLASSIFIED,
            actor_user_id=None,
            resource_type="nd_control_template_document",
            resource_id=link.id,
            template_id=link.template_id,
            document_id=link.document_id,
            document_name=document.title or document.original_filename,
            summary=(
                "Документ шаблона классифицирован: "
                f"{get_template_type_label(link.detected_template_type) or 'тип не определён'}"
            ),
            source=NdChangeJournalSource.SYSTEM,
            payload={
                "detected_template_type": getattr(link.detected_template_type, "value", link.detected_template_type),
                "classification_confidence": link.classification_confidence,
                "classification_status": getattr(link.classification_status, "value", link.classification_status),
                "metadata": link.metadata_,
            },
        )

    async def _load_link_context(
        self,
        template_document_id: uuid.UUID,
    ) -> tuple[NdControlTemplateDocument, NdControlTemplate, Document]:
        result = await self.db.execute(
            select(NdControlTemplateDocument, NdControlTemplate, Document)
            .join(NdControlTemplate, NdControlTemplate.id == NdControlTemplateDocument.template_id)
            .join(Document, Document.id == NdControlTemplateDocument.document_id)
            .where(NdControlTemplateDocument.id == template_document_id)
        )
        row = result.one_or_none()
        if row is None:
            raise NdTemplateClassificationServiceError("Документ шаблона не найден")
        return row

    async def _build_context(self, link: NdControlTemplateDocument, document: Document) -> dict[str, Any]:
        file_name = document.original_filename or document.title or ""
        sample_text = ""
        headings: list[str] = []
        try:
            text_result = await self.kb_access.get_document_text(str(link.document_id))
            sample_text = (text_result.text or "").strip()[:CLASSIFICATION_TEXT_MAX_CHARS]
        except Exception:
            sample_text = ""

        try:
            chunks = await self.kb_access.get_document_chunks(str(link.document_id))
            seen: set[str] = set()
            for chunk in chunks:
                if chunk.section and chunk.section not in seen:
                    headings.append(chunk.section)
                    seen.add(chunk.section)
                if len(headings) >= CLASSIFICATION_HEADINGS_LIMIT:
                    break
            if not sample_text:
                sample_text = "\n\n".join(chunk.text for chunk in chunks if chunk.text.strip())
                sample_text = sample_text[:CLASSIFICATION_TEXT_MAX_CHARS]
        except Exception:
            pass

        return {
            "file_name": file_name,
            "sample_text": sample_text,
            "headings": headings,
        }

    async def _classify_with_llm(self, context: dict[str, Any]) -> TemplateClassificationResult:
        response = await self._llm_chat(
            [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(context)},
            ],
            model=self._llm_model,
            temperature=0.0,
            max_tokens=1200,
            timeout=settings.ND_CONTROL_EXTRACTION_LLM_TIMEOUT_SECONDS,
        )
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or message.get("reasoning") or message.get("reasoning_content") or ""
        if not content.strip():
            raise NdTemplateClassificationServiceError("LLM вернул пустой ответ")
        try:
            payload = _parse_json_content(content)
        except json.JSONDecodeError as exc:
            raise NdTemplateClassificationServiceError(f"LLM вернул невалидный JSON: {exc}") from exc
        return _parse_classification_payload(payload, source="llm")


def classify_by_heuristics(file_name: str | None, text: str | None) -> TemplateClassificationResult | None:
    haystack = f"{file_name or ''}\n{text or ''}".lower()
    haystack = haystack.replace("ё", "е")
    for template_type, patterns in _HEURISTIC_PATTERNS:
        if any(pattern.search(haystack) for pattern in patterns):
            return TemplateClassificationResult(
                template_type=template_type,
                confidence=0.82,
                reasoning=f"Сработала эвристика по ключевым словам для типа {template_type.value}.",
                source="heuristics",
            )
    return None


def _parse_classification_payload(payload: dict[str, Any], *, source: str) -> TemplateClassificationResult:
    raw_type = str(payload.get("template_type") or "").strip()
    try:
        template_type = NdTemplateType(raw_type)
    except ValueError as exc:
        raise NdTemplateClassificationServiceError(f"Неизвестный template_type: {raw_type}") from exc
    confidence_raw = payload.get("confidence", 0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(payload.get("reasoning") or "").strip()
    return TemplateClassificationResult(
        template_type=template_type,
        confidence=confidence,
        reasoning=reasoning,
        source=source,
    )


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("JSON root must be object", text, 0)
    return payload


def _system_prompt() -> str:
    labels = "\n".join(f"- {item.value}: {label}" for item, label in ND_TEMPLATE_TYPE_LABELS.items())
    return (
        "Ты классифицируешь шаблоны нормативных документов СМК. "
        "Верни строго JSON без markdown: "
        '{ "template_type": "...", "confidence": 0.95, "reasoning": "..." }.\n'
        "Допустимые template_type:\n"
        f"{labels}"
    )


def _user_prompt(context: dict[str, Any]) -> str:
    headings = "\n".join(f"- {item}" for item in context.get("headings") or []) or "Не найдены"
    return (
        f"Название файла: {context.get('file_name') or 'Не указано'}\n\n"
        f"Оглавление/заголовки:\n{headings}\n\n"
        f"Первые символы текста:\n{context.get('sample_text') or 'Текст не найден'}"
    )


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


_HEURISTIC_PATTERNS: list[tuple[NdTemplateType, list[re.Pattern[str]]]] = [
    (NdTemplateType.CHANGE_NOTICE, [_rx(r"извещени[ея]\s+об\s+изменени")]),
    (NdTemplateType.DOCUMENT_INTRODUCTION_ORDER, [_rx(r"приказ\s+о\s+ввод[еа]\s+документ")]),
    (NdTemplateType.IMPLEMENTATION_PLAN, [_rx(r"план\s+внедрени")]),
    (NdTemplateType.CHANGE_REGISTRATION_SHEET, [_rx(r"лист\s+регистраци[ия]\s+изменени")]),
    (
        NdTemplateType.ISSUANCE_ACKNOWLEDGEMENT_SHEET,
        [_rx(r"лист\s+выдачи\s+и\s+ознакомлени"), _rx(r"лист\s+ознакомлени")],
    ),
    (NdTemplateType.TRAINING_PROTOCOL, [_rx(r"протокол\s+обучени")]),
    (NdTemplateType.DEPARTMENT_REGULATION, [_rx(r"положени[ея]\s+о\s+подразделени")]),
    (NdTemplateType.JOB_DESCRIPTION, [_rx(r"должностн\w+\s+инструкци")]),
    (NdTemplateType.WORK_INSTRUCTION, [_rx(r"рабоч\w+\s+инструкци")]),
    (NdTemplateType.PROCESS_PASSPORT, [_rx(r"паспорт\s+процесс")]),
    (NdTemplateType.PROCESS_REGULATION, [_rx(r"регламент"), _rx(r"рг[-\s]?\d")]),
    (NdTemplateType.POLICY, [_rx(r"политик[аи]")]),
    (NdTemplateType.REGULATION, [_rx(r"положени[ея]")]),
    (NdTemplateType.STO, [_rx(r"\bсто[-\s]?\d"), _rx(r"стандарт\s+организаци")]),
    (NdTemplateType.INSTRUCTION, [_rx(r"\bинструкци"), _rx(r"\bи[-\s]?\d")]),
]
