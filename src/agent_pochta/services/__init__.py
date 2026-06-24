"""Слой платформенных сервисов.

Каждый сервис — это абстрактный интерфейс (Protocol/ABC) + реализация.
По умолчанию собирается контейнер заглушек (`build_stub_container`),
который позволяет запускать агента без внешней инфраструктуры платформы.

Когда сервисы платформы (ТЗ-ПЛАТФ-001) станут доступны, добавляются
HTTP-адаптеры с тем же интерфейсом — узлы графа не меняются.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_pochta.config import Settings, get_settings
from agent_pochta.services.document_service import DocumentService, StubDocumentService
from agent_pochta.services.integration_service import (
    IntegrationService,
    StubIntegrationService,
)
from agent_pochta.services.llm_gateway import LLMGateway, StubLLMGateway
from agent_pochta.services.rag import RAGService, StubRAGService
from agent_pochta.services.vault import StubVaultClient, VaultClient


@dataclass
class ServiceContainer:
    """Контейнер зависимостей, прокидывается в узлы графа."""

    llm: LLMGateway
    documents: DocumentService
    integration: IntegrationService
    rag: RAGService
    vault: VaultClient


def build_container(settings: Settings | None = None) -> ServiceContainer:
    """Собирает контейнер сервисов в зависимости от режима (use_stubs)."""
    settings = settings or get_settings()
    if settings.use_stubs:
        return ServiceContainer(
            llm=StubLLMGateway(),
            documents=StubDocumentService(),
            integration=StubIntegrationService(),
            rag=StubRAGService(),
            vault=StubVaultClient(),
        )
    # TODO: реальные HTTP-адаптеры к сервисам платформы (ТЗ-ПЛАТФ-001).
    raise NotImplementedError(
        "Реальные адаптеры сервисов платформы ещё не реализованы. "
        "Установите USE_STUBS=true либо добавьте HTTP-адаптеры."
    )


__all__ = [
    "ServiceContainer",
    "build_container",
    "LLMGateway",
    "DocumentService",
    "IntegrationService",
    "RAGService",
    "VaultClient",
]
