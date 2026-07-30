"""Тесты удаления связанных бизнес-процессов 1С при mark_spam."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.schemas import ProcessingStatus
from agent_pochta.services.erp_process import (
    delete_linked_processes_for_incoming_document,
    delete_linked_processes_on_spam,
    find_linked_processes,
    incoming_document_subject_type,
)
from agent_pochta.services.integration_service import StubIntegrationService
from agent_pochta.services.odata_integration import ODataIntegrationService
from tests.test_resolve_human import _email_row, _mock_repo

DOC_REF = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
SUBJECT_TYPE = incoming_document_subject_type(DOC_ENTITY)


def test_incoming_document_subject_type_prefixes_standard_odata() -> None:
    assert incoming_document_subject_type(DOC_ENTITY) == SUBJECT_TYPE
    assert incoming_document_subject_type(SUBJECT_TYPE) == SUBJECT_TYPE


def test_find_linked_processes_filters_by_subject_type() -> None:
    client = MagicMock()
    client.fetch_filtered.return_value = [
        {
            "Ref_Key": "bp-1",
            "Number": "00-00000001",
            "Предмет": DOC_REF,
            "Предмет_Type": SUBJECT_TYPE,
            "DeletionMark": False,
        },
        {
            "Ref_Key": "bp-2",
            "Number": "00-00000002",
            "Предмет": DOC_REF,
            "Предмет_Type": "StandardODATA.Document_ЗаказПоставщику",
            "DeletionMark": False,
        },
    ]

    found = find_linked_processes(
        client,
        DOC_REF,
        document_entity=DOC_ENTITY,
        process_entities=("BusinessProcess_Задание",),
        include_tasks_fallback=False,
    )

    assert len(found) == 1
    assert found[0].ref_key == "bp-1"
    client.fetch_filtered.assert_called_once()
    assert client.fetch_filtered.call_args.kwargs["filter_expr"] == f"Предмет eq '{DOC_REF}'"


def test_find_linked_processes_skips_empty_document_ref() -> None:
    client = MagicMock()
    assert find_linked_processes(client, "", document_entity=DOC_ENTITY) == []
    client.fetch_filtered.assert_not_called()


def test_delete_linked_processes_for_incoming_document_deletes_and_resets_flag() -> None:
    integration = ODataIntegrationService(
        "http://1c.local/odata/standard.odata",
        entity=DOC_ENTITY,
    )

    def _fetch_filtered(entity: str, *, filter_expr: str, page_size: int = 500):
        if entity == "BusinessProcess_Задание":
            return [
                {
                    "Ref_Key": "bp-1",
                    "Number": "00-00000001",
                    "Предмет": DOC_REF,
                    "Предмет_Type": SUBJECT_TYPE,
                    "DeletionMark": False,
                }
            ]
        return []

    integration._client.fetch_filtered = MagicMock(side_effect=_fetch_filtered)
    integration._client.delete_entity = MagicMock()
    integration._client.patch_entity = MagicMock()

    result = delete_linked_processes_for_incoming_document(
        integration,
        document_ref_key=DOC_REF,
        document_entity=DOC_ENTITY,
    )

    assert result["ok"] is True
    assert result["skipped"] is False
    assert len(result["found"]) == 1
    assert result["deleted"] == [
        {
            "entity": "BusinessProcess_Задание",
            "ref_key": "bp-1",
            "number": "00-00000001",
            "deleted": True,
            "method": "delete",
        }
    ]
    integration._client.delete_entity.assert_called_once_with("BusinessProcess_Задание", "bp-1")
    integration._client.patch_entity.assert_called_once_with(
        DOC_ENTITY,
        DOC_REF,
        {"БизнесПроцессЗапущен": False},
    )
    assert result["process_flag_reset"] is True


def test_delete_linked_processes_falls_back_to_deletion_mark() -> None:
    integration = ODataIntegrationService(
        "http://1c.local/odata/standard.odata",
        entity=DOC_ENTITY,
    )

    def _fetch_filtered(entity: str, *, filter_expr: str, page_size: int = 500):
        if entity == "BusinessProcess_Задание":
            return [
                {
                    "Ref_Key": "bp-1",
                    "Number": "00-00000001",
                    "Предмет": DOC_REF,
                    "Предмет_Type": SUBJECT_TYPE,
                    "DeletionMark": False,
                }
            ]
        return []

    integration._client.fetch_filtered = MagicMock(side_effect=_fetch_filtered)
    integration._client.delete_entity = MagicMock(side_effect=ValueError("delete denied"))
    integration._client.patch_entity = MagicMock()

    result = delete_linked_processes_for_incoming_document(
        integration,
        document_ref_key=DOC_REF,
        document_entity=DOC_ENTITY,
    )

    assert result["deleted"][0]["deleted"] is True
    assert result["deleted"][0]["method"] == "deletion_mark"
    integration._client.patch_entity.assert_any_call(
        "BusinessProcess_Задание",
        "bp-1",
        {"DeletionMark": True},
    )


def test_delete_linked_processes_idempotent_for_already_deleted() -> None:
    integration = ODataIntegrationService(
        "http://1c.local/odata/standard.odata",
        entity=DOC_ENTITY,
    )

    def _fetch_filtered(entity: str, *, filter_expr: str, page_size: int = 500):
        if entity == "BusinessProcess_Задание":
            return [
                {
                    "Ref_Key": "bp-1",
                    "Number": "00-00000001",
                    "Предмет": DOC_REF,
                    "Предмет_Type": SUBJECT_TYPE,
                    "DeletionMark": True,
                }
            ]
        return []

    integration._client.fetch_filtered = MagicMock(side_effect=_fetch_filtered)
    integration._client.delete_entity = MagicMock()
    integration._client.patch_entity = MagicMock()

    result = delete_linked_processes_for_incoming_document(
        integration,
        document_ref_key=DOC_REF,
        document_entity=DOC_ENTITY,
    )

    assert result["deleted"][0]["skipped"] == "already_deleted"
    integration._client.delete_entity.assert_not_called()


def test_delete_linked_processes_on_spam_skips_without_document_ref() -> None:
    result = delete_linked_processes_on_spam(StubIntegrationService(), document_ref_key=None)
    assert result["skipped"] is True
    assert result["reason"] == "no_document_ref"


def test_delete_linked_processes_on_spam_skips_non_odata_integration() -> None:
    result = delete_linked_processes_on_spam(StubIntegrationService(), document_ref_key=DOC_REF)
    assert result["skipped"] is True
    assert result["reason"] == "integration_not_odata"


def test_mark_spam_endpoint_deletes_linked_erp_process() -> None:
    row = _email_row(status=ProcessingStatus.DONE.value)
    row.erp_task_id = DOC_REF
    row.erp_document_number = "АЛ00-000999"
    client = TestClient(app)

    with _mock_repo(row) as (_repo, _session):
        with patch(
            "agent_pochta.api.app.learn_from_spam_mark",
            return_value={"spam_pattern_saved": True},
        ):
            with patch(
                "agent_pochta.api.app.delete_linked_processes_on_spam",
                return_value={
                    "ok": True,
                    "skipped": False,
                    "deleted": [{"entity": "BusinessProcess_Задание", "ref_key": "bp-1", "deleted": True}],
                    "found": [{"entity": "BusinessProcess_Задание", "ref_key": "bp-1"}],
                },
            ) as delete_processes:
                response = client.post(
                    f"/api/v1/email-messages/{row.id}/resolve-human",
                    json={"decision": "mark_spam"},
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["erp_process_deleted_count"] == 1
    assert payload["erp_process"]["ok"] is True
    delete_processes.assert_called_once()
    assert delete_processes.call_args.kwargs["document_ref_key"] == DOC_REF
