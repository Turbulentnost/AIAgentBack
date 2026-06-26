from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.models.document import Document
from app.models.enums import NdTemplateClassificationStatus, NdTemplateType
from app.models.nd_control_templates import NdControlTemplate, NdControlTemplateDocument
from app.services.nd_template_classification_service import (
    NdTemplateClassificationService,
    classify_by_heuristics,
)


def _llm_response(template_type: str, confidence: float, reasoning: str = "ok") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"template_type": "'
                        + template_type
                        + '", "confidence": '
                        + str(confidence)
                        + ', "reasoning": "'
                        + reasoning
                        + '"}'
                    )
                }
            }
        ]
    }


def _objects(parent_type: NdTemplateType = NdTemplateType.POLICY):
    template = NdControlTemplate(
        id=uuid.uuid4(),
        name="Шаблон",
        template_type=parent_type,
    )
    document = Document(
        id=uuid.uuid4(),
        title="Документ",
        original_filename="template.docx",
    )
    link = NdControlTemplateDocument(
        id=uuid.uuid4(),
        template_id=template.id,
        knowledge_base_id=uuid.uuid4(),
        knowledge_base_source_id=uuid.uuid4(),
        document_id=document.id,
        document_version_id=uuid.uuid4(),
    )
    return link, template, document


def _service(link, template, document, *, llm_chat):
    service = NdTemplateClassificationService(AsyncMock(), llm_chat=llm_chat)
    service._load_link_context = AsyncMock(return_value=(link, template, document))
    service._build_context = AsyncMock(
        return_value={
            "file_name": document.original_filename,
            "sample_text": "Политика в области качества",
            "headings": ["1. Общие положения"],
        }
    )
    service.db.flush = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_classifies_with_mock_llm_response() -> None:
    link, template, document = _objects(NdTemplateType.POLICY)
    service = _service(
        link,
        template,
        document,
        llm_chat=AsyncMock(return_value=_llm_response("policy", 0.95)),
    )

    result = await service.classify_template_document(link.id)

    assert result.detected_template_type == NdTemplateType.POLICY
    assert result.classification_confidence == 0.95
    assert result.classification_status == NdTemplateClassificationStatus.COMPLETED


def test_heuristics_fallback_detects_change_notice() -> None:
    result = classify_by_heuristics("Извещение об изменении.docx", "")

    assert result is not None
    assert result.template_type == NdTemplateType.CHANGE_NOTICE


@pytest.mark.asyncio
async def test_low_confidence_needs_review() -> None:
    link, template, document = _objects(NdTemplateType.POLICY)
    service = _service(
        link,
        template,
        document,
        llm_chat=AsyncMock(return_value=_llm_response("policy", 0.42)),
    )

    result = await service.classify_template_document(link.id)

    assert result.classification_status == NdTemplateClassificationStatus.NEEDS_REVIEW
    assert "low_confidence" in result.metadata_["warnings"]


@pytest.mark.asyncio
async def test_mismatch_with_parent_template_needs_review() -> None:
    link, template, document = _objects(NdTemplateType.POLICY)
    service = _service(
        link,
        template,
        document,
        llm_chat=AsyncMock(return_value=_llm_response("regulation", 0.96)),
    )

    result = await service.classify_template_document(link.id)

    assert result.detected_template_type == NdTemplateType.REGULATION
    assert result.classification_status == NdTemplateClassificationStatus.NEEDS_REVIEW
    assert "template_type_mismatch" in result.metadata_["warnings"]
