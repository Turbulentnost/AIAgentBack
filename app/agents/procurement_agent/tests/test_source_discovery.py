from __future__ import annotations

from datetime import datetime

from app.agents.procurement_agent.source_discovery import (
    get_source_capability,
    is_terminal_status,
    normalize_need_lines,
    normalize_source_document,
    parse_1c_datetime,
)
from app.models.enums import ProcurementSourceType


def test_parse_zero_dates_as_missing() -> None:
    assert parse_1c_datetime("0001-01-01T00:00:00") is None
    assert parse_1c_datetime("0001-01-01") is None
    parsed = parse_1c_datetime("2026-07-16T13:25:27")
    assert isinstance(parsed, datetime)
    assert parsed.isoformat() == "2026-07-16T10:25:27+00:00"


def test_parse_1c_naive_datetime_as_moscow() -> None:
    parsed = parse_1c_datetime(datetime(2026, 7, 17, 8, 26, 43))
    assert parsed is not None
    assert parsed.isoformat() == "2026-07-17T05:26:43+00:00"
    date_only = parse_1c_datetime("2026-07-17")
    assert date_only is not None
    assert date_only.isoformat() == "2026-07-16T21:00:00+00:00"


def test_normalize_need_lines_skips_cancelled_and_invalid() -> None:
    lines = normalize_need_lines(
        [
            {
                "LineNumber": 1,
                "КодСтроки": 10,
                "Номенклатура_Key": "n1",
                "Количество": 2,
                "Отменено": False,
                "ДатаОтгрузки": "2026-07-20T00:00:00",
            },
            {
                "LineNumber": 2,
                "Номенклатура_Key": "n2",
                "Количество": 5,
                "Отменено": True,
            },
            {
                "LineNumber": 3,
                "Количество": 1,
            },
        ]
    )
    assert len(lines) == 2
    assert lines[0].nomenclature_id == "n1"
    assert lines[0].quantity == 2
    assert lines[1].cancelled is True


def test_normalize_internal_consumption_document() -> None:
    document = normalize_source_document(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw={
            "Ref_Key": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "DataVersion": "AAAAABCAAJg=",
            "Number": "НП00-001021",
            "Date": "2026-07-16T13:25:27",
            "DeletionMark": False,
            "Posted": True,
            "Статус": "КВыполнению",
            "Автор_Key": "author-1",
            "Подразделение_Key": "dept-1",
            "Склад_Key": "wh-1",
            "Организация_Key": "org-1",
            "Приоритет_Key": "prio-1",
            "ДатаОтгрузки": "0001-01-01T00:00:00",
            "Товары": [
                {
                    "LineNumber": 1,
                    "КодСтроки": 1,
                    "Номенклатура_Key": "item-1",
                    "Количество": 3,
                    "Отменено": False,
                    "ВариантОбеспечения": "КОбеспечению",
                    "ДатаОтгрузки": "2026-07-23T00:00:00",
                }
            ],
        },
    )
    assert document.skip_reason is None
    assert document.required_date is None
    assert document.positions[0].required_date is not None
    assert document.warehouse_1c_ref == "wh-1"
    assert len(document.positions) == 1
    assert document.correlation_id.startswith("proc:erp_pm:internal_consumption_order:")


def test_header_delivery_date_is_applied_to_every_position() -> None:
    document = normalize_source_document(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw={
            "Ref_Key": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "Number": "НП-HEADER-DATE",
            "Date": "2026-07-16T10:00:00",
            "DeletionMark": False,
            "Статус": "КВыполнению",
            "ЖелаемаяДатаПоступления": "2026-07-25T00:00:00",
            "Товары": [
                {
                    "LineNumber": 1,
                    "Номенклатура_Key": "item-1",
                    "Количество": 2,
                    "ВариантОбеспечения": "КОбеспечению",
                    "ДатаПоступления": "2026-07-21T00:00:00",
                },
                {
                    "LineNumber": 2,
                    "Номенклатура_Key": "item-2",
                    "Количество": 3,
                    "ВариантОбеспечения": "КОбеспечению",
                    "ДатаПоступления": "2026-07-22T00:00:00",
                },
            ],
        },
    )
    assert document.required_date is not None
    assert all(
        line.required_date == document.required_date
        for line in document.positions
    )


def test_line_delivery_dates_are_used_without_header_date() -> None:
    document = normalize_source_document(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw={
            "Ref_Key": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "Number": "НП-LINE-DATES",
            "Date": "2026-07-16T10:00:00",
            "DeletionMark": False,
            "Статус": "КВыполнению",
            "Товары": [
                {
                    "LineNumber": 1,
                    "Номенклатура_Key": "item-1",
                    "Количество": 2,
                    "ВариантОбеспечения": "КОбеспечению",
                    "ДатаПоступления": "2026-07-21T00:00:00",
                },
                {
                    "LineNumber": 2,
                    "Номенклатура_Key": "item-2",
                    "Количество": 3,
                    "ВариантОбеспечения": "КОбеспечению",
                    "ДатаПоступления": "2026-07-22T00:00:00",
                },
            ],
        },
    )
    assert document.required_date is None
    assert document.positions[0].required_date != document.positions[1].required_date


