from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import ProcurementSourceType

ZERO_DATE_PREFIXES = ("0001-01-01", "0001-01-01T00:00:00")
TERMINAL_STATUSES = frozenset(
    {
        "закрыт",
        "closed",
        "отменен",
        "отменён",
        "cancelled",
        "canceled",
    }
)


class SourceCapability(BaseModel):
    source_type: ProcurementSourceType
    entity_set: str | None
    lines_entity_set: str | None = None
    label_ru: str
    available: bool = True
    unavailable_reason: str | None = None


class NormalizedNeedLine(BaseModel):
    line_id: str
    line_number: int = 0
    nomenclature_id: str
    nomenclature_name: str | None = None
    characteristic_id: str | None = None
    unit_id: str | None = None
    unit: str | None = None
    quantity: Decimal
    required_date: datetime | None = None
    cancelled: bool = False
    supply_action: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class NormalizedSourceDocument(BaseModel):
    source_type: ProcurementSourceType
    entity_set: str
    database: str
    ref_key: str
    data_version: str | None = None
    number: str | None = None
    date: datetime | None = None
    posted: bool | None = None
    deletion_mark: bool = False
    status: str | None = None
    initiator_1c_ref: str | None = None
    initiator_name: str | None = None
    department_1c_ref: str | None = None
    department_name: str | None = None
    warehouse_1c_ref: str | None = None
    warehouse_name: str | None = None
    warehouse_from_1c_ref: str | None = None
    warehouse_to_1c_ref: str | None = None
    organization_1c_ref: str | None = None
    priority_1c_ref: str | None = None
    required_date: datetime | None = None
    positions: list[NormalizedNeedLine] = Field(default_factory=list)
    content_hash: str
    skip_reason: str | None = None

    @property
    def correlation_id(self) -> str:
        return f"proc:{self.database}:{self.source_type.value}:{self.ref_key}"[:128]

    @property
    def poll_idempotency_key(self) -> str:
        version = self.data_version or self.content_hash
        return f"poll:{self.database}:{self.ref_key}:{version}"[:255]


SOURCE_CAPABILITIES: dict[ProcurementSourceType, SourceCapability] = {
    ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER: SourceCapability(
        source_type=ProcurementSourceType.INTERNAL_CONSUMPTION_ORDER,
        entity_set="Document_ЗаказНаВнутреннееПотребление",
        lines_entity_set="Document_ЗаказНаВнутреннееПотребление_Товары",
        label_ru="Заказ на внутреннее потребление",
    ),
    ProcurementSourceType.PRODUCTION_MATERIAL_ORDER: SourceCapability(
        source_type=ProcurementSourceType.PRODUCTION_MATERIAL_ORDER,
        entity_set="Document_ЗаказМатериаловВПроизводство",
        lines_entity_set="Document_ЗаказМатериаловВПроизводство_Товары",
        label_ru="Заказ материалов в производство",
    ),
    ProcurementSourceType.TRANSFER_ORDER: SourceCapability(
        source_type=ProcurementSourceType.TRANSFER_ORDER,
        entity_set="Document_ЗаказНаПеремещение",
        lines_entity_set="Document_ЗаказНаПеремещение_Товары",
        label_ru="Заказ на перемещение",
    ),
    ProcurementSourceType.REORDER_POINT: SourceCapability(
        source_type=ProcurementSourceType.REORDER_POINT,
        entity_set="Document_ТД_УстановкаТочекЗаказа",
        lines_entity_set="Document_ТД_УстановкаТочекЗаказа_Товары",
        label_ru="Сигнал точки заказа / заказ условному поставщику",
    ),
}


def get_source_capability(source_type: ProcurementSourceType | str) -> SourceCapability:
    key = (
        source_type
        if isinstance(source_type, ProcurementSourceType)
        else ProcurementSourceType(source_type)
    )
    return SOURCE_CAPABILITIES[key]


def list_source_capabilities() -> list[SourceCapability]:
    return list(SOURCE_CAPABILITIES.values())


def parse_1c_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text or any(text.startswith(prefix) for prefix in ZERO_DATE_PREFIXES):
        return None
    normalized = text.replace("Z", "+00:00")
    if len(normalized) == 10:
        normalized = f"{normalized}T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def is_terminal_status(status: str | None) -> bool:
    if not status:
        return False
    return status.strip().casefold() in TERMINAL_STATUSES


def is_supply_action(value: str | None) -> bool:
    if not value:
        return False
    normalized = (
        value.strip()
        .casefold()
        .replace("ё", "е")
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )
    return normalized == "кобеспечению"


