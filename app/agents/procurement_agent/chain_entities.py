"""OData/MCP entity sets and minimal fields for procurement 1C document chain.

Cases are created only from need documents (source_discovery). Documents below
enrich existing cases via subordination / document-basis links — they do not
create new cases.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class ChainEntitySpec(TypedDict):
    key: str
    label_ru: str
    entity_set: str
    lines_entity_set: str | None
    header_fields: list[str]
    line_fields: list[str]
    mcp_list_capability: str
    mcp_search_capability: str
    notes: str


# Need sources (already in source_discovery) — listed for chain completeness.
NEED_ENTITY_SETS: dict[str, ChainEntitySpec] = {
    "production_material_order": {
        "key": "production_material_order",
        "label_ru": "Заказ материалов в производство",
        "entity_set": "Document_ЗаказМатериаловВПроизводство",
        "lines_entity_set": "Document_ЗаказМатериаловВПроизводство_Товары",
        "header_fields": [
            "Ref_Key",
            "Number",
            "Date",
            "Posted",
            "DeletionMark",
            "Склад_Key",
            "Подразделение_Key",
            "Статус",
            "ДатаОтгрузки",
        ],
        "line_fields": [
            "Ref_Key",
            "LineNumber",
            "Номенклатура_Key",
            "Количество",
            "Упаковка_Key",
            "ДатаОтгрузки",
            "Отменено",
        ],
        "mcp_list_capability": "read_document_get_documents",
        "mcp_search_capability": "read_document_search_documents",
        "notes": "Источник потребности (кейс).",
    },
    "transfer_order": {
        "key": "transfer_order",
        "label_ru": "Заказ на перемещение",
        "entity_set": "Document_ЗаказНаПеремещение",
        "lines_entity_set": "Document_ЗаказНаПеремещение_Товары",
        "header_fields": [
            "Ref_Key",
            "Number",
            "Date",
            "Posted",
            "DeletionMark",
            "СкладОтправитель_Key",
            "СкладПолучатель_Key",
            "Статус",
            "Основание",
            "Основание_Type",
            "ДокументОснование",
            "ДокументОснование_Type",
        ],
        "line_fields": [
            "Ref_Key",
            "LineNumber",
            "Номенклатура_Key",
            "Количество",
            "Упаковка_Key",
            "НачалоОтгрузки",
            "ОкончаниеПоступления",
            "Отменено",
        ],
        "mcp_list_capability": "read_document_get_documents",
        "mcp_search_capability": "read_document_search_documents",
        "notes": "Источник потребности (кейс).",
    },
    "internal_consumption_order": {
        "key": "internal_consumption_order",
        "label_ru": "Заказ на внутреннее потребление",
        "entity_set": "Document_ЗаказНаВнутреннееПотребление",
        "lines_entity_set": "Document_ЗаказНаВнутреннееПотребление_Товары",
        "header_fields": [
            "Ref_Key",
            "Number",
            "Date",
            "Posted",
            "DeletionMark",
            "Склад_Key",
            "Подразделение_Key",
            "Статус",
        ],
        "line_fields": [
            "Ref_Key",
            "LineNumber",
            "Номенклатура_Key",
            "Количество",
            "Упаковка_Key",
            "ДатаОтгрузки",
            "Отменено",
        ],
        "mcp_list_capability": "read_document_get_documents",
        "mcp_search_capability": "read_document_search_documents",
        "notes": "Источник потребности (кейс).",
    },
}


# Enrichment-only documents (фото 3–6).
ENRICH_ENTITY_SETS: dict[str, ChainEntitySpec] = {
    "purchase_order": {
        "key": "purchase_order",
        "label_ru": "Заказ поставщику",
        "entity_set": "Document_ЗаказПоставщику",
        "lines_entity_set": "Document_ЗаказПоставщику_Товары",
        "header_fields": [
            "Ref_Key",
            "Number",
            "Date",
            "Posted",
            "DeletionMark",
            "Контрагент_Key",
            "Партнер_Key",
            "СуммаДокумента",
            "Валюта_Key",
            "Статус",
            "ДатаПоступления",
            "ДокументОснование",
            "ХозяйственнаяОперация",
        ],
        "line_fields": [
            "Ref_Key",
            "LineNumber",
            "Номенклатура_Key",
            "Количество",
            "Цена",
            "Сумма",
            "ДатаПоступления",
            "Отменено",
        ],
        "mcp_list_capability": "read_document_get_documents",
        "mcp_search_capability": "read_document_search_documents",
        "notes": (
            "Условный vs определённый: определённый при заполненном "
            "Контрагент_Key / Партнер_Key (уточнять по метаданным при первой пробе)."
        ),
    },
    "cash_request": {
        "key": "cash_request",
        "label_ru": "Заявка на расходование ДС",
        "entity_set": "Document_ЗаявкаНаРасходованиеДенежныхСредств",
        "lines_entity_set": "Document_ЗаявкаНаРасходованиеДенежныхСредств_РасшифровкаПлатежа",
        "header_fields": [
            "Ref_Key",
            "Number",
            "Date",
            "Posted",
            "DeletionMark",
            "СуммаДокумента",
            "Валюта_Key",
            "Статус",
            "ДокументОснование",
            "Контрагент_Key",
        ],
        "line_fields": [
            "Ref_Key",
            "LineNumber",
            "Сумма",
            "СтатьяДвиженияДенежныхСредств_Key",
            "ОбъектРасчетов",
        ],
        "mcp_list_capability": "read_document_get_documents",
        "mcp_search_capability": "read_document_search_documents",
        "notes": "Статус оплаты: согласована / оплачена / отклонена; связь с ЗП.",
    },
    "otk_presentation": {
        "key": "otk_presentation",
        "label_ru": "Журнал предъявления ТМЦ на входной контроль",
        # Verified against live erp_pm OData/$metadata (2026-07):
        # Document_ТД_ПредъявлениеТМЦНаВходнойКонтроль — 404; real name below.
        "entity_set": "Document_ТД_ПредъявлениеТМЦнаОТК",
        "lines_entity_set": "Document_ТД_ПредъявлениеТМЦнаОТК_Товары",
        "header_fields": [
            "Ref_Key",
            "Number",
            "Date",
            "Posted",
            "DeletionMark",
            "ДокументОснование",
            "ДокументОснование_Type",
            "Контрагент_Key",
            "Состояние",
            "ЭтапДокумента",
            "НомерНакладной",
            "ДатаНакладной",
            "СрокИсполнения",
            "ЗонаХранения",
            "МестоПредъявления",
            "Склад_Key",
            "СкладВходногоКонтроля_Key",
        ],
        "line_fields": [
            "Ref_Key",
            "LineNumber",
            "Номенклатура_Key",
            "Количество",
            "КоличествоВУПД",
            "КоличествоПринятыхНаОТК",
            "ПринятоОТК",
            "НеПринятоОТК",
            "ПровереноОТК",
            "Качество",
        ],
        "mcp_list_capability": "read_document_get_documents",
        "mcp_search_capability": "read_document_search_documents",
        "notes": (
            "Live entity: Document_ТД_ПредъявлениеТМЦнаОТК "
            "(есть также ТоварыДляОТК). Старый hypothetical name "
            "…НаВходнойКонтроль в erp_pm отсутствует."
        ),
    },
    "purchase_receipt": {
        "key": "purchase_receipt",
        "label_ru": "Приобретение товаров и услуг",
        "entity_set": "Document_ПриобретениеТоваровУслуг",
        "lines_entity_set": "Document_ПриобретениеТоваровУслуг_Товары",
        "header_fields": [
            "Ref_Key",
            "Number",
            "Date",
            "Posted",
            "DeletionMark",
            "Склад_Key",
            "Контрагент_Key",
            "ДокументОснование",
            "СуммаДокумента",
        ],
        "line_fields": [
            "Ref_Key",
            "LineNumber",
            "Номенклатура_Key",
            "Количество",
            "Цена",
            "Сумма",
        ],
        "mcp_list_capability": "read_document_get_documents",
        "mcp_search_capability": "read_document_search_documents",
        "notes": "Связь с предъявлением/ЗП; факт оприходования по проведению.",
    },
    "goods_receipt_order": {
        "key": "goods_receipt_order",
        "label_ru": "Приходный ордер на товары",
        "entity_set": "Document_ПриходныйОрдерНаТовары",
        "lines_entity_set": "Document_ПриходныйОрдерНаТовары_Товары",
        "header_fields": [
            "Ref_Key",
            "Number",
            "Date",
            "Posted",
            "DeletionMark",
            "Склад_Key",
            "ДокументОснование",
            "Статус",
        ],
        "line_fields": [
            "Ref_Key",
            "LineNumber",
            "Номенклатура_Key",
            "Количество",
        ],
        "mcp_list_capability": "read_document_get_documents",
        "mcp_search_capability": "read_document_search_documents",
        "notes": "Финальное оприходование на склад.",
    },
}


ChainStage = Literal[
    "purchase_order",
    "cash_request",
    "otk_presentation",
    "purchase_receipt",
    "goods_receipt_order",
]

CHAIN_STAGE_ORDER: tuple[ChainStage, ...] = (
    "purchase_order",
    "cash_request",
    "otk_presentation",
    "purchase_receipt",
    "goods_receipt_order",
)

# MCP capability aliases exposed in mcp1C.json / supplier_mcp.json (read-only).
CHAIN_MCP_CAPABILITY_ALIASES: dict[str, str] = {
    "read_procurement_chain_purchase_orders": "read_document_get_documents",
    "read_procurement_chain_cash_requests": "read_document_get_documents",
    "read_procurement_chain_otk_presentations": "read_document_get_documents",
    "read_procurement_chain_purchase_receipts": "read_document_get_documents",
    "read_procurement_chain_goods_receipt_orders": "read_document_get_documents",
    "read_procurement_get_supplier_history": "read_procurement_get_supplier_history",
}


def select_fields(spec: ChainEntitySpec) -> list[str]:
    """Minimal OData $select for header (+ basis link)."""
    return list(dict.fromkeys(spec["header_fields"]))


def all_enrich_entity_sets() -> list[str]:
    return [spec["entity_set"] for spec in ENRICH_ENTITY_SETS.values()]


def inventory_snapshot() -> dict[str, Any]:
    """Compact inventory for ops / tests."""
    return {
        "need": {
            key: {
                "entity_set": spec["entity_set"],
                "lines_entity_set": spec["lines_entity_set"],
                "header_fields": spec["header_fields"],
                "line_fields": spec["line_fields"],
            }
            for key, spec in NEED_ENTITY_SETS.items()
        },
        "enrich": {
            key: {
                "entity_set": spec["entity_set"],
                "lines_entity_set": spec["lines_entity_set"],
                "header_fields": spec["header_fields"],
                "line_fields": spec["line_fields"],
                "notes": spec["notes"],
            }
            for key, spec in ENRICH_ENTITY_SETS.items()
        },
        "mcp_aliases": dict(CHAIN_MCP_CAPABILITY_ALIASES),
    }


__all__ = [
    "CHAIN_MCP_CAPABILITY_ALIASES",
    "CHAIN_STAGE_ORDER",
    "ChainEntitySpec",
    "ChainStage",
    "ENRICH_ENTITY_SETS",
    "NEED_ENTITY_SETS",
    "all_enrich_entity_sets",
    "inventory_snapshot",
    "select_fields",
]
