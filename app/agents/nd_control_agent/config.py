from __future__ import annotations

from app.models.enums import NdDocumentType, NdQmsLevel

ND_DOCUMENT_TYPE_LABELS: dict[NdDocumentType, str] = {
    NdDocumentType.POLICY: "Политика",
    NdDocumentType.REGULATION: "Положение",
    NdDocumentType.PROCEDURE: "Регламент",
    NdDocumentType.STO: "СТО",
    NdDocumentType.INSTRUCTION: "Инструкция",
}

ND_QMS_LEVEL_LABELS: dict[NdQmsLevel, str] = {
    NdQmsLevel.STRATEGIC: "Стратегический",
    NdQmsLevel.ORGANIZATIONAL: "Организационный",
    NdQmsLevel.PROCESS: "Процессный (алгоритм взаимодействия)",
    NdQmsLevel.TECHNICAL: "Технический (нормативный)",
    NdQmsLevel.OPERATIONAL: "Операционный (исполнительский)",
}

AGENT_ID = "nd_control_agent"
AGENT_NAME = "Агент контроля НД и внесения изменений"
AGENT_VERSION = "2.0.0"
# Модель для структурного извлечения документов (LM Studio / LLM gateway).
EXTRACTION_LLM_MODEL = "openai/gpt-oss-120b"
DEFAULT_MODEL = EXTRACTION_LLM_MODEL
HIGH_CONFIDENCE_THRESHOLD = 0.80
MEDIUM_CONFIDENCE_THRESHOLD = 0.55
# Максимальный объём текста для одного LLM-вызова (символы).
ND_EXTRACTION_FULL_TEXT_MAX_CHARS = 120_000
