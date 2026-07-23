from __future__ import annotations

from app.agents.common.base import BaseAgent
from app.agents.common.registry import agent_registry
from app.agents.document_analysis_agent import config
from app.agents.document_analysis_agent.schemas import DocumentAnalysisInput, DocumentAnalysisResult
from app.agents.document_analysis_agent.tools import TOOL_NAMES
from app.core.logging import get_logger
from app.models.enums import ConfidenceLevel

logger = get_logger(__name__)


@agent_registry.register
class DocumentAnalysisAgent(BaseAgent):
    agent_id = config.AGENT_ID
    name = config.AGENT_NAME
    purpose = config.AGENT_PURPOSE
    version = config.AGENT_VERSION
    allowed_tools = TOOL_NAMES

    async def run(self, payload: dict) -> DocumentAnalysisResult:
        data = DocumentAnalysisInput(**payload)
        file_names = data.file_names or [
            str(item) for item in (data.params or {}).get("file_names", [])
        ]

        logger.info(
            "document_analysis_agent.lm_studio_stub",
            task_id=data.task_id,
            file_count=len(file_names),
            document_ids=data.document_ids,
        )

        return DocumentAnalysisResult(
            agent_id=self.agent_id,
            status="completed",
            summary=(
                "Заглушка: файлы приняты, запрос на анализ через LM Studio будет выполнен "
                "после подключения."
            ),
            data_confidence=ConfidenceLevel.LOW,
            requires_human_review=True,
            analyzed_files=file_names,
            lm_studio_status="stub_sent",
            warnings=["LM Studio не подключён — используется заглушка"],
        )
