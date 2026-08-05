"""Сборка контейнера сервисов."""

from __future__ import annotations

from dataclasses import dataclass

from agent_pochta.config import Settings, get_settings
from agent_pochta.services.document_service import DocumentService
from agent_pochta.services.http_document import HttpDocumentService
from agent_pochta.services.local_document import LocalDocumentService
from agent_pochta.services.http_integration import HttpIntegrationService
from agent_pochta.services.http_llm import ChatCompletionsLLMGateway
from agent_pochta.services.integration_service import IntegrationService, StubIntegrationService
from agent_pochta.services.odata_integration import ODataIntegrationService
from agent_pochta.services.llm_gateway import LLMGateway, StubLLMGateway
from agent_pochta.services.rag import RAGService
from agent_pochta.services.rag_qdrant import build_rag_service
from agent_pochta.services.vault import StubVaultClient, VaultClient


@dataclass
class ServiceContainer:
    llm: LLMGateway
    documents: DocumentService
    integration: IntegrationService
    rag: RAGService
    vault: VaultClient


def _build_llm(settings: Settings) -> LLMGateway:
    base_url = settings.effective_llm_base_url
    if base_url and (
        settings.effective_llm_provider in {"openai_compat", "deepseek"}
        or settings.effective_llm_api_key
    ):
        return ChatCompletionsLLMGateway(
            base_url,
            api_key=settings.effective_llm_api_key,
            model=settings.llm_default_model,
            timeout_sec=settings.llm_gateway_timeout_sec,
        )
    return StubLLMGateway()


def _build_documents(settings: Settings) -> DocumentService:
    if settings.document_service_url:
        return HttpDocumentService(settings.document_service_url)
    return LocalDocumentService(
        max_attachment_mb=settings.max_attachment_mb,
        max_extract_chars=settings.document_extract_max_chars,
    )


def _build_integration(settings: Settings) -> IntegrationService:
    mode = settings.erp_integration_mode
    if mode == "odata":
        if not settings.odata_base_url:
            raise ValueError("ERP_MODE=odata requires ODATA_BASE_URL")
        return ODataIntegrationService(
            settings.odata_base_url,
            entity=settings.odata_incoming_doc_entity,
            username=settings.odata_username,
            password=settings.odata_password,
            timeout_sec=settings.odata_timeout_sec,
            field_map_json=settings.odata_incoming_field_map,
            extra_fields_json=settings.odata_incoming_extra_fields,
            incoming_defaults_file=settings.odata_incoming_defaults_file,
            organization_keys_json=settings.odata_organization_keys,
            department_keys_json=settings.odata_department_keys,
            organization_keys_file=settings.odata_organization_keys_file,
            department_keys_file=settings.odata_department_keys_file,
            routing_rules_path=settings.odata_routing_rules_path,
            attached_file_field_map_path=settings.odata_attached_file_field_map_file,
            attach_files_enabled=settings.odata_attach_files_enabled,
            file_volume_key=settings.odata_file_volume_key,
            file_author_key=settings.odata_file_author_key,
            file_storage_mode=settings.odata_file_storage_mode,
            file_volume_root=settings.odata_file_volume_root,
            file_volume_preupload=settings.odata_file_volume_preupload,
        )
    if mode == "http":
        return HttpIntegrationService(settings.integration_service_url)
    return StubIntegrationService()


def build_container(settings: Settings | None = None) -> ServiceContainer:
    settings = settings or get_settings()
    vault = StubVaultClient()

    if settings.use_stubs:
        return ServiceContainer(
            llm=StubLLMGateway(),
            documents=_build_documents(settings),
            integration=StubIntegrationService(),
            rag=build_rag_service(settings),
            vault=vault,
        )

    return ServiceContainer(
        llm=_build_llm(settings),
        documents=_build_documents(settings),
        integration=_build_integration(settings),
        rag=build_rag_service(settings),
        vault=vault,
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