def normalize_need_lines(raw_lines: Any) -> list[NormalizedNeedLine]:
    if not isinstance(raw_lines, list):
        return []
    lines: list[NormalizedNeedLine] = []
    for index, item in enumerate(raw_lines, start=1):
        if not isinstance(item, dict):
            continue
        nomenclature_id = _optional_str(item.get("Номенклатура_Key"))
        if not nomenclature_id:
            continue
        quantity = (
            _decimal(item.get("Количество"))
            or _decimal(item.get("МаксимальноеКоличествоЗапаса_После"))
            or _decimal(item.get("МинимальноеКоличествоЗапаса_После"))
        )
        if quantity is None:
            continue
        line_number = int(item.get("LineNumber") or item.get("КодСтроки") or index)
        line_id = str(item.get("КодСтроки") or item.get("LineNumber") or line_number)
        required_date = (
            parse_1c_datetime(item.get("ДатаОтгрузки"))
            or parse_1c_datetime(item.get("НачалоОтгрузки"))
            or parse_1c_datetime(item.get("ОкончаниеПоступления"))
            or parse_1c_datetime(item.get("ДатаПоступления"))
        )
        lines.append(
            NormalizedNeedLine(
                line_id=line_id,
                line_number=line_number,
                nomenclature_id=nomenclature_id,
                nomenclature_name=_optional_str(item.get("Номенклатура") or item.get("item")),
                characteristic_id=_optional_str(item.get("Характеристика_Key")),
                unit_id=_optional_str(item.get("Упаковка_Key")),
                unit=_optional_str(item.get("Упаковка") or item.get("unit")),
                quantity=quantity,
                required_date=required_date,
                cancelled=bool(item.get("Отменено")),
                supply_action=_optional_str(
                    item.get("ВариантОбеспечения")
                    or item.get("Действие")
                    or item.get("ОбеспечениеЗаказовПриПоддержанииЗапаса")
                    or item.get("МетодОбеспеченияПотребностей")
                ),
                raw_payload={
                    key: item.get(key)
                    for key in (
                        "LineNumber",
                        "КодСтроки",
                        "Номенклатура_Key",
                        "Характеристика_Key",
                        "Количество",
                        "МинимальноеКоличествоЗапаса_До",
                        "МаксимальноеКоличествоЗапаса_До",
                        "МинимальноеКоличествоЗапаса_После",
                        "МаксимальноеКоличествоЗапаса_После",
                        "МетодОбеспеченияПотребностей",
                        "ОбеспечениеЗаказовПриПоддержанииЗапаса",
                        "Отменено",
                        "ВариантОбеспечения",
                    )
                    if key in item
                },
            )
        )
    return lines


def _content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_source_document(
    *,
    source_type: ProcurementSourceType,
    database: str,
    entity_set: str,
    raw: dict[str, Any],
) -> NormalizedSourceDocument:
    ref_key = _optional_str(raw.get("Ref_Key") or raw.get("ref"))
    if not ref_key:
        raise ValueError("source document missing Ref_Key")

    status = _optional_str(raw.get("Статус"))
    deletion_mark = bool(raw.get("DeletionMark"))
    positions = normalize_need_lines(raw.get("Товары") or [])
    active_positions = [line for line in positions if not line.cancelled]
    required_date = (
        parse_1c_datetime(raw.get("ЖелаемаяДатаПоступления"))
        or parse_1c_datetime(raw.get("ДатаОтгрузки"))
        or parse_1c_datetime(raw.get("ДатаУтверждения"))
        or next((line.required_date for line in positions if line.required_date), None)
    )
    warehouse_1c_ref = _optional_str(raw.get("Склад_Key") or raw.get("ЦеховаяКладовая_Key"))
    warehouse_from = _optional_str(raw.get("СкладОтправитель_Key"))
    warehouse_to = _optional_str(raw.get("СкладПолучатель_Key"))
    if source_type is ProcurementSourceType.TRANSFER_ORDER:
        warehouse_1c_ref = warehouse_to or warehouse_from

    document = NormalizedSourceDocument(
        source_type=source_type,
        entity_set=entity_set,
        database=database,
        ref_key=ref_key,
        data_version=_optional_str(raw.get("DataVersion")),
        number=_optional_str(raw.get("Number")),
        date=parse_1c_datetime(raw.get("Date")),
        posted=raw.get("Posted") if isinstance(raw.get("Posted"), bool) else None,
        deletion_mark=deletion_mark,
        status=status,
        initiator_1c_ref=_optional_str(raw.get("Автор_Key") or raw.get("Ответственный_Key")),
        department_1c_ref=_optional_str(raw.get("Подразделение_Key")),
        warehouse_1c_ref=warehouse_1c_ref,
        warehouse_from_1c_ref=warehouse_from,
        warehouse_to_1c_ref=warehouse_to,
        organization_1c_ref=_optional_str(raw.get("Организация_Key")),
        priority_1c_ref=_optional_str(raw.get("Приоритет_Key")),
        required_date=required_date,
        positions=active_positions,
        content_hash="",
    )
    hash_payload = document.model_dump(mode="json", exclude={"content_hash", "skip_reason"})
    document.content_hash = _content_hash(hash_payload)

    if deletion_mark:
        document.skip_reason = "deletion_mark"
    elif is_terminal_status(status):
        document.skip_reason = f"terminal_status:{status}"
    elif not active_positions:
        document.skip_reason = "no_active_positions"
    elif any(not is_supply_action(line.supply_action) for line in active_positions):
        document.skip_reason = "inactive_supply_action"
    return document


def positions_to_agent_source_data(document: NormalizedSourceDocument) -> dict[str, Any]:
    warehouse_ids = [
        value
        for value in (
            document.warehouse_1c_ref,
            document.warehouse_from_1c_ref,
            document.warehouse_to_1c_ref,
        )
        if value
    ]
    return {
        "positions": [
            {
                "line_id": line.line_id,
                "nomenclature_id": line.nomenclature_id,
                "nomenclature_name": line.nomenclature_name or line.nomenclature_id,
                "unit": line.unit or "шт",
                "gross_quantity": str(line.quantity),
                "required_date": line.required_date.isoformat() if line.required_date else None,
                "characteristic_id": line.characteristic_id,
            }
            for line in document.positions
        ],
        "warehouse_ids": list(dict.fromkeys(warehouse_ids)),
        "organization_id": document.organization_1c_ref,
        "requested_date": document.required_date.isoformat() if document.required_date else None,
        "source_number": document.number,
        "source_status": document.status,
    }


__all__ = [
    "SOURCE_CAPABILITIES",
    "NormalizedNeedLine",
    "NormalizedSourceDocument",
    "SourceCapability",
    "get_source_capability",
    "is_terminal_status",
    "is_supply_action",
    "list_source_capabilities",
    "normalize_need_lines",
    "normalize_source_document",
    "parse_1c_datetime",
    "positions_to_agent_source_data",
]