def test_terminal_and_deleted_documents_are_skipped() -> None:
    closed = normalize_source_document(
        source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказМатериаловВПроизводство",
        raw={
            "Ref_Key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "DataVersion": "v1",
            "Number": "НП00-001360",
            "Date": "2026-07-16T11:40:53",
            "DeletionMark": False,
            "Статус": "Закрыт",
            "Склад_Key": "wh-2",
            "Товары": [
                {"LineNumber": 1, "Номенклатура_Key": "item-2", "Количество": 1, "Отменено": False}
            ],
        },
    )
    assert is_terminal_status("Закрыт")
    assert closed.skip_reason == "terminal_status:Закрыт"

    deleted = normalize_source_document(
        source_type=ProcurementSourceType.TRANSFER_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаПеремещение",
        raw={
            "Ref_Key": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "DataVersion": "v2",
            "Number": "НП00-000287",
            "Date": "2026-07-15T14:54:02",
            "DeletionMark": True,
            "Статус": "КВыполнению",
            "СкладОтправитель_Key": "from-1",
            "СкладПолучатель_Key": "to-1",
            "Товары": [
                {"LineNumber": 1, "Номенклатура_Key": "item-3", "Количество": 1, "Отменено": False}
            ],
        },
    )
    assert deleted.skip_reason == "deletion_mark"
    assert deleted.warehouse_1c_ref == "to-1"


def test_cancelled_document_is_skipped_even_with_active_lines() -> None:
    cancelled = normalize_source_document(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw={
            "Ref_Key": "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa",
            "DataVersion": "v-cancelled",
            "Number": "НП-CANCELLED",
            "Date": "2026-07-16T10:00:00",
            "DeletionMark": False,
            "Отменен": True,
            "Статус": "КВыполнению",
            "Товары": [
                {
                    "LineNumber": 1,
                    "Номенклатура_Key": "item-active",
                    "Количество": 2,
                    "Отменено": False,
                    "ВариантОбеспечения": "КОбеспечению",
                }
            ],
        },
    )
    assert cancelled.cancelled is True
    assert cancelled.skip_reason == "cancelled"


def test_document_keeps_only_non_cancelled_lines_for_supply() -> None:
    base = {
        "Ref_Key": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        "DataVersion": "v-action",
        "Number": "НП-2",
        "Date": "2026-07-16T10:00:00",
        "DeletionMark": False,
        "Статус": "КВыполнению",
        "Товары": [
            {
                "LineNumber": 1,
                "Номенклатура_Key": "item-active",
                "Количество": 2,
                "Отменено": False,
                "ВариантОбеспечения": "КОбеспечению",
            },
            {
                "LineNumber": 2,
                "Номенклатура_Key": "item-other",
                "Количество": 1,
                "Отменено": False,
                "ВариантОбеспечения": "КПроизводству",
            },
        ],
    }
    mixed = normalize_source_document(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw=base,
    )
    assert mixed.skip_reason is None
    assert [line.nomenclature_id for line in mixed.positions] == ["item-active"]

    base["Товары"][0]["Отменено"] = True
    without_supply = normalize_source_document(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        database="erp_pm",
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        raw=base,
    )
    assert without_supply.skip_reason == "inactive_supply_action"
    assert without_supply.positions == []


def test_reorder_point_capability_is_published() -> None:
    capability = get_source_capability(ProcurementSourceType.REORDER_POINT)
    assert capability.available is True
    assert capability.entity_set == "Document_ТД_УстановкаТочекЗаказа"
    assert capability.lines_entity_set == "Document_ТД_УстановкаТочекЗаказа_Товары"
    assert capability.unavailable_reason is None


def test_normalize_reorder_point_uses_new_maximum_stock() -> None:
    document = normalize_source_document(
        source_type=ProcurementSourceType.REORDER_POINT,
        database="erp_pm",
        entity_set="Document_ТД_УстановкаТочекЗаказа",
        raw={
            "Ref_Key": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "DataVersion": "v3",
            "Number": "ТД-1",
            "Date": "2026-07-16T10:00:00",
            "ДатаУтверждения": "2026-07-17T00:00:00",
            "DeletionMark": False,
            "Posted": True,
            "Статус": "Утвержден",
            "Ответственный_Key": "user-1",
            "Склад_Key": "wh-1",
            "Основание": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "Основание_Type": "StandardODATA.Document_ЗаказПоставщику",
            "Товары": [
                {
                    "LineNumber": 1,
                    "КодСтроки": 1,
                    "Номенклатура_Key": "item-4",
                    "МинимальноеКоличествоЗапаса_После": 5,
                    "МаксимальноеКоличествоЗапаса_После": 12,
                    "ОбеспечениеЗаказовПриПоддержанииЗапаса": "ЗаСчетЗапасов",
                }
            ],
        },
    )
    assert document.skip_reason is None
    assert document.positions[0].quantity == 12
    assert document.required_date is None
    assert document.positions[0].supply_action == "ЗаСчетЗапасов"
    assert document.source_basis_1c_ref == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert document.source_basis_type == "StandardODATA.Document_ЗаказПоставщику"


def test_reorder_point_uses_line_number_when_line_code_is_zero() -> None:
    document = normalize_source_document(
        source_type=ProcurementSourceType.REORDER_POINT,
        database="erp_pm",
        entity_set="Document_ТД_УстановкаТочекЗаказа",
        raw={
            "Ref_Key": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
            "Date": "2026-07-20T10:00:00",
            "Товары": [
                {
                    "LineNumber": str(number),
                    "КодСтроки": "0",
                    "Номенклатура_Key": f"item-{number}",
                    "МаксимальноеКоличествоЗапаса_После": number,
                    "ОбеспечениеЗаказовПриПоддержанииЗапаса": "ЗаСчетЗапасов",
                }
                for number in (1, 2)
            ],
        },
    )

    assert [line.line_id for line in document.positions] == ["1", "2"]
