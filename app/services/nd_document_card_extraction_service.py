from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.builder.llm import parse_json_content
from app.agents.nd_control_agent import config
from app.agents.nd_control_agent.document_context import DocumentContext, DocumentContextChunk
from app.agents.nd_control_agent.extraction_merge import merge_document_extraction_results
from app.agents.nd_control_agent.knowledge_base_access_schemas import (
    KnowledgeBaseDocumentMetadata,
    KnowledgeBaseDocumentTextResult,
)
from app.agents.nd_control_agent.knowledge_base_access_service import (
    KnowledgeBaseAccessService,
    KnowledgeBaseAccessServiceError,
)
from app.agents.nd_control_agent.prompts.nd_document_extraction_prompt import (
    ND_DOCUMENT_EXTRACTION_SYSTEM_PROMPT,
    build_chunk_extraction_user_prompt,
    build_full_text_extraction_user_prompt,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.gateway import llm_gateway
from app.models.enums import (
    ConfidenceLevel,
    KnowledgeBaseSourceStatus,
    NdExtractionStatus,
    NdGraphEntityType,
    NdRelationExtractionType,
    NdRelationType,
    NdStructuralDocumentStatus,
    NdStructuralDocumentType,
)
from app.models.nd_control_structural import DocumentCard, NdRelation, ProcessCard
from app.services.nd_relation_display_mapper import (
    EVIDENCE_REQUIRED_TYPES,
    evidence_has_content,
    format_document_name_parts,
)
from app.schemas.nd_document_extraction import (
    DocumentExtractionResult,
    Evidence,
    FormExtraction,
    ProcessExtraction,
    ResponsibilityExtraction,
    parse_document_extraction_result,
)
from app.utils.smk_document_classification import (
    LEGACY_DOCUMENT_TYPE_VALUES,
    sync_document_card_level,
)

logger = get_logger(__name__)

LLMChatFn = Callable[..., Awaitable[dict[str, Any]]]


class NdDocumentCardExtractionServiceError(Exception):
    pass


class NdDocumentCardExtractionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        kb_access: KnowledgeBaseAccessService | None = None,
        llm_chat: LLMChatFn | None = None,
        llm_model: str | None = None,
    ) -> None:
        self.db = db
        self.kb_access = kb_access or KnowledgeBaseAccessService(db)
        self._llm_chat = llm_chat or llm_gateway.chat
        self._llm_model = llm_model or settings.ND_CONTROL_EXTRACTION_MODEL or config.EXTRACTION_LLM_MODEL

    async def build_document_context(self, document_id: str) -> DocumentContext:
        logger.info("nd_control.extraction.build_context", document_id=document_id)
        warnings: list[str] = []
        text_result = await self.kb_access.get_document_text(document_id)
        full_text = text_result.text.strip() if text_result.text else ""

        if full_text and len(full_text) <= config.ND_EXTRACTION_SINGLE_CALL_MAX_CHARS:
            return DocumentContext(
                mode="full_text",
                full_text=full_text,
                chunks=[],
                total_chars=len(full_text),
                total_chunks=0,
                warnings=warnings,
            )

        if full_text and len(full_text) > config.ND_EXTRACTION_FULL_TEXT_MAX_CHARS:
            warnings.append("full_text_exceeds_limit")

        try:
            chunk_items = await self.kb_access.get_document_chunks(document_id)
        except KnowledgeBaseAccessServiceError as exc:
            if exc.code == "chunks_not_found" and full_text:
                warnings.append("chunks_not_found_using_text_split")
                chunk_items = []
            else:
                if not full_text:
                    warnings.append(str(exc.message))
                chunk_items = []

        context_chunks = [
            DocumentContextChunk(
                chunk_id=str(chunk.chunk_id),
                text=chunk.text,
                page_number=chunk.page_number,
                section=chunk.section,
                chunk_index=chunk.metadata.get("chunk_index"),
            )
            for chunk in chunk_items
            if chunk.text.strip()
        ]

        if context_chunks:
            total_chars = sum(len(chunk.text) for chunk in context_chunks)
            if (
                len(context_chunks) > 8
                or total_chars > config.ND_EXTRACTION_SINGLE_CALL_MAX_CHARS
            ):
                warnings.append("kb_chunks_merged_for_extraction")
                merged_text = "\n\n".join(chunk.text for chunk in context_chunks)
                merged_chunks = _split_text_into_chunks(merged_text, config.ND_EXTRACTION_CHUNK_MAX_CHARS)
                return DocumentContext(
                    mode="chunked",
                    full_text=None,
                    chunks=merged_chunks,
                    total_chars=len(merged_text),
                    total_chunks=len(merged_chunks),
                    warnings=warnings,
                )
            return DocumentContext(
                mode="chunked",
                full_text=None,
                chunks=context_chunks,
                total_chars=total_chars,
                total_chunks=len(context_chunks),
                warnings=warnings,
            )

        if full_text:
            synthetic_chunks = _split_text_into_chunks(full_text, config.ND_EXTRACTION_CHUNK_MAX_CHARS)
            warnings.append("synthetic_text_chunks")
            return DocumentContext(
                mode="chunked",
                full_text=None,
                chunks=synthetic_chunks,
                total_chars=len(full_text),
                total_chunks=len(synthetic_chunks),
                warnings=warnings,
            )

        return DocumentContext(
            mode="full_text",
            full_text="",
            chunks=[],
            total_chars=0,
            total_chunks=0,
            warnings=[*(warnings or []), text_result.message or "empty_text"],
        )

    async def extract_document_card(self, document_id: str) -> DocumentCard:
        logger.info("nd_control.extraction.start", document_id=document_id, model=self._llm_model)
        metadata = await self.kb_access.get_document_metadata(document_id)
        context = await self.build_document_context(document_id)
        card = await self._get_or_create_document_card(metadata)

        card.kb_parse_status = _parse_kb_status(metadata.parse_status)
        if metadata.file_name:
            card.file_name = metadata.file_name

        if context.total_chars == 0 and not context.chunks:
            return await self._mark_empty_text(card, context)

        card.extraction_status = NdExtractionStatus.PROCESSING
        card.raw_extracted_json = {
            "context_mode": context.mode,
            "context_warnings": context.warnings,
        }
        await self.db.flush()

        try:
            extraction = await self._run_extraction(metadata, context)
        except NdDocumentCardExtractionServiceError as exc:
            logger.warning("nd_control.extraction.failed", document_id=document_id, error=str(exc))
            card.extraction_status = NdExtractionStatus.FAILED
            card.raw_extracted_json = {
                "error": str(exc),
                "context_mode": context.mode,
                "context_warnings": context.warnings,
            }
            await self.db.flush()
            return card

        await self._apply_extraction_to_card(card, metadata, extraction, context)
        process_cards = await self._upsert_process_cards(extraction, metadata.document_id)
        await self._create_relations(
            extraction=extraction,
            document_id=metadata.document_id,
            document_code=extraction.document.document_code or card.document_code,
            document_title=extraction.document.title or card.title or card.file_name,
            process_cards=process_cards,
        )
        await self.db.flush()
        logger.info(
            "nd_control.extraction.completed",
            document_id=document_id,
            card_id=str(card.id),
            processes=len(process_cards),
        )
        return card

    async def _run_extraction(
        self,
        metadata: KnowledgeBaseDocumentMetadata,
        context: DocumentContext,
    ) -> DocumentExtractionResult:
        document_code = _metadata_document_code(metadata)
        if context.mode == "full_text":
            assert context.full_text is not None
            raw = await self._call_llm(
                user_prompt=build_full_text_extraction_user_prompt(
                    document_code=document_code,
                    file_name=metadata.file_name,
                    document_text=context.full_text,
                )
            )
            return self._parse_llm_response(raw)

        partials: list[DocumentExtractionResult] = []
        for index, chunk in enumerate(context.chunks):
            raw = await self._call_llm(
                user_prompt=build_chunk_extraction_user_prompt(
                    document_code=document_code,
                    file_name=metadata.file_name,
                    chunk_index=index,
                    total_chunks=context.total_chunks,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    chunk_text=chunk.text,
                )
            )
            partial = self._parse_llm_response(raw)
            partials.append(_enrich_partial_result(partial, metadata.document_id, chunk))

        merged = merge_document_extraction_results(partials)
        return _enrich_partial_result(merged, metadata.document_id, chunk=None)

    async def _call_llm(self, *, user_prompt: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._llm_chat(
                    [
                        {"role": "system", "content": ND_DOCUMENT_EXTRACTION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    model=self._llm_model,
                    temperature=0.1,
                    max_tokens=8000,
                    timeout=settings.ND_CONTROL_EXTRACTION_LLM_TIMEOUT_SECONDS,
                )
                break
            except Exception as exc:
                last_exc = exc
                detail = str(exc).strip() or repr(exc)
                is_timeout = "timeout" in detail.lower() or type(exc).__name__ in {"ReadTimeout", "TimeoutException"}
                if attempt == 0 and is_timeout:
                    logger.warning("nd_control.extraction.llm_timeout_retry", error=detail)
                    continue
                raise NdDocumentCardExtractionServiceError(
                    f"Ошибка вызова LLM ({type(exc).__name__}): {detail}"
                ) from exc
        else:
            raise NdDocumentCardExtractionServiceError(
                f"Ошибка вызова LLM ({type(last_exc).__name__}): {last_exc}"
            ) from last_exc

        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or message.get("reasoning") or message.get("reasoning_content") or ""
        if not content.strip():
            raise NdDocumentCardExtractionServiceError("LLM вернул пустой ответ")
        return content

    def _parse_llm_response(self, content: str) -> DocumentExtractionResult:
        try:
            payload = parse_json_content(content)
        except json.JSONDecodeError as exc:
            raise NdDocumentCardExtractionServiceError(f"LLM вернул невалидный JSON: {exc}") from exc
        try:
            return parse_document_extraction_result(payload)
        except ValidationError as exc:
            raise NdDocumentCardExtractionServiceError(f"JSON не прошёл валидацию DocumentExtractionResult: {exc}") from exc

    async def _get_or_create_document_card(self, metadata: KnowledgeBaseDocumentMetadata) -> DocumentCard:
        result = await self.db.execute(
            select(DocumentCard).where(DocumentCard.document_id == metadata.document_id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        if metadata.knowledge_base_id is None:
            raise NdDocumentCardExtractionServiceError(
                "Документ не привязан к базе знаний, невозможно создать DocumentCard"
            )

        card = DocumentCard(
            document_id=metadata.document_id,
            knowledge_base_id=metadata.knowledge_base_id,
            file_name=metadata.file_name,
            title=metadata.title,
            extraction_status=NdExtractionStatus.PENDING,
        )
        self.db.add(card)
        await self.db.flush()
        return card

    async def _mark_empty_text(self, card: DocumentCard, context: DocumentContext) -> DocumentCard:
        card.extraction_status = NdExtractionStatus.NEEDS_REVIEW
        card.raw_extracted_json = {
            "error": "empty_text",
            "message": "Текст документа недоступен для структурного извлечения",
            "context_warnings": context.warnings,
        }
        await self.db.flush()
        logger.warning("nd_control.extraction.empty_text", document_id=str(card.document_id))
        return card

    async def _apply_extraction_to_card(
        self,
        card: DocumentCard,
        metadata: KnowledgeBaseDocumentMetadata,
        extraction: DocumentExtractionResult,
        context: DocumentContext,
    ) -> None:
        doc = extraction.document
        card.document_code = doc.document_code or card.document_code
        card.title = doc.title or card.title or metadata.title
        card.document_type = _map_document_type(doc.document_type)
        card.document_type_confidence = doc.document_type_confidence
        sync_document_card_level(card)
        card.version = doc.version or card.version
        card.status = _map_document_status(doc.status)
        card.approval_date = _parse_date(doc.approval_date)
        card.effective_date = _parse_date(doc.effective_date)
        card.purpose = doc.purpose
        card.scope_text = doc.scope.text
        card.extraction_status = (
            NdExtractionStatus.NEEDS_REVIEW if extraction.unknowns else NdExtractionStatus.COMPLETED
        )
        card.extraction_confidence = _estimate_confidence(extraction)
        card.raw_extracted_json = {
            **extraction.model_dump(mode="json"),
            "context_mode": context.mode,
            "context_warnings": context.warnings,
            "total_chunks_processed": context.total_chunks,
        }

    async def _upsert_process_cards(
        self,
        extraction: DocumentExtractionResult,
        document_id: uuid.UUID,
    ) -> dict[str, ProcessCard]:
        indexed: dict[str, ProcessCard] = {}
        for process in extraction.processes:
            card = await self._upsert_process_card(process, document_id)
            indexed[_norm(process.name)] = card
        return indexed

    async def _upsert_process_card(self, process: ProcessExtraction, document_id: uuid.UUID) -> ProcessCard:
        result = await self.db.execute(
            select(ProcessCard).where(ProcessCard.canonical_name == process.name)
        )
        existing = result.scalar_one_or_none()
        owner = process.owner_candidates[0] if process.owner_candidates else None
        doc_id_str = str(document_id)

        if existing is not None:
            source_ids = _merge_string_lists(existing.source_document_ids or [], [doc_id_str])
            existing.source_document_ids = source_ids
            existing.description = existing.description or process.description
            existing.goal = existing.goal or process.goal
            existing.alternative_names = _merge_string_lists(existing.alternative_names or [], [])
            existing.inputs_json = _merge_string_lists(existing.inputs_json or [], process.inputs)
            existing.outputs_json = _merge_string_lists(existing.outputs_json or [], process.outputs)
            existing.actions_json = [action.model_dump(mode="json") for action in process.actions] or existing.actions_json
            existing.roles_json = _merge_string_lists(existing.roles_json or [], process.roles)
            existing.forms_json = _merge_string_lists(existing.forms_json or [], process.forms)
            existing.systems_json = _merge_string_lists(existing.systems_json or [], process.systems)
            existing.resources_json = _merge_string_lists(existing.resources_json or [], process.resources)
            if owner and not existing.owner_candidate:
                existing.owner_candidate = owner.name_or_role
                existing.owner_confidence = owner.confidence
            return existing

        card = ProcessCard(
            canonical_name=process.name,
            alternative_names=[],
            description=process.description,
            goal=process.goal,
            owner_candidate=owner.name_or_role if owner else None,
            owner_confidence=owner.confidence if owner else None,
            source_document_ids=[doc_id_str],
            inputs_json=process.inputs,
            outputs_json=process.outputs,
            actions_json=[action.model_dump(mode="json") for action in process.actions],
            roles_json=process.roles,
            forms_json=process.forms,
            systems_json=process.systems,
            resources_json=process.resources,
        )
        self.db.add(card)
        await self.db.flush()
        return card

    async def _create_relations(
        self,
        *,
        extraction: DocumentExtractionResult,
        document_id: uuid.UUID,
        document_code: str | None,
        document_title: str | None,
        process_cards: dict[str, ProcessCard],
    ) -> None:
        document_name = format_document_name_parts(document_code, document_title)
        for department in extraction.related_departments:
            await self._add_relation_if_missing(
                source_type=NdGraphEntityType.DOCUMENT,
                source_id=document_id,
                source_name=document_name,
                relation_type=NdRelationType.DOCUMENT_MENTIONS_DEPARTMENT,
                target_type=NdGraphEntityType.DEPARTMENT,
                target_id=None,
                target_name=department,
                confidence=ConfidenceLevel.LOW,
                extraction_type=NdRelationExtractionType.INFERRED,
                evidence=[],
            )

        for process in extraction.processes:
            process_card = process_cards.get(_norm(process.name))
            process_id = process_card.id if process_card else None
            process_evidence = _collect_process_evidence(process, document_id, document_code)

            await self._add_relation_if_missing(
                source_type=NdGraphEntityType.DOCUMENT,
                source_id=document_id,
                source_name=document_name,
                relation_type=NdRelationType.DOCUMENT_REGULATES_PROCESS,
                target_type=NdGraphEntityType.PROCESS,
                target_id=process_id,
                target_name=process.name,
                confidence=_process_confidence(process),
                extraction_type=_extraction_type_from_evidence(process_evidence),
                evidence=process_evidence,
            )

            for department in process.related_departments:
                await self._add_relation_if_missing(
                    source_type=NdGraphEntityType.DOCUMENT,
                    source_id=document_id,
                    source_name=document_name,
                    relation_type=NdRelationType.DOCUMENT_MENTIONS_DEPARTMENT,
                    target_type=NdGraphEntityType.DEPARTMENT,
                    target_id=None,
                    target_name=department,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=NdRelationExtractionType.INFERRED,
                    evidence=process_evidence,
                )

            for role in process.roles:
                await self._add_relation_if_missing(
                    source_type=NdGraphEntityType.PROCESS,
                    source_id=process_id,
                    source_name=process.name,
                    relation_type=NdRelationType.PROCESS_HAS_ROLE,
                    target_type=NdGraphEntityType.ROLE,
                    target_id=None,
                    target_name=role,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=NdRelationExtractionType.INFERRED,
                    evidence=process_evidence,
                )

            for form_name in process.forms:
                await self._add_relation_if_missing(
                    source_type=NdGraphEntityType.PROCESS,
                    source_id=process_id,
                    source_name=process.name,
                    relation_type=NdRelationType.PROCESS_USES_FORM,
                    target_type=NdGraphEntityType.FORM,
                    target_id=None,
                    target_name=form_name,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=NdRelationExtractionType.INFERRED,
                    evidence=process_evidence,
                )

            for system in process.systems:
                await self._add_relation_if_missing(
                    source_type=NdGraphEntityType.PROCESS,
                    source_id=process_id,
                    source_name=process.name,
                    relation_type=NdRelationType.PROCESS_USES_SYSTEM,
                    target_type=NdGraphEntityType.SYSTEM,
                    target_id=None,
                    target_name=system,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=NdRelationExtractionType.INFERRED,
                    evidence=process_evidence,
                )

            for input_name in process.inputs:
                await self._add_relation_if_missing(
                    source_type=NdGraphEntityType.PROCESS,
                    source_id=process_id,
                    source_name=process.name,
                    relation_type=NdRelationType.PROCESS_CONSUMES_INPUT,
                    target_type=NdGraphEntityType.RESOURCE,
                    target_id=None,
                    target_name=input_name,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=NdRelationExtractionType.INFERRED,
                    evidence=process_evidence,
                )

            for output_name in process.outputs:
                await self._add_relation_if_missing(
                    source_type=NdGraphEntityType.PROCESS,
                    source_id=process_id,
                    source_name=process.name,
                    relation_type=NdRelationType.PROCESS_PRODUCES_OUTPUT,
                    target_type=NdGraphEntityType.RESOURCE,
                    target_id=None,
                    target_name=output_name,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=NdRelationExtractionType.INFERRED,
                    evidence=process_evidence,
                )

            for action in process.actions:
                if not action.performer:
                    continue
                await self._add_relation_if_missing(
                    source_type=NdGraphEntityType.ROLE,
                    source_id=None,
                    source_name=action.performer,
                    relation_type=NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION,
                    target_type=NdGraphEntityType.PROCESS,
                    target_id=process_id,
                    target_name=process.name,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=_extraction_type_from_evidence(_evidence_to_dicts(action.evidence)),
                    evidence=_evidence_to_dicts(action.evidence),
                )

        for form in extraction.forms:
            form_evidence = _evidence_to_dicts(form.evidence)
            related_process = form.related_process
            process_id = None
            if related_process:
                process_card = process_cards.get(_norm(related_process))
                process_id = process_card.id if process_card else None
            if related_process and process_id:
                await self._add_relation_if_missing(
                    source_type=NdGraphEntityType.PROCESS,
                    source_id=process_id,
                    source_name=related_process,
                    relation_type=NdRelationType.PROCESS_USES_FORM,
                    target_type=NdGraphEntityType.FORM,
                    target_id=None,
                    target_name=form.name,
                    confidence=ConfidenceLevel.MEDIUM,
                    extraction_type=_extraction_type_from_evidence(form_evidence),
                    evidence=form_evidence,
                )

        for responsibility in extraction.responsibilities:
            resp_evidence = _evidence_to_dicts(responsibility.evidence)
            await self._add_relation_if_missing(
                source_type=NdGraphEntityType.ROLE,
                source_id=None,
                source_name=responsibility.subject,
                relation_type=NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION,
                target_type=NdGraphEntityType.DOCUMENT,
                target_id=document_id,
                target_name=document_name,
                confidence=responsibility.confidence,
                extraction_type=_extraction_type_from_evidence(resp_evidence),
                evidence=resp_evidence,
            )

    async def _add_relation_if_missing(
        self,
        *,
        source_type: NdGraphEntityType,
        source_id: uuid.UUID | None,
        source_name: str,
        relation_type: NdRelationType,
        target_type: NdGraphEntityType,
        target_id: uuid.UUID | None,
        target_name: str,
        confidence: ConfidenceLevel,
        extraction_type: NdRelationExtractionType,
        evidence: list[dict[str, Any]],
    ) -> None:
        if relation_type in EVIDENCE_REQUIRED_TYPES and not evidence_has_content(evidence):
            if relation_type == NdRelationType.DOCUMENT_REGULATES_PROCESS:
                extraction_type = NdRelationExtractionType.UNCERTAIN
                confidence = ConfidenceLevel.LOW
            else:
                return

        if await self._relation_exists(
            source_type=source_type,
            source_id=source_id,
            source_name=source_name,
            relation_type=relation_type,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
        ):
            return

        relation = NdRelation(
            source_type=source_type,
            source_id=source_id,
            source_name=source_name,
            relation_type=relation_type,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            confidence=confidence,
            extraction_type=extraction_type,
            evidence_json=evidence or None,
            is_confirmed=False,
        )
        self.db.add(relation)

    async def _relation_exists(
        self,
        *,
        source_type: NdGraphEntityType,
        source_id: uuid.UUID | None,
        source_name: str,
        relation_type: NdRelationType,
        target_type: NdGraphEntityType,
        target_id: uuid.UUID | None,
        target_name: str,
    ) -> bool:
        stmt = select(NdRelation.id).where(
            NdRelation.source_type == source_type,
            NdRelation.relation_type == relation_type,
            NdRelation.target_type == target_type,
            NdRelation.source_name == source_name,
            NdRelation.target_name == target_name,
        )
        if source_id is not None:
            stmt = stmt.where(NdRelation.source_id == source_id)
        else:
            stmt = stmt.where(NdRelation.source_id.is_(None))
        if target_id is not None:
            stmt = stmt.where(NdRelation.target_id == target_id)
        else:
            stmt = stmt.where(NdRelation.target_id.is_(None))
        result = await self.db.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None


def _split_text_into_chunks(text: str, max_chars: int) -> list[DocumentContextChunk]:
    chunks: list[DocumentContextChunk] = []
    start = 0
    index = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split_at = text.rfind("\n\n", start, end)
            if split_at > start:
                end = split_at
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(
                DocumentContextChunk(
                    chunk_id=f"synthetic-{index}",
                    text=chunk_text,
                    chunk_index=index,
                )
            )
            index += 1
        start = end
    return chunks


def _enrich_partial_result(
    result: DocumentExtractionResult,
    document_id: uuid.UUID,
    chunk: DocumentContextChunk | None,
) -> DocumentExtractionResult:
    doc_id_str = str(document_id)
    document = result.document.model_copy(deep=True)
    if document.document_code is None:
        document.document_code = None

    def enrich_evidence(evidence: Evidence | None) -> Evidence | None:
        if evidence is None:
            if chunk is None:
                return None
            return Evidence(document_id=doc_id_str, page=chunk.page_number, section=chunk.section)
        payload = evidence.model_copy(deep=True)
        if payload.document_id is None:
            payload.document_id = doc_id_str
        if chunk is not None:
            if payload.page is None:
                payload.page = chunk.page_number
            if payload.section is None:
                payload.section = chunk.section
        return payload

    processes = []
    for process in result.processes:
        actions = [
            action.model_copy(update={"evidence": enrich_evidence(action.evidence)})
            for action in process.actions
        ]
        owner_candidates = [
            candidate.model_copy(update={"evidence": enrich_evidence(candidate.evidence)})
            for candidate in process.owner_candidates
        ]
        processes.append(process.model_copy(update={"actions": actions, "owner_candidates": owner_candidates}))

    responsibilities = [
        item.model_copy(update={"evidence": enrich_evidence(item.evidence)})
        for item in result.responsibilities
    ]
    forms = [item.model_copy(update={"evidence": enrich_evidence(item.evidence)}) for item in result.forms]

    participants = result.participants.model_copy(deep=True)
    for field_name in ("developed_by", "checked_by", "approved_by", "agreed_by"):
        items = [
            participant.model_copy(update={"evidence": enrich_evidence(participant.evidence)})
            for participant in getattr(participants, field_name)
        ]
        setattr(participants, field_name, items)

    return result.model_copy(
        update={
            "document": document,
            "participants": participants,
            "processes": processes,
            "responsibilities": responsibilities,
            "forms": forms,
        }
    )


def _collect_process_evidence(
    process: ProcessExtraction,
    document_id: uuid.UUID,
    document_code: str | None,
) -> list[dict[str, Any]]:
    evidence_items: list[dict[str, Any]] = []
    for action in process.actions:
        evidence_items.extend(_evidence_to_dicts(action.evidence))
    for candidate in process.owner_candidates:
        evidence_items.extend(_evidence_to_dicts(candidate.evidence))
    if not evidence_items:
        evidence_items.append(
            {
                "document_id": str(document_id),
                "document_code": document_code,
                "quote": process.description or process.name,
            }
        )
    return evidence_items


def _evidence_to_dicts(evidence: Evidence | None) -> list[dict[str, Any]]:
    if evidence is None:
        return []
    payload = evidence.model_dump(mode="json")
    return [payload] if any(payload.values()) else []


def _extraction_type_from_evidence(evidence: list[dict[str, Any]]) -> NdRelationExtractionType:
    if any(item.get("quote") for item in evidence):
        return NdRelationExtractionType.EXPLICIT
    if evidence:
        return NdRelationExtractionType.INFERRED
    return NdRelationExtractionType.UNCERTAIN


def _process_confidence(process: ProcessExtraction) -> ConfidenceLevel:
    if process.owner_candidates:
        return process.owner_candidates[0].confidence
    return ConfidenceLevel.MEDIUM


def _estimate_confidence(extraction: DocumentExtractionResult) -> Decimal:
    penalty = Decimal(len(extraction.unknowns)) * Decimal("0.05")
    value = max(Decimal("0.35"), Decimal("1.0") - penalty)
    return value.quantize(Decimal("0.0001"))


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _map_document_type(value: str | None) -> NdStructuralDocumentType | None:
    if not value:
        return None
    normalized = _norm(value)
    mapping = {
        "policy": NdStructuralDocumentType.POLICY,
        "POLICY": NdStructuralDocumentType.POLICY,
        "политика": NdStructuralDocumentType.POLICY,
        "regulation": NdStructuralDocumentType.REGULATION,
        "REGULATION": NdStructuralDocumentType.REGULATION,
        "положение": NdStructuralDocumentType.REGULATION,
        "process_regulation": NdStructuralDocumentType.PROCESS_REGULATION,
        "PROCESS_REGULATION": NdStructuralDocumentType.PROCESS_REGULATION,
        "process-regulation": NdStructuralDocumentType.PROCESS_REGULATION,
        "регламент": NdStructuralDocumentType.PROCESS_REGULATION,
        "sto": NdStructuralDocumentType.STO,
        "STO": NdStructuralDocumentType.STO,
        "сто": NdStructuralDocumentType.STO,
        "instruction": NdStructuralDocumentType.INSTRUCTION,
        "INSTRUCTION": NdStructuralDocumentType.INSTRUCTION,
        "инструкция": NdStructuralDocumentType.INSTRUCTION,
    }
    if normalized in mapping:
        return mapping[normalized]
    return LEGACY_DOCUMENT_TYPE_VALUES.get(normalized)


def _map_document_status(value: str | None) -> NdStructuralDocumentStatus:
    if not value:
        return NdStructuralDocumentStatus.DRAFT
    normalized = _norm(value)
    mapping = {
        "active": NdStructuralDocumentStatus.ACTIVE,
        "действующий": NdStructuralDocumentStatus.ACTIVE,
        "project": NdStructuralDocumentStatus.PROJECT,
        "проект": NdStructuralDocumentStatus.PROJECT,
        "draft": NdStructuralDocumentStatus.DRAFT,
        "archived": NdStructuralDocumentStatus.ARCHIVED,
        "архивный": NdStructuralDocumentStatus.ARCHIVED,
        "superseded": NdStructuralDocumentStatus.SUPERSEDED,
    }
    return mapping.get(normalized, NdStructuralDocumentStatus.DRAFT)


def _parse_kb_status(value: str | None) -> KnowledgeBaseSourceStatus | None:
    if not value:
        return None
    try:
        return KnowledgeBaseSourceStatus(value)
    except ValueError:
        return None


def _merge_string_lists(current: list[Any], incoming: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for value in [*current, *incoming]:
        text = str(value).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(text if isinstance(value, str) else value)
    return merged


def _metadata_document_code(metadata: KnowledgeBaseDocumentMetadata) -> str | None:
    extra = metadata.extra or {}
    nested = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else {}
    for candidate in (nested.get("code"), nested.get("document_code"), extra.get("document_code")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _norm(value: str) -> str:
    return value.strip().casefold()
