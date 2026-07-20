"""Тесты OData-интеграции с 1С (создание входящей корреспонденции)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent_pochta.config import Settings, reset_settings
from agent_pochta.schemas import EmailMessage, Priority, RoutingResult
from agent_pochta.services import build_container
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.odata_incoming_mapper import (
    build_department_name_lookup,
    build_incoming_document_payload,
    load_field_map,
    load_guid_map,
    resolve_department_name,
)
from agent_pochta.services.odata_integration import ODataIntegrationService

SAMPLE_XML = (
    "<document>"
    "<organization>НП</organization>"
    "<theme>Тестовая тема</theme>"
    "<направление>КС</направление>"
    "<claim>false</claim>"
    "<partner>ООО Пример</partner>"
    "<services>"
    "<service><name>00-000076</name><process>исполнение</process>"
    "<reasoning>Тест</reasoning></service>"
    "</services>"
    "<email_sender>sender@example.com</email_sender>"
    "<email_recipient>info@turbo-don.ru</email_recipient>"
    "<mail_datetime>2026-07-03 10:00:00</mail_datetime>"
    "<process>исполнение</process>"
    "<spam>false</spam>"
    "<confidence_level>ВЫСОКАЯ</confidence_level>"
    "<matching_keywords>договор</matching_keywords>"
    "<processing_notes>Автоматическая регистрация разрешена.</processing_notes>"
    "</document>"
)

ORG_KEYS = {"НП": "11111111-1111-1111-1111-111111111111"}
DEPT_KEYS = {"00-000076": "22222222-2222-2222-2222-222222222222"}


@pytest.fixture
def sample_email() -> EmailMessage:
    return EmailMessage(
        message_id="<test-001@example.com>",
        mailbox="info@turbo-don.ru",
        sender_email="sender@example.com",
        subject="Тема письма из почты",
        received_at=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_routing() -> RoutingResult:
    return RoutingResult(
        department_id="00-000076",
        department_name="Отдел тест",
        confidence=0.95,
        reasoning="Правило",
        priority=Priority.NORMAL,
    )


def test_build_incoming_document_payload_maps_required_fields(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    payload = build_incoming_document_payload(
        sample_email,
        sample_routing,
        "Краткий обзор",
        xml_document=SAMPLE_XML,
        organization_keys=ORG_KEYS,
        department_keys=DEPT_KEYS,
    )
    assert payload["ТемаСлужебнойЗаписки"] == "Тестовая тема"
    assert payload["Подразделение"] == "Отдел тест"
    assert payload["Партнер"] == "ООО Пример"
    assert payload["ПлательщикНаправление"] == "ООО Пример"
    assert payload["Направление"] == "КС"
    assert payload["Организация_Key"] == ORG_KEYS["НП"]
    assert payload["ПодразделениеИсполнитель_Key"] == DEPT_KEYS["00-000076"]
    assert payload["КомуПодразделениеСсылка_Key"] == DEPT_KEYS["00-000076"]


def test_build_incoming_document_payload_maps_optional_fields(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    payload = build_incoming_document_payload(
        sample_email,
        sample_routing,
        "Краткий обзор",
        xml_document=SAMPLE_XML,
    )
    assert payload["Содержание"] == "Краткий обзор"
    assert payload["Автор"] == "ИИ 1С"
    assert payload["Кому"] == "00-000076"
    assert payload["ДокументОснование"] == "Тема письма из почты"
    assert payload["EmailОтправителяПисьма"] == "sender@example.com"
    assert payload["EmailПолучателяПисьма"] == "info@turbo-don.ru"
    assert payload["Претензия"] is False
    assert payload["ID_XML"] == "<test-001@example.com>"
    assert payload["ИсточникПоступления"] == "E-MAIL"
    assert "XML:" in payload["Комментарий"]
    assert payload["Date"] == "2026-07-03T10:00:00"


def test_payer_defaults_to_partner_when_missing() -> None:
    xml = (
        "<document>"
        "<organization>НП</organization>"
        "<theme>Тема</theme>"
        "<partner>ООО Контрагент</partner>"
        "<направление>КС</направление>"
        "</document>"
    )
    email = EmailMessage(
        message_id="m1",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Subj",
        received_at=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )
    routing = RoutingResult(
        department_id="00-000001",
        department_name="Отдел",
        confidence=1.0,
        reasoning="",
    )
    payload = build_incoming_document_payload(email, routing, "", xml_document=xml)
    assert payload["Партнер"] == "ООО Контрагент"
    assert payload["ПлательщикНаправление"] == "ООО Контрагент"


def test_resolve_department_name_falls_back_to_rules_lookup() -> None:
    routing = RoutingResult(
        department_id="00-000054",
        department_name="",
        confidence=1.0,
        reasoning="",
    )
    names = {"00-000054": "Отдел тендерных продаж"}
    assert resolve_department_name(routing, department_names=names) == "Отдел тендерных продаж"


def test_build_department_name_lookup_collects_rule_names() -> None:
    lookup = build_department_name_lookup(
        {
            "email_keyword_rules": [
                {"keyword": "tender", "code": "00-000054", "name": "Отдел тендерных продаж"},
            ],
            "reserve_code": "00-000066",
            "reserve_name": "Управление делами",
        }
    )
    assert lookup["00-000054"] == "Отдел тендерных продаж"
    assert lookup["00-000066"] == "Управление делами"


def test_odata_client_create_entity_posts_json() -> None:
    client = ODataClient("http://1c.local/odata/standard.odata/", username="u", password="p")
    mock_response = MagicMock()
    mock_response.json.return_value = {"Ref_Key": "abc", "Number": "ВК-000001"}
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = mock_response

    with patch("agent_pochta.services.odata_client.httpx.Client", return_value=mock_http):
        result = client.create_entity("Document_ТД_ВходящаяКорреспонденция", {"Date": "2026-07-03T10:00:00"})

    assert result["Number"] == "ВК-000001"
    mock_http.post.assert_called_once()
    args, kwargs = mock_http.post.call_args
    assert args[0].endswith("Document_ТД_ВходящаяКорреспонденция?$format=json")
    assert kwargs["json"]["Date"] == "2026-07-03T10:00:00"


def test_odata_integration_service_returns_document_ids(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    service = ODataIntegrationService(
        "http://1c.local/odata/standard.odata",
        entity="Document_ТД_ВходящаяКорреспонденция",
        organization_keys_json=json.dumps(ORG_KEYS),
        department_keys_json=json.dumps(DEPT_KEYS),
    )
    with patch.object(
        service._client,
        "create_entity",
        return_value={"Ref_Key": "11111111-2222-3333-4444-555555555555", "Number": "ВК-000042"},
    ) as create_mock:
        result = service.create_incoming_correspondence(
            sample_email,
            sample_routing,
            "Обзор",
            xml_document=SAMPLE_XML,
        )

    create_mock.assert_called_once()
    payload = create_mock.call_args[0][1]
    assert payload["Организация_Key"] == ORG_KEYS["НП"]
    assert payload["Кому"] == "00-000076"
    assert result["erp_document_number"] == "ВК-000042"
    assert result["erp_document_id"] == "11111111-2222-3333-4444-555555555555"
    assert result["erp_task_id"] == "11111111-2222-3333-4444-555555555555"


def test_build_container_uses_odata_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_STUBS", "false")
    monkeypatch.setenv("ERP_MODE", "odata")
    monkeypatch.setenv("ODATA_BASE_URL", "http://1c.local/odata/standard.odata")
    monkeypatch.setenv("ODATA_USERNAME", "odata.user")
    monkeypatch.setenv("ODATA_PASSWORD", "secret")
    reset_settings()

    container = build_container(Settings())
    assert isinstance(container.integration, ODataIntegrationService)


def test_odata_client_create_entity_raises_on_http_error() -> None:
    client = ODataClient("http://1c.local/odata/standard.odata/")
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403",
        request=MagicMock(),
        response=MagicMock(status_code=403),
    )

    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = mock_response

    with patch("agent_pochta.services.odata_client.httpx.Client", return_value=mock_http):
        with pytest.raises(httpx.HTTPStatusError):
            client.create_entity("Document_ТД_ВходящаяКорреспонденция", {})


def test_load_field_map_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="ODATA_INCOMING_FIELD_MAP"):
        load_field_map("{bad json")


def test_field_map_override() -> None:
    merged = load_field_map(json.dumps({"xml_result": "ТекстHTML", "department_name": ""}))
    assert merged["xml_result"] == "ТекстHTML"
    assert merged["department_name"] == ""


def test_load_guid_map_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="ODATA_ORGANIZATION_KEYS"):
        load_guid_map("{bad", env_name="ODATA_ORGANIZATION_KEYS")
