"""Тесты OData-интеграции с 1С (создание входящей корреспонденции)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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
    load_guid_map_from_file,
    resolve_guid_map,
    resolve_incoming_extra_fields,
    resolve_department_name,
    resolve_odata_direction,
    resolve_organization_key,
    resolve_payer_direction,
)
from agent_pochta.routing.xml_builder import resolve_document_theme
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
    "</document>"
)


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


SAMPLE_ORG_KEYS = {
    "НП": "fbca2148-6cfd-11e7-812d-001e67112509",
    "АЛ": "fbca2146-6cfd-11e7-812d-001e67112509",
    "МГ": "fbca2145-6cfd-11e7-812d-001e67112509",
    "АМ": "b3529fec-5813-11e8-8273-ac1f6b05524d",
    "МИ": "171272c0-ef41-11e9-829c-ac1f6b05524d",
    "БМ": "fbca2148-6cfd-11e7-812d-001e67112509",
}
SAMPLE_DEPT_KEYS = {"00-000076": "bd7b5184-9f9c-11e4-80da-001e67112509"}


def test_build_incoming_document_payload_full_mapping(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    payload = build_incoming_document_payload(
        sample_email,
        sample_routing,
        "Краткий обзор",
        xml_document=SAMPLE_XML,
        organization_keys=SAMPLE_ORG_KEYS,
        department_keys=SAMPLE_DEPT_KEYS,
    )
    expected_theme = resolve_document_theme(
        sample_email,
        explicit_theme="Тестовая тема",
        combined_text=sample_email.body_text or "",
        process_type="исполнение",
        claim=False,
    )
    assert payload["Организация_Key"] == SAMPLE_ORG_KEYS["НП"]
    assert payload["ТемаСлужебнойЗаписки"] == expected_theme
    assert payload["ТемаСлужебнойЗаписки_Type"] == "Edm.String"
    assert payload["Подразделение"] == resolve_department_name(sample_routing)
    assert payload["Подразделение_Type"] == "Edm.String"
    assert payload["ПодразделениеИсполнитель_Key"] == SAMPLE_DEPT_KEYS["00-000076"]
    assert payload["КомуПодразделениеСсылка_Key"] == SAMPLE_DEPT_KEYS["00-000076"]
    assert payload["Партнер"] == "ООО Пример"
    assert payload["Партнер_Type"] == "Edm.String"
    assert payload["ПлательщикНаправление"] == resolve_payer_direction("НП", "КС")
    assert payload["ПлательщикНаправление_Type"] == "Edm.String"
    assert payload["Автор"] == "Искусственный интеллект 1С"
    assert payload["Автор_Type"] == "Edm.String"
    assert payload["Date"] == "2026-07-03T10:00:00"
    assert "ДатаИсходящая" not in payload
    assert payload["EmailОтправителяПисьма"] == "sender@example.com"
    assert payload["EmailПолучателяПисьма"] == "info@turbo-don.ru"
    assert payload["Содержание"] == "Краткий обзор"
    assert payload["Направление"] == resolve_odata_direction("00-000076", "КС")
    assert payload["Претензия"] is False
    assert payload["ИсточникПоступления"] == "EMAIL"
    assert payload["Статус"] == "Подготовлен"
    assert "ID_XML" not in payload
    assert "Комментарий" not in payload
    assert "ДокументОснование" not in payload
    assert payload["Кому"] == "00-000076"


def test_build_incoming_document_payload_merges_extra_fields(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    payload = build_incoming_document_payload(
        sample_email,
        sample_routing,
        "Обзор",
        xml_document=SAMPLE_XML,
        extra_fields={
            "Posted": False,
            "ГрифДоступа_Key": "bbdfce50-4266-11e8-8272-ac1f6b05524d",
            "Ответственный_Key": "4a3f1bd0-04f3-11e8-826d-ac1f6b05524d",
        },
    )
    assert payload["Posted"] is False
    assert payload["ГрифДоступа_Key"] == "bbdfce50-4266-11e8-8272-ac1f6b05524d"
    assert payload["Ответственный_Key"] == "4a3f1bd0-04f3-11e8-826d-ac1f6b05524d"
    assert payload["Партнер"] == "ООО Пример"
    assert payload["ПлательщикНаправление"] == resolve_payer_direction("НП", "КС")


def test_build_incoming_document_payload_requires_xml(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    with pytest.raises(ValueError, match="xml_document is required"):
        build_incoming_document_payload(sample_email, sample_routing, "Обзор")


def test_payer_direction_from_xml_not_partner() -> None:
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
    assert payload["ПлательщикНаправление"] == resolve_payer_direction("НП", "КС")
    assert payload["ПлательщикНаправление"] != "ООО Контрагент"


def test_leadership_department_forces_ks_payer_direction_despite_xml_pr() -> None:
    xml = (
        "<document>"
        "<organization>НП</organization>"
        "<theme>Тема</theme>"
        "<partner>ООО Контрагент</partner>"
        "<направление>ПР</направление>"
        "</document>"
    )
    email = EmailMessage(
        message_id="m-leadership",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Subj",
        received_at=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )
    routing = RoutingResult(
        department_id="00-000152",
        department_name="ОПЕРАЦИОННЫЙ ДИРЕКТОР",
        confidence=1.0,
        reasoning="",
    )
    payload = build_incoming_document_payload(email, routing, "", xml_document=xml)
    assert payload["ПлательщикНаправление"] == resolve_payer_direction("НП", "КС")
    assert payload["Направление"] == "ОперационныйДиректор"
    assert payload["Партнер_Type"] == "Edm.String"


def test_legal_department_forces_ks_payer_direction_despite_xml_pr() -> None:
    xml = (
        "<document>"
        "<organization>НП</organization>"
        "<theme>Тема</theme>"
        "<partner>ООО Контрагент</partner>"
        "<направление>ПР</направление>"
        "</document>"
    )
    email = EmailMessage(
        message_id="m-legal",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Subj",
        received_at=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )
    routing = RoutingResult(
        department_id="00-000044",
        department_name="Юридический отдел",
        confidence=1.0,
        reasoning="",
    )
    payload = build_incoming_document_payload(email, routing, "", xml_document=xml)
    assert payload["ПлательщикНаправление"] == resolve_payer_direction("НП", "КС")


def test_resolve_odata_direction_maps_xml_codes_to_enum() -> None:
    assert resolve_odata_direction("", "ПР") == "ДиректорПроизводства1"
    assert resolve_odata_direction("", "КС") == "ПрочиеВнутренние"
    assert resolve_odata_direction("", "СС") == "ДиректорПоСервисномуОбслуживанию"
    assert resolve_odata_direction("00-000152", "ПР") == "ОперационныйДиректор"


def test_resolve_odata_direction_unknown_xml_code_is_omitted() -> None:
    assert resolve_odata_direction("", "INVALID") == ""


def test_resolve_payer_direction_np_default_is_production() -> None:
    assert resolve_payer_direction("НП", None) == "ТурбулентностьДОНПроизводство1"
    assert resolve_payer_direction("НП", "") == "ТурбулентностьДОНПроизводство1"


def test_resolve_payer_direction_np_pr_uses_production_suffix() -> None:
    assert resolve_payer_direction("НП", "ПР") == "ТурбулентностьДОНПроизводство1"


def test_resolve_payer_direction_child_orgs() -> None:
    assert resolve_payer_direction("АЛ", "АЛ") == "АЛМАЗ"
    assert resolve_payer_direction("МГ", "МГ") == "Метрогазсервис"
    assert resolve_payer_direction("БМ", "БМ") == "БМИ"


def test_resolve_organization_key_bm_uses_npo_guid() -> None:
    npo_guid = "fbca2148-6cfd-11e7-812d-001e67112509"
    assert resolve_organization_key("БМ", SAMPLE_ORG_KEYS) == npo_guid
    assert resolve_organization_key("БМ", {"НП": npo_guid}) == npo_guid


def test_resolve_department_name_falls_back_to_rules_lookup() -> None:
    routing = RoutingResult(
        department_id="00-999998",
        department_name="",
        confidence=1.0,
        reasoning="",
    )
    names = {"00-999998": "Резервный отдел из правил"}
    assert resolve_department_name(routing, department_names=names) == "Резервный отдел из правил"


def test_build_department_name_lookup_collects_rule_names() -> None:
    from agent_pochta.services.routing_departments import resolve_department_display_name

    lookup = build_department_name_lookup(
        {
            "email_keyword_rules": [
                {"keyword": "tender", "code": "00-000054", "name": "Отдел тендерных продаж"},
            ],
            "reserve_code": "00-000066",
            "reserve_name": "Управление делами",
        }
    )
    assert lookup["00-000054"] == resolve_department_display_name("00-000054", "Отдел тендерных продаж")
    assert lookup["00-000066"] == "Управление делами"


def test_odata_client_create_entity_posts_json() -> None:
    client = ODataClient("http://1c.local/odata/standard.odata/", username="u", password="p")
    mock_response = MagicMock()
    mock_response.status_code = 200
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


def test_odata_integration_service_returns_document_ids(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    service = ODataIntegrationService(
        "http://1c.local/odata/standard.odata",
        entity="Document_ТД_ВходящаяКорреспонденция",
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
    expected_theme = resolve_document_theme(
        sample_email,
        explicit_theme="Тестовая тема",
        combined_text=sample_email.body_text or "",
        process_type="исполнение",
        claim=False,
    )
    assert payload["Автор"] == "Искусственный интеллект 1С"
    assert payload["ТемаСлужебнойЗаписки"] == expected_theme
    assert payload["Партнер"] == "ООО Пример"
    assert payload["ПлательщикНаправление"] == resolve_payer_direction("НП", "КС")
    assert result["erp_document_number"] == "ВК-000042"
    assert result["erp_document_id"] == "11111111-2222-3333-4444-555555555555"


def test_build_container_uses_odata_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_STUBS", "false")
    monkeypatch.setenv("ERP_MODE", "odata")
    monkeypatch.setenv("ODATA_BASE_URL", "http://1c.local/odata/standard.odata")
    monkeypatch.setenv("ODATA_USERNAME", "odata.user")
    monkeypatch.setenv("ODATA_PASSWORD", "secret")
    reset_settings()

    container = build_container(Settings())
    assert isinstance(container.integration, ODataIntegrationService)


def test_odata_client_create_entity_raises_on_odata_error_message() -> None:
    client = ODataClient("http://1c.local/odata/standard.odata/")
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {
        "odata.error": {"message": {"value": "Нарушение прав доступа!"}},
    }
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = mock_response

    with patch("agent_pochta.services.odata_client.httpx.Client", return_value=mock_http):
        with pytest.raises(ValueError, match="Нарушение прав доступа"):
            client.create_entity("Document_ТД_ВходящаяКорреспонденция", {})


def test_odata_client_create_entity_raises_on_http_error() -> None:
    client = ODataClient("http://1c.local/odata/standard.odata/")
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.json.side_effect = json.JSONDecodeError("err", "", 0)
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


def test_resolve_guid_map_prefers_inline_over_file(tmp_path: Path) -> None:
    file_path = tmp_path / "keys.json"
    file_path.write_text('{"00-000001": "from-file"}', encoding="utf-8")
    resolved = resolve_guid_map(
        '{"00-000001": "from-inline"}',
        file_path=str(file_path),
        env_name="ODATA_DEPARTMENT_KEYS",
    )
    assert resolved == {"00-000001": "from-inline"}


def test_resolve_guid_map_loads_file_when_inline_empty(tmp_path: Path) -> None:
    file_path = tmp_path / "keys.json"
    file_path.write_text('{"НП": "11111111-1111-1111-1111-111111111111"}', encoding="utf-8")
    resolved = resolve_guid_map("", file_path=str(file_path), env_name="ODATA_ORGANIZATION_KEYS")
    assert resolved["НП"] == "11111111-1111-1111-1111-111111111111"


def test_load_guid_map_from_file(tmp_path: Path) -> None:
    file_path = tmp_path / "org.json"
    file_path.write_text('{"АЛ": "abc"}', encoding="utf-8")
    assert load_guid_map_from_file(file_path, env_name="ODATA_ORGANIZATION_KEYS") == {"АЛ": "abc"}


def test_load_guid_map_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="ODATA_ORGANIZATION_KEYS"):
        load_guid_map("{bad", env_name="ODATA_ORGANIZATION_KEYS")


def test_resolve_incoming_extra_fields_merges_file_and_inline(tmp_path: Path) -> None:
    defaults_file = tmp_path / "defaults.json"
    defaults_file.write_text(
        json.dumps(
            {
                "Posted": False,
                "ГрифДоступа_Key": "from-file",
                "Ответственный_Key": "resp-from-file",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    resolved = resolve_incoming_extra_fields(
        json.dumps({"ГрифДоступа_Key": "from-inline"}, ensure_ascii=False),
        file_path=str(defaults_file),
    )
    assert resolved["Posted"] is False
    assert resolved["ГрифДоступа_Key"] == "from-inline"
    assert resolved["Ответственный_Key"] == "resp-from-file"


def test_odata_integration_service_loads_defaults_file(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
    tmp_path: Path,
) -> None:
    defaults_file = tmp_path / "defaults.json"
    defaults_file.write_text(
        json.dumps(
            {
                "Posted": False,
                "ГрифДоступа_Key": "bbdfce50-4266-11e8-8272-ac1f6b05524d",
                "Ответственный_Key": "4a3f1bd0-04f3-11e8-826d-ac1f6b05524d",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = ODataIntegrationService(
        "http://1c.local/odata/standard.odata",
        entity="Document_ТД_ВходящаяКорреспонденция",
        incoming_defaults_file=str(defaults_file),
    )
    with patch.object(
        service._client,
        "create_entity",
        return_value={"Ref_Key": "abc", "Number": "ВК-0001"},
    ) as create_mock:
        service.create_incoming_correspondence(
            sample_email,
            sample_routing,
            "Обзор",
            xml_document=SAMPLE_XML,
        )

    payload = create_mock.call_args[0][1]
    assert payload["Posted"] is False
    assert payload["ГрифДоступа_Key"] == "bbdfce50-4266-11e8-8272-ac1f6b05524d"
    assert payload["Ответственный_Key"] == "4a3f1bd0-04f3-11e8-826d-ac1f6b05524d"


def test_build_container_applies_incoming_defaults_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    defaults_file = tmp_path / "defaults.json"
    defaults_file.write_text(
        json.dumps({"Posted": False, "ГрифДоступа_Key": "guid-from-file"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("USE_STUBS", "false")
    monkeypatch.setenv("ERP_MODE", "odata")
    monkeypatch.setenv("ODATA_BASE_URL", "http://1c.local/odata/standard.odata")
    monkeypatch.setenv("ODATA_INCOMING_DEFAULTS_FILE", str(defaults_file))
    reset_settings()

    container = build_container(Settings())
    assert isinstance(container.integration, ODataIntegrationService)
    assert container.integration._extra_fields["ГрифДоступа_Key"] == "guid-from-file"
    assert container.integration._extra_fields["Posted"] is False


def test_build_incoming_document_payload_uses_msk_for_utc_received_at(
    sample_routing: RoutingResult,
) -> None:
    xml = (
        "<document>"
        "<organization>НП</organization>"
        "<theme>Тема</theme>"
        "<направление>КС</направление>"
        "</document>"
    )
    email = EmailMessage(
        message_id="m-utc",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Subj",
        received_at=datetime(2026, 7, 3, 7, 0, tzinfo=timezone.utc),
    )
    payload = build_incoming_document_payload(email, sample_routing, "", xml_document=xml)
    assert payload["Date"] == "2026-07-03T10:00:00"


def test_build_incoming_document_payload_org_from_xml_code(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    xml = SAMPLE_XML.replace("<organization>НП</organization>", "<organization>АЛ</organization>")
    org_keys = {
        "НП": "11111111-1111-1111-1111-111111111111",
        "АЛ": "22222222-2222-2222-2222-222222222222",
    }
    payload = build_incoming_document_payload(
        sample_email,
        sample_routing,
        "",
        xml_document=xml,
        organization_keys=org_keys,
    )
    assert payload["Организация_Key"] == org_keys["АЛ"]
    assert payload["ПлательщикНаправление"] == resolve_payer_direction("АЛ", "КС")


def test_gazprom_and_yandex_payer_direction_from_xml_direction() -> None:
    """Направление из XML, не partner — как у писем gazprom/yandex в production."""
    assert resolve_payer_direction("НП", "СС") == "ТурбулентностьДОНСС"
    assert resolve_payer_direction("НП", "ПР") == "ТурбулентностьДОНПроизводство1"


def test_almaz_default_partner_when_xml_partner_dash(
    sample_email: EmailMessage,
    sample_routing: RoutingResult,
) -> None:
    xml = (
        SAMPLE_XML.replace("<organization>НП</organization>", "<organization>АЛ</organization>")
        .replace("<partner>ООО Пример</partner>", "<partner>-</partner>")
        .replace("<направление>КС</направление>", "<направление>АЛ</направление>")
    )
    payload = build_incoming_document_payload(
        sample_email,
        sample_routing,
        "",
        xml_document=xml,
        organization_keys=SAMPLE_ORG_KEYS,
        department_keys=SAMPLE_DEPT_KEYS,
    )
    assert payload["ПлательщикНаправление"] == "АЛМАЗ"
    assert payload["Партнер"] == 'ООО "АЛМАЗ"'
    assert payload["Партнер_Type"] == "Edm.String"
