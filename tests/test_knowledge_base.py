from __future__ import annotations

import uuid

from app.models.enums import KnowledgeBaseAccessType, KnowledgeBaseAgentAccessMode
from app.schemas.knowledge_base import KnowledgeBaseSearchHit
from app.services.knowledge_base_access_service import ACCESS_RANK, AGENT_MODE_RANK
from app.services.knowledge_base_search_service import KnowledgeBaseSearchService


def test_knowledge_base_access_rank_allows_management_to_search() -> None:
    assert ACCESS_RANK[KnowledgeBaseAccessType.MANAGE_ACCESS] > ACCESS_RANK[KnowledgeBaseAccessType.SEARCH]
    assert ACCESS_RANK[KnowledgeBaseAccessType.ADMIN] > ACCESS_RANK[KnowledgeBaseAccessType.MANAGE_SOURCES]


def test_agent_access_modes_are_ordered_by_risk() -> None:
    assert AGENT_MODE_RANK[KnowledgeBaseAgentAccessMode.SEARCH_ONLY] < AGENT_MODE_RANK[KnowledgeBaseAgentAccessMode.DECISION]
    assert AGENT_MODE_RANK[KnowledgeBaseAgentAccessMode.DECISION] < AGENT_MODE_RANK[KnowledgeBaseAgentAccessMode.AUTO_ACTION]


def test_answer_preview_uses_accessible_hit_text() -> None:
    service = KnowledgeBaseSearchService.__new__(KnowledgeBaseSearchService)
    hit = KnowledgeBaseSearchHit(
        content="  Правило: внеплановое совещание оформляется через служебную записку.  ",
        score=0.91,
        accessible=True,
        access_reason="allowed",
        knowledge_base_id=uuid.uuid4(),
    )

    assert service._answer_preview([hit]) == "Правило: внеплановое совещание оформляется через служебную записку."
