from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from calendar import monthrange
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, TypeVar

import httpx
from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

TCatalogEntry = TypeVar("TCatalogEntry")

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_AVEON_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "aveon"
_MAPPING_FILE = _AVEON_DATA_DIR / "Сопоставление номенклатур.xlsx"
_SPECS_FILE = _AVEON_DATA_DIR / "Сокол Спецификация из 1с.xlsx"
_HEADER_FILE = _AVEON_DATA_DIR / "Header.xlsx"
_PRICES_FILE = _AVEON_DATA_DIR / "Цены закупки за 2026_0833.xlsx"
_RESULT_DATA_START_ROW = 5  # дневной лист
_MONTHLY_DATA_START_ROW = 6  # помесячный: шапка 1–5, данные с 6
_RESULT_GRID_COLS = 23  # устаревший лимит Header.xlsx; помесячный строится динамически
_PRICE_FUZZY_THRESHOLD = 0.78
_SHEET_MONTHLY_ASSURANCE = "помесячное обеспечение"
_SHEET_DAILY_ASSURANCE = "обеспечение по дням"
_SCHEDULE_CATEGORIES = ("заказ", "опытные", "склад")
_SCHEDULE_METRICS = ("план", "факт")
_CATEGORY_LABELS = {
    "заказ": "Заказ",
    "опытные": "Опытные образцы",
    "склад": "Склад",
}
_MONTHLY_COLS_PER_MONTH = 8  # 3×(план+факт) + поступление + прогноз
# A–F: номенклатура, изделия, поставщик, ед. изм., цена, остаток
_FIXED_RESULT_COLS = 6
_STOCK_COL_LETTER = "F"
# Как в эталоне «Анализ обеспеченности»: дефицит < 0
_FORECAST_DEFICIT_FILL = PatternFill(start_color="F4CCCC", end_color="F4CCCC", fill_type="solid")
_FORECAST_DEFICIT_FONT = Font(color="9C0006")
# Цвета шапки как в Header.xlsx (помесячное / по дням — одинаково; ARGB)
_HEADER_TITLE_FILL = PatternFill(start_color="FF1F4E78", end_color="FF1F4E78", fill_type="solid")
_HEADER_TITLE_FONT = Font(bold=True, color="FFFFFFFF", size=15)
_HEADER_SUBTITLE_FILL = PatternFill(start_color="FFD9EAF7", end_color="FFD9EAF7", fill_type="solid")
_HEADER_SUBTITLE_FONT = Font(bold=False, color="FF1F1F1F", size=11)
_HEADER_GROUP_FILL = PatternFill(start_color="FF5B9BD5", end_color="FF5B9BD5", fill_type="solid")
_HEADER_GROUP_FONT = Font(bold=True, color="FFFFFFFF", size=11)
_HEADER_METRIC_FILL = PatternFill(start_color="FFD9EAF7", end_color="FFD9EAF7", fill_type="solid")
_HEADER_METRIC_FONT = Font(bold=True, color="FF1F1F1F", size=11)
_DETAILED_DAY_SKIP_HEADERS = {
    "итог",
    "итого",
    "план",
    "остаток",
    "отклонение",
    "№",
    "п/п",
    "№ п/п",
    "наименование",
    "наимнование",
    "наименования",
}

_SECTION_NAME_RE = re.compile(
    r"(?i)^(материалы\s+и\s+работы|дерево\s+спецификации|спецификация)\b"
)


@dataclass
class UploadedWorkbook:
    filename: str
    content: bytes


@dataclass
class ProductSpecLink:
    schedule_product: str
    nomenclature: str | None = None
    spec_sheet: str | None = None
    status: str = "unmatched"
    reason: str = ""


@dataclass
class SpecMaterialItem:
    """Позиция спецификации в разрезе одного изделия."""

    nomenclature: str
    quantity: float | None
    product: str
    unit: str | None = None
    spec_sheet: str | None = None


@dataclass
class PurchasePriceEntry:
    """Строка из файла закупочных цен (после агрегации дублей)."""

    nomenclature: str
    supplier: str
    price: float | None
    turnover_qty: float = 0.0


@dataclass
class MergedNomenclatureRow:
    """Уникальная номенклатура после объединения по всем изделиям.

    Структура хранится в памяти результата анализа для дальнейших расчётов.
    """

    nomenclature: str
    products: list[str]
    quantity: float | None
    unit: str | None = None
    by_product: dict[str, float | None] = field(default_factory=dict)
    supplier: str | None = None
    price: float | None = None
    price_match: str = ""  # exact | contains | fuzzy | unmatched
    stock: float | None = None
    stock_match: str = ""  # exact | contains | fuzzy | unmatched
    # месяц → категория → {план, факт}
    monthly_demand: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    monthly_receipts: dict[str, float] = field(default_factory=dict)
    monthly_forecast: dict[str, float] = field(default_factory=dict)
    # Ключи ISO-дат YYYY-MM-DD для листа «обеспечение по дням»
    daily_demand: dict[str, float] = field(default_factory=dict)
    daily_receipts: dict[str, float] = field(default_factory=dict)
    daily_forecast: dict[str, float] = field(default_factory=dict)


@dataclass
class StockEntry:
    nomenclature: str
    quantity: float | None


@dataclass
class ShipmentReceiptEntry:
    """Сводка ожидаемых поступлений по номенклатуре (сумма по всем листам/изделиям)."""

    nomenclature: str
    monthly_qty: dict[str, float] = field(default_factory=dict)
    daily_qty: dict[str, float] = field(default_factory=dict)  # ISO date → qty


@dataclass
class LogisticsRiskItem:
    """Позиция на контрольной точке логистики (на расчётную дату = сегодня)."""

    nomenclature: str
    supplier: str | None
    quantity: float
    moscow_date: str
    milestone_date: str  # крайняя дата окна (deadline)
    sheet: str
    window_start: str = ""
    window_end: str = ""
    days_remaining: int = 0
    risk_ratio: float = 0.0  # 0 = красный (риск), 1 = зелёный (запас)
    risk_level: str = "critical"  # low | medium | high | critical


@dataclass
class LogisticsRiskStage:
    key: str
    label: str
    items: list[LogisticsRiskItem] = field(default_factory=list)


@dataclass
class LogisticsRiskBoard:
    """Доска рисков по стадиям логистики (сортировка = порядок цепочки)."""

    as_of: str
    stages: list[LogisticsRiskStage] = field(default_factory=list)


@dataclass
class ScheduleQtyColumn:
    """Колонка qty в графике производства (месяц × категория × план/факт)."""

    col: int
    month: str
    category: str  # заказ | опытные | склад
    metric: str  # план | факт


@dataclass
class ScheduleTableLayout:
    """Распознанная таблица помесячного графика."""

    name_col: int
    data_start_row: int
    columns: list[ScheduleQtyColumn]
    is_split: bool  # True = Заказ/Опытные/Склад × План/Факт


@dataclass
class ScheduleProductPlan:
    """Изделие из графика производства с помесячным планом выпуска.

    monthly_qty: месяц → категория → {план, факт}
    """

    product: str
    monthly_qty: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)


@dataclass
class DetailedScheduleProductPlan:
    """Изделие из детального графика с планом выпуска по дням выбранного месяца."""

    product: str
    daily_qty: dict[str, float] = field(default_factory=dict)  # ISO date → qty
    year: int = 0
    month: int = 0


@dataclass
class DetailedScheduleExtract:
    files: list[str]
    plans: list[DetailedScheduleProductPlan]
    year: int
    month: int
    day_keys: list[str] = field(default_factory=list)


@dataclass
class AveonAnalysisResult:
    roles: dict[str, str]
    source: str
    production_schedule_files: list[str]
    production_schedule_products: list[str]
    production_schedule_plans: list[ScheduleProductPlan] = field(default_factory=list)
    detailed_production_schedule_files: list[str] = field(default_factory=list)
    detailed_schedule_month: str = ""
    product_spec_links: list[ProductSpecLink] = field(default_factory=list)
    material_usages: list[SpecMaterialItem] = field(default_factory=list)
    merged_nomenclatures: list[MergedNomenclatureRow] = field(default_factory=list)
    result_xlsx_bytes: bytes | None = None
    stock_files: list[str] = field(default_factory=list)
    shipment_files: list[str] = field(default_factory=list)
    logistics_risks: LogisticsRiskBoard | None = None


@dataclass
class _MappingRow:
    nomenclature: str
    contract_nomenclature: str
    contract: str = ""


WorkbookRole = str
ROLE_SPECIFICATION = "specification"
ROLE_STOCK = "stock"
ROLE_PRODUCTION_SCHEDULE = "production_schedule"
ROLE_DETAILED_PRODUCTION_SCHEDULE = "detailed_production_schedule"
ROLE_SHIPMENT_SCHEDULE = "shipment_schedule"
ROLE_OTHER = "other"

KNOWN_ROLES = {
    ROLE_SPECIFICATION,
    ROLE_STOCK,
    ROLE_PRODUCTION_SCHEDULE,
    ROLE_DETAILED_PRODUCTION_SCHEDULE,
    ROLE_SHIPMENT_SCHEDULE,
    ROLE_OTHER,
}

# «Неделя 01.07-03.07» / «План 01.07-03.07» (префикс обязателен — иначе ловим годовые диапазоны)
_DETAILED_PERIOD_HEADER_RE = re.compile(
    r"(?:недел[яи]?|план)\s+\d{1,2}\.\d{1,2}\s*[-–—]\s*\d{1,2}\.\d{1,2}",
    re.IGNORECASE,
)
_WEEK_HEADER_RE = _DETAILED_PERIOD_HEADER_RE
# Дневные колонки в превью (datetime из openpyxl → ISO-строка)
_DAY_DATE_HEADER_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")

_MONTH_LOWER = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)

_MONTH_NOMINATIVE = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)

_MONTH_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# Порядок = стадии цепочки для UI. D = дата поставки в Москву из колонки графика.
# «5-10» / «7-14 к.д.» → (короткая, длинная) календарные дни.
# МСК/Ростов — окна [короткая … длинная]; загрузка/таможня — точечные дни.
_LOGISTICS_CUSTOMS_DAYS = 2
_LOGISTICS_STAGE_DEFS: tuple[tuple[str, str], ...] = (
    ("loading_dispatch", "Загрузка и отправка"),
    ("msk_arrival", "Прибытие в МСК"),
    ("customs_clearance", "Таможня"),
    ("rostov_arrival", "Прибытие в Ростов"),
)
_LOGISTICS_RANGE_RE = re.compile(r"(\d+)\s*[-–—]\s*(\d+)")


async def analyze_aveon_excel_files(workbooks: list[UploadedWorkbook]) -> AveonAnalysisResult:
    """Роли → изделия графика → листы спецификаций → материалы → result.xlsx."""
    previews = await asyncio.to_thread(_build_workbook_previews, workbooks)
    role_map, source = await _classify_workbooks_with_lm(previews)
    schedule_files, schedule_plans = await asyncio.to_thread(
        _extract_production_schedule_products, workbooks, role_map
    )
    detailed_extract = await asyncio.to_thread(
        _extract_detailed_production_schedule, workbooks, role_map
    )
    products = [plan.product for plan in schedule_plans]
    product_spec_links = await _resolve_schedule_products_to_specs(products)
    (
        material_usages,
        merged_nomenclatures,
        result_xlsx,
        stock_files,
        shipment_files,
        logistics_risks,
    ) = await asyncio.to_thread(
        _collect_and_merge_spec_materials,
        product_spec_links,
        workbooks,
        role_map,
        schedule_plans,
        detailed_extract,
    )
    price_matched = sum(
        1 for row in merged_nomenclatures if row.price_match not in ("", "unmatched")
    )
    stock_matched = sum(
        1 for row in merged_nomenclatures if row.stock_match not in ("", "unmatched")
    )
    receipts_nonzero = sum(
        1
        for row in merged_nomenclatures
        if any(value > 0 for value in row.monthly_receipts.values())
    )
    forecast_deficit = sum(
        1
        for row in merged_nomenclatures
        if any(value < 0 for value in row.monthly_forecast.values())
    )
    logger.info(
        "document_analysis_agent.roles_classified",
        source=source,
        roles=role_map,
    )
    logger.info(
        "document_analysis_agent.production_schedule_products",
        files=schedule_files,
        count=len(products),
        products=products[:20],
        plans=[
            {"product": plan.product, "months": plan.monthly_qty}
            for plan in schedule_plans[:5]
        ],
    )
    logger.info(
        "document_analysis_agent.detailed_production_schedule",
        files=detailed_extract.files,
        month=f"{detailed_extract.year:04d}-{detailed_extract.month:02d}"
        if detailed_extract.year and detailed_extract.month
        else "",
        products=len(detailed_extract.plans),
        days=len(detailed_extract.day_keys),
    )
    logger.info(
        "document_analysis_agent.product_spec_links",
        matched=sum(1 for item in product_spec_links if item.status == "matched"),
        total=len(product_spec_links),
    )
    logger.info(
        "document_analysis_agent.spec_materials_merged",
        usages=len(material_usages),
        unique=len(merged_nomenclatures),
        price_matched=price_matched,
        stock_matched=stock_matched,
        stock_files=stock_files,
        shipment_files=shipment_files,
        receipts_nonzero=receipts_nonzero,
        forecast_deficit_rows=forecast_deficit,
        logistics_risk_items=sum(len(stage.items) for stage in logistics_risks.stages),
        result_bytes=len(result_xlsx) if result_xlsx else 0,
    )
    return AveonAnalysisResult(
        roles=role_map,
        source=source,
        production_schedule_files=schedule_files,
        production_schedule_products=products,
        production_schedule_plans=schedule_plans,
        detailed_production_schedule_files=detailed_extract.files,
        detailed_schedule_month=(
            f"{detailed_extract.year:04d}-{detailed_extract.month:02d}"
            if detailed_extract.year and detailed_extract.month
            else ""
        ),
        product_spec_links=product_spec_links,
        material_usages=material_usages,
        merged_nomenclatures=merged_nomenclatures,
        result_xlsx_bytes=result_xlsx,
        stock_files=stock_files,
        shipment_files=shipment_files,
        logistics_risks=logistics_risks,
    )


async def classify_aveon_excel_files(
    workbooks: list[UploadedWorkbook],
) -> tuple[dict[str, WorkbookRole], str]:
    """Только определение ролей файлов (без полного пайплайна анализа)."""
    previews = await asyncio.to_thread(_build_workbook_previews, workbooks)
    role_map, source = await _classify_workbooks_with_lm(previews)
    logger.info(
        "document_analysis_agent.roles_classified_only",
        source=source,
        roles=role_map,
    )
    return role_map, source


def _build_workbook_previews(workbooks: list[UploadedWorkbook]) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for uploaded in workbooks:
        workbook = load_workbook(BytesIO(uploaded.content), data_only=True, read_only=True)
        sheet_previews: list[dict[str, Any]] = []
        for sheet in workbook.worksheets[:8]:
            rows: list[list[str | None]] = []
            for row in sheet.iter_rows(
                min_row=1,
                max_row=min(sheet.max_row, 12),
                max_col=min(sheet.max_column, 14),
                values_only=True,
            ):
                rows.append([_short(_clean_text(value), 120) if value is not None else None for value in row])
            sheet_previews.append(
                {
                    "sheet": sheet.title,
                    "max_row": sheet.max_row,
                    "max_column": sheet.max_column,
                    "sample_rows": rows,
                }
            )
        previews.append({"filename": uploaded.filename, "sheets": sheet_previews})
    return previews


async def _classify_workbooks_with_lm(previews: list[dict[str, Any]]) -> tuple[dict[str, WorkbookRole], str]:
    lm_role_map = await _try_lm_classify_workbooks(previews) or {}
    resolved: dict[str, WorkbookRole] = {}
    for preview in previews:
        filename = str(preview["filename"])
        role = lm_role_map.get(filename)
        if role not in KNOWN_ROLES:
            role = _classify_preview_locally(preview)
        resolved[filename] = role

    required_roles = {
        ROLE_SPECIFICATION,
        ROLE_STOCK,
        ROLE_PRODUCTION_SCHEDULE,
        ROLE_SHIPMENT_SCHEDULE,
    }
    missing_roles = required_roles - set(resolved.values())
    if missing_roles:
        for preview in previews:
            filename = str(preview["filename"])
            local_role = _classify_preview_locally(preview)
            if local_role in missing_roles:
                resolved[filename] = local_role
                missing_roles.discard(local_role)
            if not missing_roles:
                break

    reconciled = _reconcile_role_map_from_content(previews, resolved)
    source = "lm_studio" if lm_role_map else "local_parser"
    return reconciled, source


def _preview_text(preview: dict[str, Any]) -> str:
    return _normalize(json.dumps(preview, ensure_ascii=False))


def _preview_looks_like_shipment_schedule(preview: dict[str, Any]) -> bool:
    text = _preview_text(preview)
    if "график отгрузок" in text or "график отгрузки" in text:
        return True
    filename = _normalize(preview.get("filename"))
    if "отгруз" in filename:
        return True
    # расширенный график: номенклатура + даты поставки / логистика
    if "номенклатура" in text and (
        "примерная дата поставки" in text
        or ("дата заказа" in text and "логистика" in text)
        or ("заказано" in text and "поставк" in text)
    ):
        return True
    return False


def _preview_has_material_stock_table(preview: dict[str, Any]) -> bool:
    """Таблица остатков материалов: «Номенклатура» + «Остаток…» (не остаток ГП в графике выпуска)."""
    text = _preview_text(preview)
    if "ведомость по товарам" in text or "конечный остаток" in text:
        return True
    has_nomenclature = "номенклатура" in text or "наименование тмц" in text
    has_stock_col = (
        "остаток на" in text
        or "остаток," in text
        or " остаток" in text
        or "ост." in text
        or "конечный остаток" in text
        or "начальный остаток" in text
    )
    # Гибрид «план сверху + остатки снизу»: приоритет остатков для агента
    return has_nomenclature and has_stock_col


def _preview_looks_like_detailed_production_schedule(preview: dict[str, Any]) -> bool:
    """Детальный график: по дням/неделям. Не путать с остатками и помесячным графиком."""
    if _preview_looks_like_shipment_schedule(preview):
        return False
    # Файл с таблицей остатков материалов (даже если сверху есть кусок плана) → не детальный
    if _preview_has_material_stock_table(preview):
        return False

    text = _preview_text(preview)
    filename = _normalize(preview.get("filename"))

    if "детальный график" in text or "детальный план" in text:
        return True
    if "недел" in filename or "по дням" in filename:
        return True
    # «План по недельно»: график выпуска ГП с колонками-днями
    if "график выпуска" in text:
        return True
    # Отчёт план/факт по дням: «Модель / изделие» + план/факт
    if ("модель" in text and "изделие" in text) and ("факт" in text):
        return True

    day_date_hits = len(_DAY_DATE_HEADER_RE.findall(text))
    week_word_hits = text.count("неделя") + text.count("недели")
    period_hits = len(_DETAILED_PERIOD_HEADER_RE.findall(text))
    has_product_rows = any(
        marker in text
        for marker in ("наименование", "сокол", "fpv", "модель", "изделие")
    )
    # Много дневных дат в шапке (01.04, 02.04…) при изделиях
    if day_date_hits >= 5 and has_product_rows and "логистика" not in text:
        return True
    # Явные недельные колонки без таблицы материалов
    if (week_word_hits >= 2 or period_hits >= 3) and has_product_rows and "номенклатура" not in text:
        return True
    return False


def _preview_looks_like_production_schedule(preview: dict[str, Any]) -> bool:
    """Обычный (помесячный) график производства."""
    text = _preview_text(preview)
    if "график отгрузок" in text or "график отгрузки" in text:
        return False
    if _preview_looks_like_detailed_production_schedule(preview):
        return False
    if _preview_has_material_stock_table(preview):
        return False
    if "график выпуска" in text:
        return False
    if "неделя" in text or "недели" in text:
        return False
    if "график производства" in text:
        return True
    if "наименования изделий" in text:
        month_hits = sum(1 for month in _MONTH_LOWER if month in text)
        return month_hits >= 2
    return False


def _preview_looks_like_specification(preview: dict[str, Any]) -> bool:
    text = _preview_text(preview)
    return (
        "наименование тмц" in text
        or "цена по спецификации" in text
        or "manufacturer partno" in text
        or ("description" in text and "designator" in text)
        or "спецификация" in text
    )


def _preview_looks_like_stock(preview: dict[str, Any]) -> bool:
    if _preview_looks_like_shipment_schedule(preview):
        return False
    # Сильный детальный график (выпуск/план-факт) важнее слабого слова «остаток» в шапке
    text = _preview_text(preview)
    filename = _normalize(preview.get("filename"))
    if "график выпуска" in text or (("модель" in text and "изделие" in text) and "факт" in text):
        return False
    if "недел" in filename:
        return False
    if _preview_has_material_stock_table(preview):
        return True
    if "остат" in filename and "производ" not in text and "график выпуска" not in text:
        return True
    return False


def _reconcile_role_map_from_content(
    previews: list[dict[str, Any]], role_map: dict[str, WorkbookRole]
) -> dict[str, WorkbookRole]:
    reconciled = dict(role_map)
    for preview in previews:
        filename = str(preview["filename"])
        # Порядок: отгрузки → остатки материалов → детальный → помесячный → спеки
        if _preview_looks_like_shipment_schedule(preview):
            reconciled[filename] = ROLE_SHIPMENT_SCHEDULE
        elif _preview_looks_like_stock(preview):
            reconciled[filename] = ROLE_STOCK
        elif _preview_looks_like_detailed_production_schedule(preview):
            reconciled[filename] = ROLE_DETAILED_PRODUCTION_SCHEDULE
        elif _preview_looks_like_production_schedule(preview):
            reconciled[filename] = ROLE_PRODUCTION_SCHEDULE
        elif _preview_looks_like_specification(preview):
            reconciled[filename] = ROLE_SPECIFICATION
    return reconciled


async def _try_lm_classify_workbooks(previews: list[dict[str, Any]]) -> dict[str, WorkbookRole] | None:
    payload = _lm_settings()
    if payload is None:
        return None
    base_url, model = payload
    prompt = (
        "Ты классифицируешь Excel-файлы для агента закупок Авион. "
        "На входе превью файлов: имя, листы, первые строки. "
        "Для каждого файла выбери одну роль:\n"
        "- shipment_schedule — график отгрузок материалов (номенклатура, логистика, даты поставки в Москву);\n"
        "- stock — остатки МАТЕРИАЛОВ: колонки «Номенклатура» + «Остаток…» "
        "(даже если сверху есть небольшой план изделий — это всё равно stock);\n"
        "- production_schedule — ОБЫЧНЫЙ график производства ПО МЕСЯЦАМ "
        "(колонки Июль/Август/…, «Наименования изделий», без дневной/недельной разбивки);\n"
        "- detailed_production_schedule — ДЕТАЛЬНЫЙ график/отчёт выпуска "
        "(«График выпуска готовой продукции», колонки-дни 1..31 / даты, "
        "или отчёт «Модель/изделие» с план/факт по дням; имя вроде «План по недельно»; "
        "НЕ путать с stock и НЕ путать с помесячным графиком);\n"
        "- specification — спецификация / ведомость материалов;\n"
        "- other.\n"
        "Приоритет: если есть таблица Номенклатура+Остаток материалов → stock. "
        "Детальный график НЕ используется в расчётах агента — только помечается. "
        "Имя файла может врать — смотри на структуру колонок. "
        "Верни строго JSON: {\"files\":[{\"filename\":\"...\",\"role\":\"...\",\"reason\":\"...\"}]}.\n\n"
        f"FILES={json.dumps(previews, ensure_ascii=False)}"
    )
    try:
        data = await _post_lm_json(base_url, model, prompt, timeout=settings.AVEON_LM_STUDIO_TIMEOUT_SECONDS)
        files = data.get("files")
        if not isinstance(files, list):
            return None
        result: dict[str, WorkbookRole] = {}
        for item in files:
            if not isinstance(item, dict):
                continue
            filename = _clean_text(item.get("filename"))
            role = _normalize_lm_role(item.get("role"))
            if filename and role:
                result[filename] = role
        return result or None
    except Exception as exc:
        logger.warning("document_analysis_agent.classification_lm_failed", error=str(exc))
        return None


def _classify_preview_locally(preview: dict[str, Any]) -> WorkbookRole:
    text = _preview_text(preview)
    filename = _normalize(preview.get("filename"))

    if "график отгрузок" in text or "график отгрузки" in text or "отгруз" in filename:
        return ROLE_SHIPMENT_SCHEDULE
    # Остатки материалов — до детального (гибрид «план + остатки» → stock)
    if _preview_looks_like_stock(preview):
        return ROLE_STOCK
    if _preview_looks_like_detailed_production_schedule(preview):
        return ROLE_DETAILED_PRODUCTION_SCHEDULE
    if _preview_looks_like_production_schedule(preview):
        return ROLE_PRODUCTION_SCHEDULE
    if "график производства" in text:
        return ROLE_PRODUCTION_SCHEDULE
    if (
        "наименования изделий" in text
        and sum(1 for month in _MONTH_LOWER if month in text) >= 2
    ):
        return ROLE_PRODUCTION_SCHEDULE
    if (
        "наименование тмц" in text
        or "цена по спецификации" in text
        or "manufacturer partno" in text
        or ("description" in text and "designator" in text)
        or "спецификация" in text
        or "спек" in filename
    ):
        return ROLE_SPECIFICATION
    return ROLE_OTHER


def _normalize_lm_role(value: Any) -> WorkbookRole:
    role = _normalize(value)
    aliases = {
        "specs": ROLE_SPECIFICATION,
        "spec": ROLE_SPECIFICATION,
        "specification": ROLE_SPECIFICATION,
        "спецификация": ROLE_SPECIFICATION,
        "спецификации": ROLE_SPECIFICATION,
        "stock": ROLE_STOCK,
        "inventory": ROLE_STOCK,
        "остатки": ROLE_STOCK,
        "остаток": ROLE_STOCK,
        "production_schedule": ROLE_PRODUCTION_SCHEDULE,
        "monthly_production_schedule": ROLE_PRODUCTION_SCHEDULE,
        "график производства": ROLE_PRODUCTION_SCHEDULE,
        "detailed_production_schedule": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "detailed_schedule": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "weekly_production_schedule": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "daily_production_schedule": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "production_output_schedule": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "детальный график производства": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "детальный план производства": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "график выпуска": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "план по недельно": ROLE_DETAILED_PRODUCTION_SCHEDULE,
        "shipment_schedule": ROLE_SHIPMENT_SCHEDULE,
        "shipping_schedule": ROLE_SHIPMENT_SCHEDULE,
        "delivery_schedule": ROLE_SHIPMENT_SCHEDULE,
        "график отгрузок": ROLE_SHIPMENT_SCHEDULE,
        "график отгрузки": ROLE_SHIPMENT_SCHEDULE,
        "unknown": ROLE_OTHER,
        "other": ROLE_OTHER,
        # общий «schedule» без уточнения — не маппим (иначе путаем типы)
    }
    return aliases.get(role, ROLE_OTHER)


def _lm_settings() -> tuple[str, str] | None:
    base_url = settings.AVEON_LM_STUDIO_BASE_URL.strip().rstrip("/")
    model = settings.AVEON_LM_STUDIO_MODEL.strip()
    if not base_url or not model:
        return None
    return base_url, model


async def _post_lm_json(base_url: str, model: str, prompt: str, timeout: int | float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты возвращаешь только валидный JSON. Не используй markdown и пояснения.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        try:
            return _parse_json_object(content)
        except json.JSONDecodeError:
            repair_response = await client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ты исправляешь ответ в валидный JSON без markdown и пояснений.",
                        },
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "Предыдущий ответ был невалидным JSON. "
                                "Верни только исправленный JSON с теми же данными."
                            ),
                        },
                    ],
                    "temperature": 0,
                    "stream": False,
                },
            )
            repair_response.raise_for_status()
            repair_data = repair_response.json()
            repair_content = repair_data["choices"][0]["message"]["content"]
            return _parse_json_object(repair_content)


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def _empty_month_bucket() -> dict[str, dict[str, float]]:
    return {cat: {metric: 0.0 for metric in _SCHEDULE_METRICS} for cat in _SCHEDULE_CATEGORIES}


def _round_qty(value: float) -> float:
    return round(value, 6) if abs(value - round(value)) > 1e-9 else float(round(value))


def _plan_demand_total(month_bucket: dict[str, dict[str, float]] | None) -> float:
    """Сумма плановых потребностей по категориям (для прогноза)."""
    if not month_bucket:
        return 0.0
    total = 0.0
    for category in _SCHEDULE_CATEGORIES:
        total += float(month_bucket.get(category, {}).get("план", 0.0))
    return total


def _monthly_demand_has_nonzero(demand: dict[str, dict[str, dict[str, float]]]) -> bool:
    return any(
        float(qty) > 0
        for month_bucket in demand.values()
        for metrics in month_bucket.values()
        for qty in metrics.values()
    )


def _classify_schedule_category(value: Any) -> str | None:
    text = _normalize(value)
    if not text:
        return None
    if "опытн" in text:
        return "опытные"
    if text == "заказ" or text.startswith("заказ"):
        return "заказ"
    if text == "склад" or text.startswith("склад"):
        return "склад"
    return None


def _classify_schedule_metric(value: Any) -> str | None:
    text = _normalize(value)
    if text == "план" or text.startswith("план"):
        return "план"
    if text == "факт" or text.startswith("факт"):
        return "факт"
    return None


def _sheet_cell_value(sheet: Worksheet, row: int, col: int) -> Any:
    """Значение ячейки с учётом merge (значение только в левом верхнем углу)."""
    value = sheet.cell(row, col).value
    if value is not None and value != "":
        return value
    for merged in sheet.merged_cells.ranges:
        if merged.min_row <= row <= merged.max_row and merged.min_col <= col <= merged.max_col:
            return sheet.cell(merged.min_row, merged.min_col).value
    return value


def _filled_month_labels(sheet: Worksheet, row: int, max_col: int) -> dict[int, str]:
    """Месяц для каждой колонки (propagate из merge / влево)."""
    labels: dict[int, str] = {}
    last = ""
    for col in range(1, max_col + 1):
        month_num = _month_number_from_header(_normalize(_sheet_cell_value(sheet, row, col)))
        if month_num is not None:
            last = _MONTH_NOMINATIVE[month_num - 1]
        if last:
            labels[col] = last
    return labels


def _find_production_schedule_layout(sheet: Worksheet) -> ScheduleTableLayout | None:
    """Ищет помесячную таблицу: split (3 уровня) или legacy (1 qty на месяц)."""
    max_col = min(sheet.max_column or 1, 80)
    max_scan = min(sheet.max_row or 1, 40)
    legacy_candidate: ScheduleTableLayout | None = None

    for month_row in range(1, max_scan + 1):
        headers = {
            col: _normalize(_sheet_cell_value(sheet, month_row, col))
            for col in range(1, max_col + 1)
        }
        name_col = _pick_column(
            headers,
            [
                "наименования изделий",
                "наименование изделий",
                "наименование",
                "изделие",
                "контракты / изделия",
                "контракты/изделия",
            ],
        )
        month_labels = _filled_month_labels(sheet, month_row, max_col)
        distinct_months = list(dict.fromkeys(month_labels.values()))
        if name_col is None or len(distinct_months) < 2:
            continue

        # --- split: месяц / категория / план|факт ---
        cat_row = month_row + 1
        metric_row = month_row + 2
        if metric_row <= (sheet.max_row or 0):
            split_cols: list[ScheduleQtyColumn] = []
            last_category: str | None = None
            last_category_month: str | None = None
            for col in range(1, max_col + 1):
                month = month_labels.get(col)
                if not month:
                    last_category = None
                    last_category_month = None
                    continue
                category = _classify_schedule_category(
                    _sheet_cell_value(sheet, cat_row, col)
                )
                if category is not None:
                    last_category = category
                    last_category_month = month
                elif last_category is not None and last_category_month == month:
                    category = last_category
                metric = _classify_schedule_metric(
                    _sheet_cell_value(sheet, metric_row, col)
                )
                if category is None or metric is None:
                    continue
                split_cols.append(
                    ScheduleQtyColumn(
                        col=col, month=month, category=category, metric=metric
                    )
                )
            if len(split_cols) >= 6:
                return ScheduleTableLayout(
                    name_col=name_col,
                    data_start_row=metric_row + 1,
                    columns=split_cols,
                    is_split=True,
                )

        # --- legacy: одна колонка на месяц → заказ/план ---
        month_cols: list[ScheduleQtyColumn] = []
        seen_month: set[str] = set()
        for col, month in month_labels.items():
            header = headers.get(col, "")
            if _month_number_from_header(header) is None:
                continue
            if month in seen_month:
                continue
            seen_month.add(month)
            month_cols.append(
                ScheduleQtyColumn(col=col, month=month, category="заказ", metric="план")
            )
        if len(month_cols) >= 2 and legacy_candidate is None:
            data_cols = [item.col for item in month_cols]
            legacy_candidate = ScheduleTableLayout(
                name_col=name_col,
                data_start_row=month_row + 1,
                columns=month_cols,
                is_split=False,
            )
            # продолжаем искать split на других строках/не берём сразу
            _ = data_cols

    return legacy_candidate


def _extract_production_schedule_products(
    workbooks: list[UploadedWorkbook], role_map: dict[str, WorkbookRole]
) -> tuple[list[str], list[ScheduleProductPlan]]:
    schedule_files = [
        uploaded.filename
        for uploaded in workbooks
        if role_map.get(uploaded.filename) == ROLE_PRODUCTION_SCHEDULE
    ]
    plans: list[ScheduleProductPlan] = []
    seen: set[str] = set()

    for uploaded in workbooks:
        if role_map.get(uploaded.filename) != ROLE_PRODUCTION_SCHEDULE:
            continue
        workbook = load_workbook(BytesIO(uploaded.content), data_only=True)
        try:
            chosen: tuple[Worksheet, ScheduleTableLayout] | None = None
            legacy: tuple[Worksheet, ScheduleTableLayout] | None = None
            for sheet in workbook.worksheets:
                layout = _find_production_schedule_layout(sheet)
                if layout is None:
                    continue
                if layout.is_split:
                    chosen = (sheet, layout)
                    break
                if legacy is None:
                    legacy = (sheet, layout)
            if chosen is None:
                chosen = legacy
            if chosen is None:
                continue

            sheet, layout = chosen
            data_cols = [item.col for item in layout.columns]
            for row_idx in range(layout.data_start_row, sheet.max_row + 1):
                product_name = _clean_text(sheet.cell(row_idx, layout.name_col).value)
                if not _is_schedule_product_name(product_name):
                    continue
                if not any(
                    _cell_has_number(sheet.cell(row_idx, col_idx).value)
                    for col_idx in data_cols
                ):
                    continue
                key = _normalize(product_name)
                if key in seen:
                    continue
                seen.add(key)
                monthly_qty: dict[str, dict[str, dict[str, float]]] = {}
                for item in layout.columns:
                    bucket = monthly_qty.setdefault(item.month, _empty_month_bucket())
                    qty = _to_float(sheet.cell(row_idx, item.col).value)
                    bucket[item.category][item.metric] = float(qty) if qty is not None else 0.0
                plans.append(ScheduleProductPlan(product=product_name, monthly_qty=monthly_qty))
            logger.info(
                "document_analysis_agent.production_schedule_layout",
                file=uploaded.filename,
                sheet=sheet.title,
                split=layout.is_split,
                columns=len(layout.columns),
                products=len(plans),
            )
        finally:
            workbook.close()

    return schedule_files, plans


def _extract_detailed_production_schedule(
    workbooks: list[UploadedWorkbook],
    role_map: dict[str, WorkbookRole],
    as_of: date | None = None,
) -> DetailedScheduleExtract:
    """Извлекает дневной план изделий из файлов роли detailed_production_schedule."""
    as_of_day = as_of or date.today()
    files = [
        uploaded.filename
        for uploaded in workbooks
        if role_map.get(uploaded.filename) == ROLE_DETAILED_PRODUCTION_SCHEDULE
    ]
    if not files:
        year, month = as_of_day.year, as_of_day.month
        return DetailedScheduleExtract(
            files=[],
            plans=[],
            year=year,
            month=month,
            day_keys=_month_day_keys(year, month),
        )

    sheet_candidates: list[tuple[int, int, str, Worksheet, list[DetailedScheduleProductPlan]]] = []
    for uploaded in workbooks:
        if role_map.get(uploaded.filename) != ROLE_DETAILED_PRODUCTION_SCHEDULE:
            continue
        workbook = load_workbook(BytesIO(uploaded.content), data_only=True)
        try:
            for sheet in workbook.worksheets:
                year, month = _infer_detailed_sheet_year_month(sheet, as_of_day.year)
                if year <= 0 or month <= 0:
                    continue
                plans = _parse_detailed_schedule_sheet(sheet, year, month)
                if not plans and not _sheet_has_daily_day_columns(sheet, year, month):
                    continue
                sheet_candidates.append((year, month, uploaded.filename, sheet, plans))
        finally:
            workbook.close()

    if not sheet_candidates:
        year, month = as_of_day.year, as_of_day.month
        logger.warning(
            "document_analysis_agent.detailed_schedule_empty",
            files=files,
            fallback_month=f"{year:04d}-{month:02d}",
        )
        return DetailedScheduleExtract(
            files=files,
            plans=[],
            year=year,
            month=month,
            day_keys=_month_day_keys(year, month),
        )

    chosen = _choose_detailed_schedule_month(sheet_candidates, as_of_day)
    year, month, _filename, _sheet, plans = chosen
    # схлопнуть одноимённые изделия с разных блоков/файлов
    merged_plans = _merge_detailed_product_plans(plans)
    day_keys = _month_day_keys(year, month)
    for plan in merged_plans:
        for day_key in day_keys:
            plan.daily_qty.setdefault(day_key, 0.0)
        plan.year = year
        plan.month = month

    logger.info(
        "document_analysis_agent.detailed_schedule_extracted",
        files=files,
        month=f"{year:04d}-{month:02d}",
        products=len(merged_plans),
        nonzero_days=sum(
            1 for plan in merged_plans for qty in plan.daily_qty.values() if qty > 0
        ),
    )
    return DetailedScheduleExtract(
        files=files,
        plans=merged_plans,
        year=year,
        month=month,
        day_keys=day_keys,
    )


def _month_day_keys(year: int, month: int) -> list[str]:
    if year <= 0 or month <= 0:
        return []
    days = monthrange(year, month)[1]
    return [date(year, month, day).isoformat() for day in range(1, days + 1)]


def _infer_detailed_sheet_year_month(sheet: Worksheet, default_year: int) -> tuple[int, int]:
    """Год/месяц листа: имя листа, заголовок, либо даты в шапке дневных колонок."""
    month = _month_number_from_header(_normalize(sheet.title))
    year = default_year
    title_year = _year_from_text(sheet.title)
    if title_year:
        year = title_year

    for row_idx in range(1, min(sheet.max_row, 12) + 1):
        for col_idx in range(1, min(sheet.max_column, 8) + 1):
            text = _clean_text(sheet.cell(row_idx, col_idx).value)
            if not text:
                continue
            if month is None:
                month = _month_number_from_header(_normalize(text))
            found_year = _year_from_text(text)
            if found_year:
                year = found_year

    # даты в шапке имеют приоритет для месяца/года
    for header_idx, _name_col, day_cols in _iter_detailed_schedule_tables(sheet, year, month or 1):
        if not day_cols:
            continue
        first_day = day_cols[0][1]
        return first_day.year, first_day.month

    if month is None:
        return 0, 0
    return year, month


def _year_from_text(value: Any) -> int | None:
    text = _clean_text(value)
    match = re.search(r"(20\d{2})", text)
    if not match:
        return None
    return int(match.group(1))


def _sheet_has_daily_day_columns(sheet: Worksheet, year: int, month: int) -> bool:
    for _header_idx, _name_col, day_cols in _iter_detailed_schedule_tables(sheet, year, month):
        if day_cols:
            return True
    return False


def _choose_detailed_schedule_month(
    candidates: list[tuple[int, int, str, Worksheet, list[DetailedScheduleProductPlan]]],
    as_of: date,
) -> tuple[int, int, str, Worksheet, list[DetailedScheduleProductPlan]]:
    """Месяц asOf → ближайший будущий → первый кандидат."""
    # уникальные (year, month) — берём планы со всех листов этого месяца
    by_month: dict[tuple[int, int], list[tuple[int, int, str, Worksheet, list[DetailedScheduleProductPlan]]]] = {}
    for item in candidates:
        by_month.setdefault((item[0], item[1]), []).append(item)

    target = (as_of.year, as_of.month)
    if target in by_month:
        return _flatten_month_candidates(by_month[target])

    future = sorted(
        [(y, m) for (y, m) in by_month if (y, m) > target],
    )
    if future:
        return _flatten_month_candidates(by_month[future[0]])

    past_or_any = sorted(by_month.keys())
    return _flatten_month_candidates(by_month[past_or_any[0]])


def _flatten_month_candidates(
    items: list[tuple[int, int, str, Worksheet, list[DetailedScheduleProductPlan]]],
) -> tuple[int, int, str, Worksheet, list[DetailedScheduleProductPlan]]:
    year, month, filename, sheet, _ = items[0]
    plans: list[DetailedScheduleProductPlan] = []
    for item in items:
        plans.extend(item[4])
    return year, month, filename, sheet, plans


def _merge_detailed_product_plans(
    plans: list[DetailedScheduleProductPlan],
) -> list[DetailedScheduleProductPlan]:
    merged: dict[str, DetailedScheduleProductPlan] = {}
    for plan in plans:
        key = _normalize(plan.product)
        existing = merged.get(key)
        if existing is None:
            merged[key] = DetailedScheduleProductPlan(
                product=plan.product,
                daily_qty=dict(plan.daily_qty),
                year=plan.year,
                month=plan.month,
            )
            continue
        for day_key, qty in plan.daily_qty.items():
            existing.daily_qty[day_key] = existing.daily_qty.get(day_key, 0.0) + float(qty)
    return list(merged.values())


def _iter_detailed_schedule_tables(
    sheet: Worksheet,
    year: int,
    month: int,
) -> list[tuple[int, int, list[tuple[int, date]]]]:
    """Все блоки таблицы на листе: (header_row, name_col, [(col, date), …])."""
    tables: list[tuple[int, int, list[tuple[int, date]]]] = []
    max_scan = min(sheet.max_row, 120)
    for header_idx in range(1, max_scan + 1):
        name_col = None
        day_cols: list[tuple[int, date]] = []
        for col_idx in range(1, sheet.max_column + 1):
            value = sheet.cell(header_idx, col_idx).value
            text = _normalize(value)
            if name_col is None and text in {
                "наименование",
                "наимнование",
                "наименования",
                "наименования изделий",
                "наименование изделий",
                "изделие",
            }:
                name_col = col_idx
                continue
            day_date = _detailed_header_to_day(value, year, month)
            if day_date is not None:
                day_cols.append((col_idx, day_date))

        # шапка дат может быть на строке ниже «Наименование» (как в Апреле)
        if name_col is not None and not day_cols and header_idx < max_scan:
            for col_idx in range(1, sheet.max_column + 1):
                day_date = _detailed_header_to_day(
                    sheet.cell(header_idx + 1, col_idx).value, year, month
                )
                if day_date is not None:
                    day_cols.append((col_idx, day_date))
            if day_cols:
                tables.append((header_idx + 1, name_col, day_cols))
                continue

        if name_col is not None and len(day_cols) >= 2:
            tables.append((header_idx, name_col, day_cols))
    return tables


def _detailed_header_to_day(value: Any, year: int, month: int) -> date | None:
    """Колонка дня: дата, либо номер 1..31; skip Итог/План/Отклонение/Остаток."""
    text = _normalize(value)
    if text:
        if text in _DETAILED_DAY_SKIP_HEADERS:
            return None
        if re.fullmatch(r"\d{1,2}", text) is None:
            if any(
                token in text
                for token in ("итог", "факт", "оклон", "отклон", "остаток")
            ):
                return None
            if text == "план" or text.startswith("план "):
                return None

    # datetime/date в шапке (лист «Апрель») — до разбора чисел 1..31
    if isinstance(value, datetime):
        parsed = value.date()
        if year > 0 and month > 0 and (parsed.year != year or parsed.month != month):
            return None
        return parsed
    if isinstance(value, date):
        if year > 0 and month > 0 and (value.year != year or value.month != month):
            return None
        return value

    # Номер дня месяца: НЕ через from_excel(1) → 1899/1900
    day_num: int | None = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if float(value).is_integer():
            candidate = int(value)
            if 1 <= candidate <= 31:
                day_num = candidate
    else:
        text_raw = _clean_text(value)
        if re.fullmatch(r"\d{1,2}", text_raw):
            day_num = int(text_raw)

    if day_num is not None:
        if year <= 0 or month <= 0:
            return None
        max_day = monthrange(year, month)[1]
        if day_num > max_day:
            return None
        return date(year, month, day_num)

    # Строковые даты «2026-04-01» / «01.04.2026»
    parsed = _header_value_to_date(value)
    if parsed is None:
        return None
    if year > 0 and month > 0 and (parsed.year != year or parsed.month != month):
        return None
    return parsed


def _parse_detailed_schedule_sheet(
    sheet: Worksheet,
    year: int,
    month: int,
) -> list[DetailedScheduleProductPlan]:
    plans: list[DetailedScheduleProductPlan] = []
    seen_rows: set[tuple[int, str]] = set()
    tables = _iter_detailed_schedule_tables(sheet, year, month)
    for header_idx, name_col, day_cols in tables:
        # строки данных до следующего блока шапки или пустой зоны
        next_headers = sorted(h for h, _, _ in tables if h > header_idx)
        end_row = next_headers[0] if next_headers else sheet.max_row + 1
        # если шапка дат на header_idx, а «Наименование» на строке выше — данные с header_idx+1
        start_row = header_idx + 1
        for row_idx in range(start_row, end_row):
            product_name = _clean_text(sheet.cell(row_idx, name_col).value)
            if not _is_schedule_product_name(product_name):
                continue
            # пропуск повторных шапок «Наименование»
            if _normalize(product_name) in {
                "наименование",
                "наимнование",
                "наименования",
            }:
                continue
            row_key = (row_idx, _normalize(product_name))
            if row_key in seen_rows:
                continue
            # есть ли хоть одно число в дневных колонках (или текстовый мусор — skip qty)
            has_qty = False
            daily_qty: dict[str, float] = {}
            for col_idx, day in day_cols:
                raw = sheet.cell(row_idx, col_idx).value
                qty = _to_float(raw)
                if qty is None:
                    # текст вроде «Заказ комплектующих» — 0
                    daily_qty[day.isoformat()] = 0.0
                    continue
                has_qty = has_qty or qty != 0
                daily_qty[day.isoformat()] = float(qty)
            if not daily_qty:
                continue
            # строка без чисел вообще (кроме полностью пустой) всё равно берём с нулями,
            # если это похоже на изделие и рядом есть № п/п
            pp_val = sheet.cell(row_idx, 1).value
            if not has_qty and not _cell_has_number(pp_val):
                continue
            seen_rows.add(row_key)
            plans.append(
                DetailedScheduleProductPlan(
                    product=product_name,
                    daily_qty=daily_qty,
                    year=year,
                    month=month,
                )
            )
    return plans


def _find_schedule_table(
    sheet: Worksheet,
) -> tuple[int, int, list[int], list[tuple[int, str]]] | None:
    for header_idx in range(1, min(sheet.max_row, 40) + 1):
        headers = {
            col_idx: _normalize(sheet.cell(header_idx, col_idx).value)
            for col_idx in range(1, sheet.max_column + 1)
        }
        name_col = _pick_column(
            headers,
            ["наименования изделий", "наименование изделий", "наименование", "изделие"],
        )
        month_cols: list[tuple[int, str]] = []
        for col_idx, header in headers.items():
            month_num = _month_number_from_header(header)
            if month_num is None:
                continue
            month_cols.append((col_idx, _MONTH_NOMINATIVE[month_num - 1]))
        data_cols = [
            col_idx
            for col_idx, header in headers.items()
            if col_idx != name_col
            and (_month_number_from_header(header) or "кол" in header or "итог" in header)
        ]
        if name_col and len(month_cols) >= 2:
            if not data_cols:
                data_cols = [col_idx for col_idx, _ in month_cols]
            return header_idx, name_col, data_cols, month_cols
    return None


def _cell_has_number(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, int | float):
        return True
    text = _clean_text(value).replace(" ", "").replace(",", ".")
    try:
        float(text)
        return True
    except ValueError:
        return False


def _month_number_from_header(header: str) -> int | None:
    normalized = _normalize(header)
    if not normalized:
        return None
    for idx, name in enumerate(_MONTH_NOMINATIVE):
        month_key = _normalize(name)
        if normalized == month_key or normalized.startswith(month_key):
            return idx + 1
    for idx, name in enumerate(_MONTH_LOWER):
        if normalized == name or normalized.startswith(name):
            return idx + 1
    return None


def _pick_column(headers: dict[int, str], candidates: list[str]) -> int | None:
    normalized_candidates = [_normalize(candidate) for candidate in candidates]
    for candidate in normalized_candidates:
        for col_idx, header in headers.items():
            if header == candidate:
                return col_idx
    for candidate in normalized_candidates:
        for col_idx, header in headers.items():
            if candidate and candidate in header:
                return col_idx
    return None


def _is_schedule_product_name(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower().strip()
    if lowered.startswith("итого"):
        return False
    if lowered in {"наименование", "наименования изделий", "изделие"}:
        return False
    if any(token in lowered for token in ("руководитель", "начальник", "подпись", "утверждаю")):
        return False
    return len(value) > 1


async def _resolve_schedule_products_to_specs(products: list[str]) -> list[ProductSpecLink]:
    mapping_rows = await asyncio.to_thread(_load_nomenclature_mapping)
    sheet_names = await asyncio.to_thread(_load_spec_sheet_names)
    if not products:
        return []
    if not mapping_rows or not sheet_names:
        logger.warning(
            "document_analysis_agent.spec_resolve_skipped",
            mapping_exists=_MAPPING_FILE.exists(),
            specs_exists=_SPECS_FILE.exists(),
            mapping_rows=len(mapping_rows),
            sheets=len(sheet_names),
        )
        return [
            ProductSpecLink(
                schedule_product=product,
                status="unmatched",
                reason="Нет файла сопоставления или спецификаций в data/aveon",
            )
            for product in products
        ]

    unique_nomenclatures = list(dict.fromkeys(row.nomenclature for row in mapping_rows))
    local_nomenclature_map = _match_schedule_to_nomenclatures_locally(products, mapping_rows)
    ambiguous = [
        product
        for product in products
        if local_nomenclature_map.get(product) is None
        or local_nomenclature_map[product][1] < 0.62
    ]
    lm_nomenclature_map = await _match_schedule_to_nomenclatures_with_lm(
        ambiguous, mapping_rows
    )

    links: list[ProductSpecLink] = []
    for product in products:
        nomenclature: str | None = None
        reason = ""
        local = local_nomenclature_map.get(product)
        if local and local[1] >= 0.62:
            nomenclature, score = local[0], local[1]
            reason = f"локальный матч номенклатуры ({score:.2f})"
        elif product in lm_nomenclature_map and lm_nomenclature_map[product]:
            nomenclature = lm_nomenclature_map[product]
            reason = "LM Studio: номенклатура"
        elif local and local[0]:
            nomenclature, score = local[0], local[1]
            reason = f"слабый локальный матч номенклатуры ({score:.2f})"

        if not nomenclature:
            links.append(
                ProductSpecLink(
                    schedule_product=product,
                    status="no_mapping",
                    reason="Не найдена номенклатура в таблице сопоставления",
                )
            )
            continue

        if nomenclature not in unique_nomenclatures:
            # LM мог вернуть близкое имя — подтянуть к ближайшему из mapping
            best_name, best_score = _best_text_match(nomenclature, unique_nomenclatures)
            if best_name and best_score >= 0.55:
                nomenclature = best_name
                reason = f"{reason}; нормализовано к mapping"

        sheet, sheet_reason = _match_nomenclature_to_sheet(product, nomenclature, sheet_names)
        if not sheet:
            lm_sheet = await _match_nomenclature_to_sheet_with_lm(nomenclature, sheet_names)
            if lm_sheet:
                sheet = lm_sheet
                sheet_reason = "LM Studio: лист спецификации"

        if not sheet:
            links.append(
                ProductSpecLink(
                    schedule_product=product,
                    nomenclature=nomenclature,
                    status="no_sheet",
                    reason=f"{reason}; лист не найден",
                )
            )
            continue

        links.append(
            ProductSpecLink(
                schedule_product=product,
                nomenclature=nomenclature,
                spec_sheet=sheet,
                status="matched",
                reason=f"{reason}; {sheet_reason}".strip("; "),
            )
        )
    return links


def _collect_and_merge_spec_materials(
    links: list[ProductSpecLink],
    workbooks: list[UploadedWorkbook],
    role_map: dict[str, WorkbookRole],
    schedule_plans: list[ScheduleProductPlan],
    detailed_extract: DetailedScheduleExtract | None = None,
) -> tuple[
    list[SpecMaterialItem],
    list[MergedNomenclatureRow],
    bytes | None,
    list[str],
    list[str],
    LogisticsRiskBoard,
]:
    """Разбор листов → merge → цены → остатки → потребность → поступления → риски → result.xlsx."""
    if detailed_extract is None:
        detailed_extract = DetailedScheduleExtract(files=[], plans=[], year=0, month=0)
    usages = _extract_materials_from_matched_specs(links)
    merged = _merge_material_usages(usages)
    _enrich_merged_with_purchase_prices(merged)
    stock_files = _enrich_merged_with_stock(merged, workbooks, role_map)
    _enrich_merged_with_monthly_demand(merged, schedule_plans)
    shipment_files = _enrich_merged_with_monthly_receipts(merged, workbooks, role_map)
    _enrich_merged_with_monthly_forecast(merged)
    _enrich_merged_with_daily_demand(merged, detailed_extract)
    _enrich_merged_with_daily_receipts(merged, workbooks, role_map, detailed_extract)
    _enrich_merged_with_daily_forecast(merged, detailed_extract)
    logistics_risks = _build_logistics_risk_board(merged, workbooks, role_map)
    result_bytes = _build_result_xlsx(merged, detailed_extract)
    return usages, merged, result_bytes, stock_files, shipment_files, logistics_risks


def _extract_materials_from_matched_specs(
    links: list[ProductSpecLink],
) -> list[SpecMaterialItem]:
    matched = [link for link in links if link.status == "matched" and link.spec_sheet]
    if not matched:
        return []
    if not _SPECS_FILE.exists():
        logger.warning("document_analysis_agent.specs_file_missing", path=str(_SPECS_FILE))
        return []

    workbook = load_workbook(_SPECS_FILE, data_only=True, read_only=True)
    try:
        sheet_lookup = {name: name for name in workbook.sheetnames}
        sheet_lookup_norm = {_normalize(name): name for name in workbook.sheetnames}

        usages: list[SpecMaterialItem] = []
        for link in matched:
            sheet_name = link.spec_sheet or ""
            resolved = sheet_lookup.get(sheet_name) or sheet_lookup_norm.get(
                _normalize(sheet_name)
            )
            if not resolved:
                logger.warning(
                    "document_analysis_agent.spec_sheet_missing_in_workbook",
                    sheet=sheet_name,
                    product=link.schedule_product,
                )
                continue
            worksheet = workbook[resolved]
            sheet_items = _parse_spec_sheet_materials(
                worksheet,
                product=link.schedule_product,
                sheet_name=resolved,
            )
            usages.extend(sheet_items)
            logger.info(
                "document_analysis_agent.spec_sheet_parsed",
                product=link.schedule_product,
                sheet=resolved,
                materials=len(sheet_items),
            )
        return usages
    finally:
        workbook.close()


def _parse_spec_sheet_materials(
    worksheet: Worksheet,
    *,
    product: str,
    sheet_name: str,
) -> list[SpecMaterialItem]:
    rows = list(worksheet.iter_rows(values_only=True))
    if not rows:
        return []

    header_row_idx, name_col, qty_col, unit_col = _detect_spec_header(rows)
    if header_row_idx is None or name_col is None:
        return []

    items: list[SpecMaterialItem] = []
    for row in rows[header_row_idx + 1 :]:
        if name_col >= len(row):
            continue
        name = _clean_text(row[name_col])
        if not name or _normalize(name) in {"наименование", "номенклатура"}:
            continue
        if _SECTION_NAME_RE.match(name):
            continue
        if _is_spec_root_product_row(name, product, sheet_name, row, qty_col):
            continue

        quantity = _to_float(row[qty_col]) if qty_col is not None and qty_col < len(row) else None
        unit = None
        if unit_col is not None and unit_col < len(row):
            unit = _clean_text(row[unit_col]) or None

        items.append(
            SpecMaterialItem(
                nomenclature=name,
                quantity=quantity,
                product=product,
                unit=unit,
                spec_sheet=sheet_name,
            )
        )
    return items


def _detect_spec_header(
    rows: list[tuple[Any, ...]],
) -> tuple[int | None, int | None, int | None, int | None]:
    """Ищет строку заголовка: Наименование + Количество (+ ед. изм.)."""
    for idx, row in enumerate(rows[:20]):
        name_col: int | None = None
        qty_col: int | None = None
        unit_col: int | None = None
        for col_idx, value in enumerate(row):
            text = _normalize(value)
            if not text:
                continue
            if name_col is None and ("наименование" in text or text == "номенклатура"):
                name_col = col_idx
            elif qty_col is None and "количество" in text:
                qty_col = col_idx
            elif unit_col is None and ("ед" in text and "изм" in text):
                unit_col = col_idx
        if name_col is not None and qty_col is not None:
            return idx, name_col, qty_col, unit_col
        if name_col is not None and qty_col is None:
            # редкий случай: количество без явного заголовка рядом — угадываем позже
            inferred_qty = _infer_qty_column(rows, idx, name_col)
            if inferred_qty is not None:
                return idx, name_col, inferred_qty, unit_col
    return None, None, None, None


def _infer_qty_column(
    rows: list[tuple[Any, ...]],
    header_idx: int,
    name_col: int,
) -> int | None:
    scores: dict[int, int] = {}
    for row in rows[header_idx + 1 : header_idx + 40]:
        if name_col >= len(row) or not _clean_text(row[name_col]):
            continue
        for col_idx, value in enumerate(row):
            if col_idx == name_col:
                continue
            if _to_float(value) is not None:
                scores[col_idx] = scores.get(col_idx, 0) + 1
    if not scores:
        return None
    return max(scores.items(), key=lambda item: item[1])[0]


def _is_spec_root_product_row(
    name: str,
    product: str,
    sheet_name: str,
    row: tuple[Any, ...],
    qty_col: int | None,
) -> bool:
    """Корневая строка изделия в выгрузке 1С (НСУ и т.п.), не материал."""
    quantity = _to_float(row[qty_col]) if qty_col is not None and qty_col < len(row) else None
    if quantity not in (1, 1.0):
        return False
    if len(name) > 80:
        return False
    sheet_score = _product_match_score(name, sheet_name)
    product_score = _product_match_score(name, product)
    if max(sheet_score, product_score) >= 0.72:
        return True
    # короткие корни вроде «НСУ», «НСУ 2.0»
    compact = _match_key(name)
    if compact in {_match_key(sheet_name), _match_key(product)}:
        return True
    if len(compact) <= 12 and (
        compact in _match_key(sheet_name) or compact in _match_key(product)
    ):
        return True
    return False


def _merge_material_usages(usages: list[SpecMaterialItem]) -> list[MergedNomenclatureRow]:
    """Сводит поиздельные позиции в уникальные номенклатуры.

    - ключ уникальности: нормализованное наименование;
    - изделия перечисляются без повторов;
    - количество: сумма внутри одного изделия, при совпадении между изделиями —
      общее значение, иначе None (разбивка остаётся в by_product).
    """
    # product -> nomenclature_key -> aggregate
    per_product: dict[str, dict[str, dict[str, Any]]] = {}
    display_names: dict[str, str] = {}
    units: dict[str, str | None] = {}

    for item in usages:
        key = _normalize(item.nomenclature)
        if not key:
            continue
        display_names.setdefault(key, item.nomenclature)
        if key not in units and item.unit:
            units[key] = item.unit
        product_bucket = per_product.setdefault(item.product, {})
        bucket = product_bucket.setdefault(key, {"quantity": None})
        if item.quantity is not None:
            current = bucket["quantity"]
            bucket["quantity"] = item.quantity if current is None else float(current) + float(item.quantity)

    merged_map: dict[str, MergedNomenclatureRow] = {}
    for product, materials in per_product.items():
        for key, payload in materials.items():
            qty = payload["quantity"]
            if key not in merged_map:
                merged_map[key] = MergedNomenclatureRow(
                    nomenclature=display_names[key],
                    products=[product],
                    quantity=qty if isinstance(qty, (int, float)) else None,
                    unit=units.get(key),
                    by_product={product: qty if isinstance(qty, (int, float)) else None},
                )
                continue
            row = merged_map[key]
            if product not in row.products:
                row.products.append(product)
            row.by_product[product] = qty if isinstance(qty, (int, float)) else None
            known = [value for value in row.by_product.values() if value is not None]
            if not known:
                row.quantity = None
            elif all(abs(value - known[0]) < 1e-9 for value in known):
                row.quantity = known[0]
            else:
                row.quantity = None

    rows = list(merged_map.values())
    rows.sort(key=lambda item: _normalize(item.nomenclature))
    for row in rows:
        row.products = sorted(row.products, key=_normalize)
    return rows


def _load_purchase_price_index() -> dict[str, PurchasePriceEntry]:
    """Индекс закупочных цен: ключ = нормализованная номенклатура.

    При нескольких закупках одной позиции берём строку с максимальным оборотом
    (КоличествоОборот) — наиболее представительный контрагент и цена.
    """
    if not _PRICES_FILE.exists():
        logger.warning("document_analysis_agent.prices_file_missing", path=str(_PRICES_FILE))
        return {}

    workbook = load_workbook(_PRICES_FILE, data_only=True, read_only=True)
    try:
        worksheet = workbook.active
        rows = list(worksheet.iter_rows(values_only=True))
    finally:
        workbook.close()

    header_idx = None
    name_col = supplier_col = qty_col = price_col = None
    for idx, row in enumerate(rows[:15]):
        normalized = [_normalize(value) for value in row]
        for col_idx, text in enumerate(normalized):
            if not text:
                continue
            if name_col is None and ("номенклатура" in text):
                name_col = col_idx
            elif supplier_col is None and ("контрагент" in text or "поставщик" in text):
                supplier_col = col_idx
            elif qty_col is None and "количество" in text:
                qty_col = col_idx
            elif price_col is None and text.startswith("цена"):
                price_col = col_idx
        if name_col is not None and supplier_col is not None and price_col is not None:
            header_idx = idx
            break

    if header_idx is None or name_col is None or price_col is None:
        logger.warning("document_analysis_agent.prices_header_not_found")
        return {}

    index: dict[str, PurchasePriceEntry] = {}
    for row in rows[header_idx + 1 :]:
        if name_col >= len(row):
            continue
        name = _clean_text(row[name_col]).rstrip("*").strip()
        if not name:
            continue
        supplier = ""
        if supplier_col is not None and supplier_col < len(row):
            supplier = _clean_text(row[supplier_col])
        qty = 0.0
        if qty_col is not None and qty_col < len(row):
            qty = _to_float(row[qty_col]) or 0.0
        price = _to_float(row[price_col]) if price_col < len(row) else None
        key = _normalize(name)
        previous = index.get(key)
        if previous is None or qty >= previous.turnover_qty:
            index[key] = PurchasePriceEntry(
                nomenclature=name,
                supplier=supplier,
                price=price,
                turnover_qty=qty,
            )
    logger.info("document_analysis_agent.prices_loaded", unique=len(index))
    return index


def _enrich_merged_with_purchase_prices(rows: list[MergedNomenclatureRow]) -> None:
    """Дополняет итоговую структуру поставщиком и ценой без НДС за единицу."""
    index = _load_purchase_price_index()
    if not index:
        for row in rows:
            row.price_match = "unmatched"
        return

    candidates = [entry.nomenclature for entry in index.values()]
    matched = 0
    for row in rows:
        entry, method = _match_catalog_entry(row.nomenclature, index, candidates)
        if entry is None:
            row.supplier = None
            row.price = None
            row.price_match = "unmatched"
            continue
        row.supplier = entry.supplier or None
        row.price = round(entry.price, 2) if entry.price is not None else None
        row.price_match = method
        matched += 1
    logger.info(
        "document_analysis_agent.prices_enriched",
        matched=matched,
        total=len(rows),
        unmatched=len(rows) - matched,
    )


def _enrich_merged_with_stock(
    rows: list[MergedNomenclatureRow],
    workbooks: list[UploadedWorkbook],
    role_map: dict[str, WorkbookRole],
) -> list[str]:
    """Дополняет итоговую структуру остатками из файлов роли stock."""
    index, stock_files = _load_stock_index(workbooks, role_map)
    if not index:
        for row in rows:
            row.stock = 0.0
            row.stock_match = "unmatched"
        return stock_files

    candidates = [entry.nomenclature for entry in index.values()]
    matched = 0
    for row in rows:
        entry, method = _match_catalog_entry(row.nomenclature, index, candidates)
        if entry is None:
            row.stock = 0.0
            row.stock_match = "unmatched"
            continue
        row.stock = entry.quantity if entry.quantity is not None else 0.0
        row.stock_match = method
        matched += 1
    logger.info(
        "document_analysis_agent.stock_enriched",
        matched=matched,
        total=len(rows),
        unmatched=len(rows) - matched,
        files=stock_files,
    )
    return stock_files


def _load_stock_index(
    workbooks: list[UploadedWorkbook],
    role_map: dict[str, WorkbookRole],
) -> tuple[dict[str, StockEntry], list[str]]:
    """Парсит колонки «Номенклатура» + «Остаток…» из загруженных файлов остатков."""
    stock_files = [
        uploaded.filename
        for uploaded in workbooks
        if role_map.get(uploaded.filename) == ROLE_STOCK
    ]
    if not stock_files:
        return {}, []

    index: dict[str, StockEntry] = {}
    for uploaded in workbooks:
        if role_map.get(uploaded.filename) != ROLE_STOCK:
            continue
        workbook = load_workbook(BytesIO(uploaded.content), data_only=True, read_only=True)
        try:
            for worksheet in workbook.worksheets:
                _consume_stock_sheet(worksheet, index)
        finally:
            workbook.close()

    logger.info(
        "document_analysis_agent.stock_loaded",
        files=stock_files,
        unique=len(index),
    )
    return index, stock_files


def _consume_stock_sheet(worksheet: Worksheet, index: dict[str, StockEntry]) -> None:
    rows = list(worksheet.iter_rows(values_only=True))
    if not rows:
        return

    header_idx = name_col = stock_col = None
    for idx, row in enumerate(rows[:40]):
        normalized = [_normalize(value) for value in row]
        local_name = local_stock = None
        for col_idx, text in enumerate(normalized):
            if not text:
                continue
            if local_name is None and ("номенклатура" in text or text in {"наименование", "материал"}):
                local_name = col_idx
            elif local_stock is None and "остаток" in text:
                local_stock = col_idx
        if local_name is not None and local_stock is not None:
            header_idx, name_col, stock_col = idx, local_name, local_stock
            break

    if header_idx is None or name_col is None or stock_col is None:
        return

    for row in rows[header_idx + 1 :]:
        if name_col >= len(row):
            continue
        name = _clean_text(row[name_col]).rstrip("*").strip()
        if not name or _normalize(name) in {"номенклатура", "наименование", "итого"}:
            continue
        qty = _to_float(row[stock_col]) if stock_col < len(row) else None
        key = _normalize(name)
        previous = index.get(key)
        if previous is None:
            index[key] = StockEntry(nomenclature=name, quantity=qty)
            continue
        if qty is None:
            continue
        if previous.quantity is None:
            previous.quantity = qty
        else:
            previous.quantity = float(previous.quantity) + float(qty)


def _match_catalog_entry(
    nomenclature: str,
    key_to_entry: dict[str, TCatalogEntry],
    candidates: list[str],
) -> tuple[TCatalogEntry | None, str]:
    key = _normalize(nomenclature.rstrip("*"))
    if key in key_to_entry:
        return key_to_entry[key], "exact"

    contains_key = _match_catalog_key_by_containment(key, list(key_to_entry.keys()))
    if contains_key is not None:
        return key_to_entry[contains_key], "contains"

    best_name, score = _best_text_match(nomenclature, candidates)
    if best_name and score >= _PRICE_FUZZY_THRESHOLD:
        return key_to_entry[_normalize(best_name)], "fuzzy"
    return None, "unmatched"


def _match_catalog_key_by_containment(key: str, candidate_keys: list[str]) -> str | None:
    """Сопоставление по вхождению артикула/короткого имени (SM6T15A ⊂ диод SM6T15A…)."""
    if len(key) < 4:
        return None
    best_key: str | None = None
    best_len = -1
    for candidate_key in candidate_keys:
        shorter, longer = (
            (candidate_key, key) if len(candidate_key) <= len(key) else (key, candidate_key)
        )
        if len(shorter) < 6:
            continue
        # короткая сторона должна содержать цифру или быть достаточно длинной
        if not re.search(r"[0-9a-z]*\d[0-9a-z]*", shorter):
            if len(shorter) < 12:
                continue
        if shorter in longer and len(shorter) > best_len:
            best_key = candidate_key
            best_len = len(shorter)
    return best_key


def _enrich_merged_with_monthly_demand(
    rows: list[MergedNomenclatureRow],
    schedule_plans: list[ScheduleProductPlan],
) -> None:
    """Потребность: demand[месяц][кат][план|факт] = Σ (qty_график × qty_спеки)."""
    if not rows:
        return

    plans_by_key = {_normalize(plan.product): plan for plan in schedule_plans}
    months: list[str] = []
    for plan in schedule_plans:
        for month in plan.monthly_qty:
            if month not in months:
                months.append(month)

    if not months:
        for row in rows:
            row.monthly_demand = {}
        return

    for row in rows:
        demand = {month: _empty_month_bucket() for month in months}
        for product, spec_qty in row.by_product.items():
            plan = plans_by_key.get(_normalize(product))
            if plan is None:
                continue
            per_unit = float(spec_qty) if spec_qty is not None else 0.0
            if per_unit == 0:
                continue
            for month, month_bucket in plan.monthly_qty.items():
                target = demand.setdefault(month, _empty_month_bucket())
                for category in _SCHEDULE_CATEGORIES:
                    metrics = month_bucket.get(category) or {}
                    for metric in _SCHEDULE_METRICS:
                        product_qty = float(metrics.get(metric, 0.0))
                        if product_qty == 0:
                            continue
                        target[category][metric] = (
                            target[category].get(metric, 0.0) + product_qty * per_unit
                        )
        row.monthly_demand = {
            month: {
                category: {
                    metric: _round_qty(float(qty))
                    for metric, qty in metrics.items()
                }
                for category, metrics in month_bucket.items()
            }
            for month, month_bucket in demand.items()
        }

    logger.info(
        "document_analysis_agent.monthly_demand_enriched",
        nomenclatures=len(rows),
        months=months,
        nonzero=sum(1 for row in rows if _monthly_demand_has_nonzero(row.monthly_demand)),
    )


def _enrich_merged_with_monthly_receipts(
    rows: list[MergedNomenclatureRow],
    workbooks: list[UploadedWorkbook],
    role_map: dict[str, WorkbookRole],
) -> list[str]:
    """Ожидаемое поступление по месяцам из графика отгрузок (сумма по всем листам)."""
    index, shipment_files = _load_shipment_receipts_index(workbooks, role_map)
    months = sorted(
        {month for entry in index.values() for month in entry.monthly_qty},
        key=lambda name: _MONTH_NOMINATIVE.index(name) if name in _MONTH_NOMINATIVE else 99,
    )
    # если в графике дат нет — всё равно обнуляем поля под месяцы потребности
    if not months:
        for row in rows:
            seed = list(row.monthly_demand.keys()) or list(_MONTH_NOMINATIVE[6:12])
            row.monthly_receipts = {month: 0.0 for month in seed}
        return shipment_files

    if not index:
        for row in rows:
            row.monthly_receipts = {month: 0.0 for month in months}
        return shipment_files

    candidates = [entry.nomenclature for entry in index.values()]
    matched = 0
    for row in rows:
        receipts = {month: 0.0 for month in months}
        # также покрываем месяцы из потребности, даже если в отгрузках их не было
        for month in row.monthly_demand:
            receipts.setdefault(month, 0.0)
        entry, _method = _match_catalog_entry(row.nomenclature, index, candidates)
        if entry is not None:
            matched += 1
            for month, qty in entry.monthly_qty.items():
                receipts[month] = receipts.get(month, 0.0) + float(qty)
        row.monthly_receipts = {
            month: round(value, 6) if abs(value - round(value)) > 1e-9 else float(round(value))
            for month, value in receipts.items()
        }

    logger.info(
        "document_analysis_agent.monthly_receipts_enriched",
        matched=matched,
        total=len(rows),
        files=shipment_files,
        months=months,
        nonzero=sum(1 for row in rows if any(v > 0 for v in row.monthly_receipts.values())),
    )
    return shipment_files


def _enrich_merged_with_monthly_forecast(rows: list[MergedNomenclatureRow]) -> None:
    """Прогноз: остаток + поступление − Σ(планы по категориям); факт не участвует."""
    for row in rows:
        months = sorted(
            set(row.monthly_demand) | set(row.monthly_receipts),
            key=lambda name: _MONTH_NOMINATIVE.index(name) if name in _MONTH_NOMINATIVE else 99,
        )
        if not months:
            seed = list(_MONTH_NOMINATIVE[6:12])
            row.monthly_forecast = {month: 0.0 for month in seed}
            continue

        balance = 0.0 if row.stock is None else float(row.stock)
        forecasts: dict[str, float] = {}
        for month in months:
            balance = (
                balance
                + float(row.monthly_receipts.get(month, 0.0))
                - _plan_demand_total(row.monthly_demand.get(month))
            )
            forecasts[month] = _round_qty(balance)
        row.monthly_forecast = forecasts

    deficit_rows = sum(
        1 for row in rows if any(value < 0 for value in row.monthly_forecast.values())
    )
    logger.info(
        "document_analysis_agent.monthly_forecast_enriched",
        nomenclatures=len(rows),
        deficit_rows=deficit_rows,
    )


def _match_detailed_plan_for_product(
    product: str,
    plans_by_key: dict[str, DetailedScheduleProductPlan],
    plan_names: list[str],
) -> DetailedScheduleProductPlan | None:
    """Сопоставляет изделие из спеки/помесячного графика с коротким именем детального плана."""
    key = _normalize(product)
    plan = plans_by_key.get(key)
    if plan is not None:
        return plan

    contains_key = _match_catalog_key_by_containment(key, list(plans_by_key.keys()))
    if contains_key is not None:
        return plans_by_key[contains_key]

    # короткое «Сокол И» ⊂ длинного имени изделия из помесячного графика
    best_plan: DetailedScheduleProductPlan | None = None
    best_len = -1
    for plan_key, candidate in plans_by_key.items():
        if len(plan_key) < 4:
            continue
        if plan_key in key and len(plan_key) > best_len:
            best_plan = candidate
            best_len = len(plan_key)
        elif key in plan_key and len(key) >= 6 and len(key) > best_len:
            best_plan = candidate
            best_len = len(key)
    if best_plan is not None:
        return best_plan

    best_name, score = _best_text_match(product, plan_names)
    if best_name and score >= 0.72:
        return plans_by_key[_normalize(best_name)]
    return None


def _enrich_merged_with_daily_demand(
    rows: list[MergedNomenclatureRow],
    detailed: DetailedScheduleExtract,
) -> None:
    """Потребность по дням: Σ (план_изделия[день] × qty_в_спеке)."""
    day_keys = list(detailed.day_keys) or _month_day_keys(detailed.year, detailed.month)
    if not rows:
        return
    if not day_keys:
        for row in rows:
            row.daily_demand = {}
        return

    plans_by_key = {_normalize(plan.product): plan for plan in detailed.plans}
    plan_names = [plan.product for plan in detailed.plans]

    for row in rows:
        demand = {day: 0.0 for day in day_keys}
        for product, spec_qty in row.by_product.items():
            plan = _match_detailed_plan_for_product(product, plans_by_key, plan_names)
            if plan is None:
                continue
            per_unit = float(spec_qty) if spec_qty is not None else 0.0
            if per_unit == 0:
                continue
            for day_key, product_qty in plan.daily_qty.items():
                if day_key not in demand:
                    continue
                demand[day_key] = demand.get(day_key, 0.0) + float(product_qty) * per_unit
        row.daily_demand = {
            day: round(value, 6) if abs(value - round(value)) > 1e-9 else float(round(value))
            for day, value in demand.items()
        }

    logger.info(
        "document_analysis_agent.daily_demand_enriched",
        nomenclatures=len(rows),
        month=f"{detailed.year:04d}-{detailed.month:02d}",
        days=len(day_keys),
        plans=len(detailed.plans),
        nonzero=sum(1 for row in rows if any(v > 0 for v in row.daily_demand.values())),
    )


def _enrich_merged_with_daily_receipts(
    rows: list[MergedNomenclatureRow],
    workbooks: list[UploadedWorkbook],
    role_map: dict[str, WorkbookRole],
    detailed: DetailedScheduleExtract,
) -> None:
    """Ожидаемые поступления по дням выбранного месяца из графика отгрузок."""
    day_keys = list(detailed.day_keys) or _month_day_keys(detailed.year, detailed.month)
    if not rows:
        return
    if not day_keys:
        for row in rows:
            row.daily_receipts = {}
        return

    index, shipment_files = _load_shipment_receipts_index(workbooks, role_map)
    day_set = set(day_keys)
    if not index:
        for row in rows:
            row.daily_receipts = {day: 0.0 for day in day_keys}
        return

    candidates = [entry.nomenclature for entry in index.values()]
    matched = 0
    for row in rows:
        receipts = {day: 0.0 for day in day_keys}
        entry, _method = _match_catalog_entry(row.nomenclature, index, candidates)
        if entry is not None:
            matched += 1
            for day_key, qty in entry.daily_qty.items():
                if day_key in day_set:
                    receipts[day_key] = receipts.get(day_key, 0.0) + float(qty)
        row.daily_receipts = {
            day: round(value, 6) if abs(value - round(value)) > 1e-9 else float(round(value))
            for day, value in receipts.items()
        }

    logger.info(
        "document_analysis_agent.daily_receipts_enriched",
        matched=matched,
        total=len(rows),
        files=shipment_files,
        month=f"{detailed.year:04d}-{detailed.month:02d}",
        nonzero=sum(1 for row in rows if any(v > 0 for v in row.daily_receipts.values())),
    )


def _enrich_merged_with_daily_forecast(
    rows: list[MergedNomenclatureRow],
    detailed: DetailedScheduleExtract,
) -> None:
    """Прогноз остатка по дням: цепочка stock + receipt − demand."""
    day_keys = list(detailed.day_keys) or _month_day_keys(detailed.year, detailed.month)
    for row in rows:
        if not day_keys:
            row.daily_forecast = {}
            continue
        balance = 0.0 if row.stock is None else float(row.stock)
        forecasts: dict[str, float] = {}
        for day_key in day_keys:
            balance = (
                balance
                + float(row.daily_receipts.get(day_key, 0.0))
                - float(row.daily_demand.get(day_key, 0.0))
            )
            forecasts[day_key] = (
                round(balance, 6) if abs(balance - round(balance)) > 1e-9 else float(round(balance))
            )
        row.daily_forecast = forecasts

    deficit_rows = sum(
        1 for row in rows if any(value < 0 for value in row.daily_forecast.values())
    )
    logger.info(
        "document_analysis_agent.daily_forecast_enriched",
        nomenclatures=len(rows),
        days=len(day_keys),
        deficit_rows=deficit_rows,
    )


def _load_shipment_receipts_index(
    workbooks: list[UploadedWorkbook],
    role_map: dict[str, WorkbookRole],
) -> tuple[dict[str, ShipmentReceiptEntry], list[str]]:
    shipment_files = [
        uploaded.filename
        for uploaded in workbooks
        if role_map.get(uploaded.filename) == ROLE_SHIPMENT_SCHEDULE
    ]
    if not shipment_files:
        return {}, []

    index: dict[str, ShipmentReceiptEntry] = {}
    for uploaded in workbooks:
        if role_map.get(uploaded.filename) != ROLE_SHIPMENT_SCHEDULE:
            continue
        workbook = load_workbook(BytesIO(uploaded.content), data_only=True, read_only=True)
        try:
            for worksheet in workbook.worksheets:
                _consume_shipment_sheet(worksheet, index)
        finally:
            workbook.close()

    logger.info(
        "document_analysis_agent.shipment_receipts_loaded",
        files=shipment_files,
        unique=len(index),
    )
    return index, shipment_files


def _consume_shipment_sheet(
    worksheet: Worksheet,
    index: dict[str, ShipmentReceiptEntry],
) -> None:
    """Парсит лист графика отгрузок: qty под колонками дат → сумма по календарным месяцам."""
    parsed = _parse_shipment_sheet_layout(worksheet)
    if parsed is None:
        return
    header_idx, name_col, _msk_col, _rostov_col, date_cols = parsed

    rows = list(worksheet.iter_rows(values_only=True))
    for row in rows[header_idx + 1 :]:
        if name_col >= len(row):
            continue
        name = _clean_text(row[name_col]).rstrip("*").strip()
        if not name or _normalize(name) in {"номенклатура", "наименование", "итого"}:
            continue
        key = _normalize(name)
        entry = index.get(key)
        if entry is None:
            entry = ShipmentReceiptEntry(nomenclature=name)
            index[key] = entry

        for col_idx, delivery_date in date_cols:
            if col_idx >= len(row):
                continue
            qty = _to_float(row[col_idx])
            if qty is None or qty == 0:
                continue
            month_label = _MONTH_NOMINATIVE[delivery_date.month - 1]
            entry.monthly_qty[month_label] = entry.monthly_qty.get(month_label, 0.0) + float(qty)
            day_key = delivery_date.isoformat()
            entry.daily_qty[day_key] = entry.daily_qty.get(day_key, 0.0) + float(qty)


def _parse_shipment_sheet_layout(
    worksheet: Worksheet,
) -> tuple[int, int, int | None, int | None, list[tuple[int, date]]] | None:
    """Шапка листа отгрузок: номенклатура, логистика МСК / МСК-Ростов, колонки дат."""
    rows = list(worksheet.iter_rows(values_only=True))
    if not rows:
        return None

    for idx, row in enumerate(rows[:15]):
        name_col: int | None = None
        msk_col: int | None = None
        rostov_col: int | None = None
        date_cols: list[tuple[int, date]] = []
        for col_idx, value in enumerate(row):
            text = _normalize(value)
            if name_col is None and text in {"номенклатура", "наименование"}:
                name_col = col_idx
                continue
            if "логистика" in text:
                if "ростов" in text:
                    rostov_col = col_idx
                    continue
                if "мск" in text or "москв" in text:
                    msk_col = col_idx
                    continue
            delivery_date = _header_value_to_date(value)
            if delivery_date is not None:
                date_cols.append((col_idx, delivery_date))
        if name_col is not None and date_cols:
            return idx, name_col, msk_col, rostov_col, date_cols
    return None


def _parse_logistics_range(value: Any) -> tuple[int, int] | None:
    """«7-14 к.д.» / «5-10» → (короткая, длинная) в календарных днях."""
    text = _clean_text(value)
    if not text:
        return None
    match = _LOGISTICS_RANGE_RE.search(text)
    if not match:
        return None
    short_days = int(match.group(1))
    long_days = int(match.group(2))
    if short_days > long_days:
        short_days, long_days = long_days, short_days
    return short_days, long_days


def _logistics_stage_windows(
    moscow_date: date,
    short_msk: int,
    long_msk: int,
    short_rostov: int,
    long_rostov: int,
) -> dict[str, tuple[date, date]]:
    """Окна стадий от плановой поставки в Москву (D).

    Цепочка: загрузка → МСК [short…long] → таможня (+2) → Ростов [short…long].

    1. Загрузка: точечно D − long_msk − таможня − long_rostov
    2. МСК: [D − (long_msk − short_msk) … D]
    3. Таможня: точечно D + 2
    4. Ростов: [D + 2 + short_rostov … D + 2 + long_rostov]
    """
    early_msk_offset = max(0, long_msk - short_msk)
    customs = _LOGISTICS_CUSTOMS_DAYS
    cleared = moscow_date + timedelta(days=customs)
    load_day = moscow_date - timedelta(days=long_msk + customs + long_rostov)
    msk_start = moscow_date - timedelta(days=early_msk_offset)
    rostov_start = cleared + timedelta(days=short_rostov)
    rostov_end = cleared + timedelta(days=long_rostov)
    return {
        "loading_dispatch": (load_day, load_day),
        "msk_arrival": (msk_start, moscow_date),
        "customs_clearance": (cleared, cleared),
        "rostov_arrival": (rostov_start, rostov_end),
    }


def _logistics_risk_metrics(
    as_of: date, window_start: date, window_end: date
) -> tuple[int, float, str]:
    """days_remaining, risk_ratio (1=зелёный запас, 0=красный риск), risk_level."""
    days_remaining = (window_end - as_of).days
    span = max(1, (window_end - window_start).days)
    risk_ratio = max(0.0, min(1.0, days_remaining / span))
    if days_remaining <= 0:
        level = "critical"
    elif risk_ratio <= 0.25:
        level = "high"
    elif risk_ratio <= 0.5:
        level = "medium"
    else:
        level = "low"
    return days_remaining, round(risk_ratio, 4), level


def _empty_logistics_risk_board(as_of: date | None = None) -> LogisticsRiskBoard:
    day = as_of or date.today()
    return LogisticsRiskBoard(
        as_of=day.isoformat(),
        stages=[
            LogisticsRiskStage(key=key, label=label) for key, label in _LOGISTICS_STAGE_DEFS
        ],
    )


def _build_logistics_risk_board(
    merged: list[MergedNomenclatureRow],
    workbooks: list[UploadedWorkbook],
    role_map: dict[str, WorkbookRole],
    as_of: date | None = None,
) -> LogisticsRiskBoard:
    """Собирает номенклатуры+поставщиков на контрольных точках логистики на сегодня."""
    as_of_day = as_of or date.today()
    board = _empty_logistics_risk_board(as_of_day)
    stage_map = {stage.key: stage for stage in board.stages}

    supplier_by_key = {_normalize(row.nomenclature): row.supplier for row in merged}
    supplier_candidates = [row.nomenclature for row in merged]
    merged_index = {_normalize(row.nomenclature): row for row in merged}

    # aggregate: (stage_key, nom_key, moscow_iso) -> item
    buckets: dict[tuple[str, str, str], LogisticsRiskItem] = {}

    for uploaded in workbooks:
        if role_map.get(uploaded.filename) != ROLE_SHIPMENT_SCHEDULE:
            continue
        workbook = load_workbook(BytesIO(uploaded.content), data_only=True, read_only=True)
        try:
            for worksheet in workbook.worksheets:
                parsed = _parse_shipment_sheet_layout(worksheet)
                if parsed is None:
                    continue
                header_idx, name_col, msk_col, rostov_col, date_cols = parsed
                if msk_col is None or rostov_col is None:
                    continue
                rows = list(worksheet.iter_rows(values_only=True))
                sheet_name = worksheet.title
                for row in rows[header_idx + 1 :]:
                    if name_col >= len(row):
                        continue
                    name = _clean_text(row[name_col]).rstrip("*").strip()
                    if not name or _normalize(name) in {
                        "номенклатура",
                        "наименование",
                        "итого",
                    }:
                        continue
                    msk_range = (
                        _parse_logistics_range(row[msk_col]) if msk_col < len(row) else None
                    )
                    rostov_range = (
                        _parse_logistics_range(row[rostov_col])
                        if rostov_col < len(row)
                        else None
                    )
                    if msk_range is None or rostov_range is None:
                        continue
                    short_msk, long_msk = msk_range
                    short_rostov, long_rostov = rostov_range

                    nom_key = _normalize(name)
                    supplier = supplier_by_key.get(nom_key)
                    if supplier is None and merged_index:
                        matched, _method = _match_catalog_entry(
                            name, merged_index, supplier_candidates
                        )
                        if matched is not None:
                            supplier = matched.supplier

                    for col_idx, moscow_date in date_cols:
                        if col_idx >= len(row):
                            continue
                        qty = _to_float(row[col_idx])
                        if qty is None or qty == 0:
                            continue
                        windows = _logistics_stage_windows(
                            moscow_date,
                            short_msk,
                            long_msk,
                            short_rostov,
                            long_rostov,
                        )
                        for stage_key, (window_start, window_end) in windows.items():
                            if not (window_start <= as_of_day <= window_end):
                                continue
                            days_remaining, risk_ratio, risk_level = _logistics_risk_metrics(
                                as_of_day, window_start, window_end
                            )
                            moscow_iso = moscow_date.isoformat()
                            bucket_key = (stage_key, nom_key, moscow_iso)
                            existing = buckets.get(bucket_key)
                            if existing is None:
                                buckets[bucket_key] = LogisticsRiskItem(
                                    nomenclature=name,
                                    supplier=supplier,
                                    quantity=float(qty),
                                    moscow_date=moscow_iso,
                                    milestone_date=window_end.isoformat(),
                                    sheet=sheet_name,
                                    window_start=window_start.isoformat(),
                                    window_end=window_end.isoformat(),
                                    days_remaining=days_remaining,
                                    risk_ratio=risk_ratio,
                                    risk_level=risk_level,
                                )
                            else:
                                existing.quantity = round(existing.quantity + float(qty), 6)
                                if sheet_name not in existing.sheet.split(" · "):
                                    existing.sheet = f"{existing.sheet} · {sheet_name}"
                                if not existing.supplier and supplier:
                                    existing.supplier = supplier
        finally:
            workbook.close()

    stage_order = {key: idx for idx, (key, _) in enumerate(_LOGISTICS_STAGE_DEFS)}
    for (stage_key, _nom_key, _moscow_iso), item in sorted(
        buckets.items(),
        key=lambda pair: (
            stage_order.get(pair[0][0], 99),
            pair[1].days_remaining,
            pair[1].nomenclature.lower(),
            pair[0][2],
        ),
    ):
        stage = stage_map.get(stage_key)
        if stage is not None:
            stage.items.append(item)

    logger.info(
        "document_analysis_agent.logistics_risks_built",
        as_of=board.as_of,
        stages={stage.key: len(stage.items) for stage in board.stages},
        total=sum(len(stage.items) for stage in board.stages),
    )
    return board


def _header_value_to_date(value: Any) -> date | None:
    """Дата в шапке графика отгрузок → date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            from openpyxl.utils.datetime import from_excel

            parsed = from_excel(value)
            if isinstance(parsed, datetime):
                return parsed.date()
            if isinstance(parsed, date):
                return parsed
        except Exception:
            return None
        return None
    text = _clean_text(value)
    if not text:
        return None
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text)
    if match:
        day_n = int(match.group(1))
        month_n = int(match.group(2))
        year_n = int(match.group(3))
        if year_n < 100:
            year_n += 2000
        try:
            return date(year_n, month_n, day_n)
        except ValueError:
            return None
    return None


def _header_value_to_month_label(value: Any) -> str | None:
    """Дата в шапке графика отгрузок → именительный месяц (Июль…)."""
    parsed = _header_value_to_date(value)
    if parsed is None:
        return None
    return _MONTH_NOMINATIVE[parsed.month - 1]


def _detect_month_metric_columns(worksheet: Worksheet, keyword: str) -> dict[str, int]:
    """Колонки метрики по месяцам из строки 4 шаблона Header.xlsx."""
    mapping: dict[str, int] = {}
    for col_idx in range(1, _RESULT_GRID_COLS + 1):
        text = _normalize(worksheet.cell(4, col_idx).value)
        if not text or keyword not in text:
            continue
        for month_idx, nominative in enumerate(_MONTH_NOMINATIVE):
            month_key = _normalize(nominative)
            genitive = _MONTH_GENITIVE[month_idx]
            if month_key in text or genitive in text:
                mapping[nominative] = col_idx
                break
    return mapping


def _detect_columns_by_keyword(worksheet: Worksheet, keyword: str) -> list[int]:
    """Индексы колонок строки 4, где заголовок содержит keyword (порядок слева направо)."""
    cols: list[int] = []
    for col_idx in range(1, _RESULT_GRID_COLS + 1):
        text = _normalize(worksheet.cell(4, col_idx).value)
        if text and keyword in text:
            cols.append(col_idx)
    return cols


def _build_forecast_column_chain(
    worksheet: Worksheet,
    demand_columns: dict[str, int],
    receipt_columns: dict[str, int],
) -> list[tuple[str, int, int, int]]:
    """Цепочка (месяц, col_потребность, col_поступление, col_прогноз) по шаблону Header."""
    forecast_cols = _detect_columns_by_keyword(worksheet, "прогнозируемый")
    months = sorted(
        demand_columns.keys(),
        key=lambda name: _MONTH_NOMINATIVE.index(name) if name in _MONTH_NOMINATIVE else 99,
    )
    chain: list[tuple[str, int, int, int]] = []
    for index, month in enumerate(months):
        if index >= len(forecast_cols):
            break
        demand_col = demand_columns[month]
        receipt_col = receipt_columns.get(month, demand_col + 1)
        forecast_col = forecast_cols[index]
        chain.append((month, demand_col, receipt_col, forecast_col))
    return chain


def _apply_forecast_deficit_formatting(
    worksheet: Worksheet,
    forecast_cols: list[int],
    first_row: int,
    last_row: int,
) -> None:
    """Красная заливка при прогнозируемом остатке < 0 (как в эталоне обеспеченности)."""
    if last_row < first_row or not forecast_cols:
        return
    for col_idx in forecast_cols:
        letter = get_column_letter(col_idx)
        cell_range = f"{letter}{first_row}:{letter}{last_row}"
        worksheet.conditional_formatting.add(
            cell_range,
            CellIsRule(
                operator="lessThan",
                formula=["0"],
                fill=_FORECAST_DEFICIT_FILL,
                font=_FORECAST_DEFICIT_FONT,
            ),
        )


def _months_for_monthly_sheet(rows: list[MergedNomenclatureRow]) -> list[str]:
    """Месяцы колонок = из потребности графика; receipt-only месяцы не добавляем в шапку."""
    months: list[str] = []
    for row in rows:
        for month in row.monthly_demand:
            if month not in months:
                months.append(month)
    if not months:
        months = list(_MONTH_NOMINATIVE[6:12])
    return sorted(
        months,
        key=lambda name: _MONTH_NOMINATIVE.index(name) if name in _MONTH_NOMINATIVE else 99,
    )


def _next_month_forecast_label(month: str) -> str:
    """Подпись «Прогнозируемый остаток на 01.MM.YYYY» (год = текущий / +1 для января)."""
    if month not in _MONTH_NOMINATIVE:
        return f"Прогнозируемый остаток после {month}"
    idx = _MONTH_NOMINATIVE.index(month)
    next_idx = (idx + 1) % 12
    next_month_num = next_idx + 1
    year = date.today().year
    if next_idx < idx:
        year += 1
    return f"Прогнозируемый остаток на 01.{next_month_num:02d}.{year}"


def _build_result_xlsx(
    rows: list[MergedNomenclatureRow],
    detailed: DetailedScheduleExtract | None = None,
) -> bytes:
    """Собирает result.xlsx: помесячное обеспечение + обеспечение по дням."""
    if detailed is None:
        today = date.today()
        detailed = DetailedScheduleExtract(
            files=[],
            plans=[],
            year=today.year,
            month=today.month,
            day_keys=_month_day_keys(today.year, today.month),
        )

    workbook = Workbook()
    monthly_ws = workbook.active
    _write_monthly_assurance_sheet(monthly_ws, rows)

    daily_ws = workbook.create_sheet(_SHEET_DAILY_ASSURANCE)
    _write_daily_assurance_sheet(daily_ws, rows, detailed)

    logger.info(
        "document_analysis_agent.result_xlsx_built",
        rows=len(rows),
        sheets=[monthly_ws.title, daily_ws.title],
        daily_month=f"{detailed.year:04d}-{detailed.month:02d}",
        daily_days=len(detailed.day_keys),
    )

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _build_monthly_assurance_header(
    worksheet: Worksheet, months: list[str]
) -> dict[str, dict[str, int]]:
    """Шапка помесячного листа: 8 колонок на месяц. Возвращает индексы колонок."""
    last_col = _FIXED_RESULT_COLS + len(months) * _MONTHLY_COLS_PER_MONTH
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    thin = Side(style="thin", color="B0B0B0")
    header_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    worksheet.merge_cells(
        start_row=1, start_column=1, end_row=1, end_column=max(last_col, _FIXED_RESULT_COLS)
    )
    title = worksheet.cell(
        1, 1, "Обеспеченность плана производства «Сокол» материалами"
    )
    _style_header_cell(
        title, fill=_HEADER_TITLE_FILL, font=_HEADER_TITLE_FONT, alignment=center
    )
    for col_idx in range(2, last_col + 1):
        _style_header_cell(
            worksheet.cell(1, col_idx),
            fill=_HEADER_TITLE_FILL,
            font=_HEADER_TITLE_FONT,
            alignment=center,
        )

    worksheet.merge_cells(
        start_row=2, start_column=1, end_row=2, end_column=max(last_col, _FIXED_RESULT_COLS)
    )
    subtitle = worksheet.cell(
        2,
        1,
        "Остатки на дату анализа; потребность — Заказ / Опытные образцы / Склад "
        "(План и Факт) по графику производства; ожидаемые поступления — по графикам "
        "отгрузок; прогноз остатка учитывает только суммы планов",
    )
    _style_header_cell(
        subtitle, fill=_HEADER_SUBTITLE_FILL, font=_HEADER_SUBTITLE_FONT, alignment=left
    )
    for col_idx in range(2, last_col + 1):
        _style_header_cell(
            worksheet.cell(2, col_idx),
            fill=_HEADER_SUBTITLE_FILL,
            font=_HEADER_SUBTITLE_FONT,
            alignment=left,
        )

    fixed_headers = [
        "Номенклатура",
        "В каких изделиях используется",
        "Поставщик",
        "Ед. изм.",
        "Цена, руб./ед.",
        "Остаток",
    ]
    for col_idx, label in enumerate(fixed_headers, start=1):
        worksheet.merge_cells(
            start_row=3, start_column=col_idx, end_row=5, end_column=col_idx
        )
        for row_idx in (3, 4, 5):
            cell = worksheet.cell(row_idx, col_idx, label if row_idx == 3 else None)
            _style_header_cell(
                cell, fill=_HEADER_GROUP_FILL, font=_HEADER_GROUP_FONT, alignment=center
            )
            cell.border = header_border

    layout: dict[str, dict[str, int]] = {}
    for month_index, month in enumerate(months):
        base = _FIXED_RESULT_COLS + 1 + month_index * _MONTHLY_COLS_PER_MONTH
        worksheet.merge_cells(
            start_row=3, start_column=base, end_row=3, end_column=base + 7
        )
        for offset in range(8):
            cell = worksheet.cell(3, base + offset, month if offset == 0 else None)
            _style_header_cell(
                cell, fill=_HEADER_GROUP_FILL, font=_HEADER_GROUP_FONT, alignment=center
            )
            cell.border = header_border

        # категории потребности (по 2 колонки) + поступление + прогноз
        category_spans = [
            (0, "заказ", _CATEGORY_LABELS["заказ"]),
            (2, "опытные", _CATEGORY_LABELS["опытные"]),
            (4, "склад", _CATEGORY_LABELS["склад"]),
        ]
        month_cols: dict[str, int] = {}
        for offset, category_key, category_label in category_spans:
            start = base + offset
            worksheet.merge_cells(
                start_row=4, start_column=start, end_row=4, end_column=start + 1
            )
            for sub in range(2):
                cell = worksheet.cell(
                    4, start + sub, category_label if sub == 0 else None
                )
                _style_header_cell(
                    cell,
                    fill=_HEADER_GROUP_FILL,
                    font=_HEADER_GROUP_FONT,
                    alignment=center,
                )
                cell.border = header_border
            for metric_offset, metric in enumerate(_SCHEDULE_METRICS):
                cell = worksheet.cell(
                    5, start + metric_offset, "План" if metric == "план" else "Факт"
                )
                _style_header_cell(
                    cell,
                    fill=_HEADER_METRIC_FILL,
                    font=_HEADER_METRIC_FONT,
                    alignment=center,
                )
                cell.border = header_border
                month_cols[f"{category_key}:{metric}"] = start + metric_offset

        receipt_col = base + 6
        forecast_col = base + 7
        worksheet.merge_cells(
            start_row=4, start_column=receipt_col, end_row=5, end_column=receipt_col
        )
        worksheet.merge_cells(
            start_row=4, start_column=forecast_col, end_row=5, end_column=forecast_col
        )
        for row_idx in (4, 5):
            receipt_cell = worksheet.cell(
                row_idx,
                receipt_col,
                f"Ожидаемое поступление {month.lower()}" if row_idx == 4 else None,
            )
            _style_header_cell(
                receipt_cell,
                fill=_HEADER_METRIC_FILL,
                font=_HEADER_METRIC_FONT,
                alignment=center,
            )
            receipt_cell.border = header_border
            forecast_cell = worksheet.cell(
                row_idx,
                forecast_col,
                _next_month_forecast_label(month) if row_idx == 4 else None,
            )
            _style_header_cell(
                forecast_cell,
                fill=_HEADER_METRIC_FILL,
                font=_HEADER_METRIC_FONT,
                alignment=center,
            )
            forecast_cell.border = header_border

        month_cols["receipt"] = receipt_col
        month_cols["forecast"] = forecast_col
        # plan demand cols for formula
        month_cols["plan_заказ"] = month_cols["заказ:план"]
        month_cols["plan_опытные"] = month_cols["опытные:план"]
        month_cols["plan_склад"] = month_cols["склад:план"]
        layout[month] = month_cols

    worksheet.column_dimensions["A"].width = 50
    worksheet.column_dimensions["B"].width = 43
    worksheet.column_dimensions["C"].width = 36
    worksheet.column_dimensions["D"].width = 10
    worksheet.column_dimensions["E"].width = 14
    worksheet.column_dimensions["F"].width = 12
    for col_idx in range(_FIXED_RESULT_COLS + 1, last_col + 1):
        worksheet.column_dimensions[get_column_letter(col_idx)].width = 11

    worksheet.row_dimensions[1].height = 20
    worksheet.row_dimensions[2].height = 36
    worksheet.row_dimensions[3].height = 18
    worksheet.row_dimensions[4].height = 22
    worksheet.row_dimensions[5].height = 18
    return layout


def _write_monthly_assurance_sheet(
    worksheet: Worksheet,
    rows: list[MergedNomenclatureRow],
) -> None:
    """Программная шапка (8 кол/месяц) + данные + формулы прогноза по сумме планов."""
    worksheet.title = _SHEET_MONTHLY_ASSURANCE
    months = _months_for_monthly_sheet(rows)
    layout = _build_monthly_assurance_header(worksheet, months)

    data_alignment = Alignment(vertical="top", wrap_text=True, horizontal="left")
    thin = Side(style="thin", color="B0B0B0")
    data_border = Border(left=thin, right=thin, top=thin, bottom=thin)
    width_a = 50.0
    width_b = 43.0
    width_c = 36.0
    last_col = _FIXED_RESULT_COLS + len(months) * _MONTHLY_COLS_PER_MONTH
    forecast_cols: list[int] = []

    for offset, row in enumerate(rows):
        excel_row = _MONTHLY_DATA_START_ROW + offset
        values: dict[int, Any] = {
            1: row.nomenclature,
            2: "; ".join(row.products),
            3: row.supplier,
            4: row.unit,
            5: row.price,
            6: 0.0 if row.stock is None else row.stock,
        }
        for month_index, month in enumerate(months):
            cols = layout[month]
            bucket = row.monthly_demand.get(month) or _empty_month_bucket()
            for category in _SCHEDULE_CATEGORIES:
                for metric in _SCHEDULE_METRICS:
                    col_idx = cols[f"{category}:{metric}"]
                    values[col_idx] = float(bucket.get(category, {}).get(metric, 0.0))
            receipt_col = cols["receipt"]
            forecast_col = cols["forecast"]
            values[receipt_col] = float(row.monthly_receipts.get(month, 0.0))

            plan_letters = [
                get_column_letter(cols["plan_заказ"]),
                get_column_letter(cols["plan_опытные"]),
                get_column_letter(cols["plan_склад"]),
            ]
            receipt_letter = get_column_letter(receipt_col)
            plan_sum = "+".join(f"{letter}{excel_row}" for letter in plan_letters)
            if month_index == 0:
                values[forecast_col] = (
                    f"={_STOCK_COL_LETTER}{excel_row}"
                    f"+{receipt_letter}{excel_row}-({plan_sum})"
                )
            else:
                prev_forecast = get_column_letter(layout[months[month_index - 1]]["forecast"])
                values[forecast_col] = (
                    f"={prev_forecast}{excel_row}+{receipt_letter}{excel_row}-({plan_sum})"
                )
            if offset == 0:
                forecast_cols.append(forecast_col)

        for col_idx in range(1, last_col + 1):
            cell = worksheet.cell(excel_row, col_idx, values.get(col_idx))
            cell.alignment = data_alignment
            cell.border = data_border

        worksheet.row_dimensions[excel_row].height = _estimate_wrapped_row_height(
            [str(values[1] or ""), str(values[2] or ""), str(values[3] or "")],
            [width_a, width_b, width_c],
        )

    if rows and forecast_cols:
        last_data_row = _MONTHLY_DATA_START_ROW + len(rows) - 1
        _apply_forecast_deficit_formatting(
            worksheet,
            forecast_cols,
            _MONTHLY_DATA_START_ROW,
            last_data_row,
        )

    worksheet.freeze_panes = f"A{_MONTHLY_DATA_START_ROW}"
    logger.info(
        "document_analysis_agent.monthly_assurance_sheet_written",
        rows=len(rows),
        months=months,
        forecast_months=len(forecast_cols),
        cols_per_month=_MONTHLY_COLS_PER_MONTH,
    )


def _style_header_cell(
    cell: Any,
    *,
    fill: PatternFill,
    font: Font,
    alignment: Alignment,
) -> None:
    cell.fill = fill
    cell.font = font
    cell.alignment = alignment


def _build_daily_assurance_header(worksheet: Worksheet, year: int, month: int) -> list[str]:
    """Шапка дневного листа в стиле Header.xlsx: A–F + блоки по 3 колонки на день."""
    day_keys = _month_day_keys(year, month)
    if year <= 0 or month <= 0:
        today = date.today()
        year, month = today.year, today.month
        day_keys = _month_day_keys(year, month)

    month_label = _MONTH_NOMINATIVE[month - 1]
    last_col = _FIXED_RESULT_COLS + len(day_keys) * 3
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    thin = Side(style="thin", color="B0B0B0")
    header_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Строка 1–2: как в Header (тёмно-синий заголовок + голубая подпись)
    worksheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    title_cell = worksheet.cell(
        1, 1, f"Обеспеченность плана производства «Сокол» материалами — по дням ({month_label} {year})"
    )
    _style_header_cell(
        title_cell, fill=_HEADER_TITLE_FILL, font=_HEADER_TITLE_FONT, alignment=center
    )
    for col_idx in range(2, last_col + 1):
        _style_header_cell(
            worksheet.cell(1, col_idx),
            fill=_HEADER_TITLE_FILL,
            font=_HEADER_TITLE_FONT,
            alignment=center,
        )

    worksheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    subtitle = (
        f"Остатки на дату анализа; потребность и поступления — по дням {month_label.lower()} "
        f"{year} (01.{month:02d}.{year}–{len(day_keys):02d}.{month:02d}.{year}); "
        f"ожидаемые поступления — по представленным графикам отгрузок"
    )
    subtitle_cell = worksheet.cell(2, 1, subtitle)
    _style_header_cell(
        subtitle_cell,
        fill=_HEADER_SUBTITLE_FILL,
        font=_HEADER_SUBTITLE_FONT,
        alignment=left,
    )
    for col_idx in range(2, last_col + 1):
        _style_header_cell(
            worksheet.cell(2, col_idx),
            fill=_HEADER_SUBTITLE_FILL,
            font=_HEADER_SUBTITLE_FONT,
            alignment=left,
        )

    fixed_headers = [
        "Номенклатура",
        "В каких изделиях используется",
        "Поставщик",
        "Ед. изм.",
        "Цена, руб./ед.",
        "Остаток",
    ]
    # A–F: merge 3:4, заливка группы (синий)
    for col_idx, title in enumerate(fixed_headers, start=1):
        worksheet.merge_cells(
            start_row=3, start_column=col_idx, end_row=4, end_column=col_idx
        )
        for row_idx in (3, 4):
            cell = worksheet.cell(row_idx, col_idx, title if row_idx == 3 else None)
            _style_header_cell(
                cell, fill=_HEADER_GROUP_FILL, font=_HEADER_GROUP_FONT, alignment=center
            )
            cell.border = header_border

    for day_index, day_key in enumerate(day_keys):
        day = date.fromisoformat(day_key)
        base_col = _FIXED_RESULT_COLS + 1 + day_index * 3
        label = f"{day.day:02d}.{day.month:02d}"
        worksheet.merge_cells(
            start_row=3, start_column=base_col, end_row=3, end_column=base_col + 2
        )
        for offset in range(3):
            cell = worksheet.cell(3, base_col + offset, label if offset == 0 else None)
            _style_header_cell(
                cell, fill=_HEADER_GROUP_FILL, font=_HEADER_GROUP_FONT, alignment=center
            )
            cell.border = header_border

        next_day = day + timedelta(days=1)
        next_label = f"{next_day.day:02d}.{next_day.month:02d}.{next_day.year}"
        sub = [
            f"Потребность {label}",
            f"Ожидаемое поступление {label}",
            f"Прогнозируемый остаток на {next_label}",
        ]
        for offset, text in enumerate(sub):
            cell = worksheet.cell(4, base_col + offset, text)
            _style_header_cell(
                cell, fill=_HEADER_METRIC_FILL, font=_HEADER_METRIC_FONT, alignment=center
            )
            cell.border = header_border

    worksheet.column_dimensions["A"].width = 50
    worksheet.column_dimensions["B"].width = 43
    worksheet.column_dimensions["C"].width = 36
    worksheet.column_dimensions["D"].width = 10
    worksheet.column_dimensions["E"].width = 14
    worksheet.column_dimensions["F"].width = 12
    for day_index in range(len(day_keys)):
        base_col = _FIXED_RESULT_COLS + 1 + day_index * 3
        for offset in range(3):
            worksheet.column_dimensions[get_column_letter(base_col + offset)].width = 12

    worksheet.row_dimensions[1].height = 20
    worksheet.row_dimensions[2].height = 30
    worksheet.row_dimensions[3].height = 18
    worksheet.row_dimensions[4].height = 52
    return day_keys


def _write_daily_assurance_sheet(
    worksheet: Worksheet,
    rows: list[MergedNomenclatureRow],
    detailed: DetailedScheduleExtract,
) -> None:
    """Лист «обеспечение по дням»: шапка + данные + формулы прогноза + CF."""
    year, month = detailed.year, detailed.month
    if year <= 0 or month <= 0:
        today = date.today()
        year, month = today.year, today.month

    day_keys = _build_daily_assurance_header(worksheet, year, month)
    data_alignment = Alignment(vertical="top", wrap_text=True, horizontal="left")
    thin = Side(style="thin", color="B0B0B0")
    data_border = Border(left=thin, right=thin, top=thin, bottom=thin)
    width_a = float(worksheet.column_dimensions["A"].width or 50)
    width_b = float(worksheet.column_dimensions["B"].width or 43)
    width_c = float(worksheet.column_dimensions["C"].width or 36)
    forecast_cols: list[int] = []

    for offset, row in enumerate(rows):
        excel_row = _RESULT_DATA_START_ROW + offset
        values: dict[int, Any] = {
            1: row.nomenclature,
            2: "; ".join(row.products),
            3: row.supplier,
            4: row.unit,
            5: row.price,
            6: 0.0 if row.stock is None else row.stock,
        }
        for day_index, day_key in enumerate(day_keys):
            base_col = _FIXED_RESULT_COLS + 1 + day_index * 3
            demand_col = base_col
            receipt_col = base_col + 1
            forecast_col = base_col + 2
            values[demand_col] = float(row.daily_demand.get(day_key, 0.0))
            values[receipt_col] = float(row.daily_receipts.get(day_key, 0.0))
            demand_letter = get_column_letter(demand_col)
            receipt_letter = get_column_letter(receipt_col)
            if day_index == 0:
                values[forecast_col] = (
                    f"={_STOCK_COL_LETTER}{excel_row}"
                    f"+{receipt_letter}{excel_row}-{demand_letter}{excel_row}"
                )
            else:
                prev_forecast_letter = get_column_letter(base_col - 1)
                values[forecast_col] = (
                    f"={prev_forecast_letter}{excel_row}"
                    f"+{receipt_letter}{excel_row}-{demand_letter}{excel_row}"
                )
            if offset == 0:
                forecast_cols.append(forecast_col)

        last_col = _FIXED_RESULT_COLS + len(day_keys) * 3
        for col_idx in range(1, last_col + 1):
            cell = worksheet.cell(excel_row, col_idx, values.get(col_idx))
            cell.alignment = data_alignment
            cell.border = data_border

        worksheet.row_dimensions[excel_row].height = _estimate_wrapped_row_height(
            [str(values[1] or ""), str(values[2] or ""), str(values[3] or "")],
            [width_a, width_b, width_c],
        )

    if rows and forecast_cols:
        last_data_row = _RESULT_DATA_START_ROW + len(rows) - 1
        _apply_forecast_deficit_formatting(
            worksheet,
            forecast_cols,
            _RESULT_DATA_START_ROW,
            last_data_row,
        )

    worksheet.freeze_panes = f"A{_RESULT_DATA_START_ROW}"
    logger.info(
        "document_analysis_agent.daily_assurance_sheet_written",
        rows=len(rows),
        month=f"{year:04d}-{month:02d}",
        days=len(day_keys),
        forecast_cols=len(forecast_cols),
    )


def _estimate_wrapped_row_height(texts: list[str], column_widths: list[float]) -> float:
    """Оценивает высоту строки при wrap_text, чтобы текст не вылезал визуально."""
    lines = 1
    for text, width in zip(texts, column_widths, strict=False):
        content = _clean_text(text)
        if not content:
            continue
        chars_per_line = max(int(width * 1.05), 8)
        # учитываем явные переносы и длину строки
        soft_lines = 0
        for part in content.split("\n"):
            soft_lines += max(1, (len(part) + chars_per_line - 1) // chars_per_line)
        lines = max(lines, soft_lines)
    return float(min(220, max(18, lines * 15)))


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _clean_text(value).replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_nomenclature_mapping() -> list[_MappingRow]:
    if not _MAPPING_FILE.exists():
        return []
    workbook = load_workbook(_MAPPING_FILE, data_only=True)
    sheet = workbook.active
    headers = {
        col_idx: _normalize(sheet.cell(1, col_idx).value)
        for col_idx in range(1, sheet.max_column + 1)
    }
    nom_col = _pick_column(headers, ["номенклатура"]) or 1
    contract_nom_col = _pick_column(headers, ["номенклатура контракта"]) or 4
    contract_col = _pick_column(headers, ["договор"]) or 3

    rows: list[_MappingRow] = []
    seen: set[str] = set()
    for row_idx in range(2, sheet.max_row + 1):
        nomenclature = _clean_text(sheet.cell(row_idx, nom_col).value)
        if not nomenclature:
            continue
        key = _normalize(nomenclature)
        # Схлопываем дубликаты по договорам — оставляем первое вхождение A
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            _MappingRow(
                nomenclature=nomenclature,
                contract_nomenclature=_clean_text(sheet.cell(row_idx, contract_nom_col).value),
                contract=_clean_text(sheet.cell(row_idx, contract_col).value),
            )
        )
    return rows


def _load_spec_sheet_names() -> list[str]:
    if not _SPECS_FILE.exists():
        return []
    workbook = load_workbook(_SPECS_FILE, read_only=True)
    return [name for name in workbook.sheetnames if _clean_text(name)]


def _match_schedule_to_nomenclatures_locally(
    products: list[str], mapping_rows: list[_MappingRow]
) -> dict[str, tuple[str | None, float]]:
    result: dict[str, tuple[str | None, float]] = {}
    for product in products:
        heuristic = _heuristic_nomenclature(product, mapping_rows)
        if heuristic:
            result[product] = (heuristic, 0.95)
            continue
        best_name: str | None = None
        best_score = 0.0
        for row in mapping_rows:
            score_a = _product_match_score(product, row.nomenclature)
            score_d = _product_match_score(product, row.contract_nomenclature) if row.contract_nomenclature else 0.0
            score = max(score_a, score_d * 0.95)
            if score > best_score:
                best_score = score
                best_name = row.nomenclature
        result[product] = (best_name, best_score)
    return result


def _heuristic_nomenclature(product: str, mapping_rows: list[_MappingRow]) -> str | None:
    text = _match_key(product)
    by_norm = {_match_key(row.nomenclature): row.nomenclature for row in mapping_rows}
    is_sokol_r = bool(re.search(r"сокол\s*-?\s*р\b", text) or "z8" in text or "z40" in text or "мини" in text)
    is_is_family = bool("ис" in text or "ист" in text or "сокол с" in text)
    is_i_family = bool(re.search(r"\bи\b", text)) and not is_is_family and not is_sokol_r

    def find_contains(*needles: str) -> str | None:
        for key, name in by_norm.items():
            if all(needle in key for needle in needles):
                return name
        return None

    # Сначала ИС / И, потом Сокол-Р — иначе «перехватчик» ломает эвристику по букве «р»
    if is_is_family and "ночь" in text:
        return find_contains("сокол с", "ночь") or find_contains("ночь", "ascent")
    if is_is_family and "день" in text:
        return find_contains("сокол с", "день")

    if is_i_family and "ночь" in text:
        if "ascent" in text or "1.01" in text:
            return find_contains("т-1.01") or find_contains("ascent", "ночь")
        for key, name in by_norm.items():
            if (
                "ночь" in key
                and "т-1.01" not in key
                and "сокол с" not in key
                and "ист" not in key
                and "ascent" not in key
            ):
                return name
        return None
    if is_i_family and "день" in text:
        if "ascent" in text or "1.01" in text:
            return find_contains("и-1.01") or find_contains("ascent", "день")
        for key, name in by_norm.items():
            if (
                "день" in key
                and "сокол с" not in key
                and "и-1.01" not in key
                and "ascent" not in key
                and "бпла" not in key
            ):
                return name
        return None

    if is_sokol_r:
        if "z8" in text or "мини" in text:
            return find_contains("мини") or find_contains("z8")
        if "z40" in text:
            return find_contains("сокол-р") or next(
                (name for key, name in by_norm.items() if "сокол" in key and "р" in key and "мини" not in key and "бпла" in key),
                None,
            )
        return find_contains("сокол-р")

    if "катапульта" in text:
        if "2.0" in text or re.search(r"v\s*2\b", text) or re.search(r"\b2\b", text):
            return find_contains("катапульта 2.0") or find_contains("катапульта 2")
        return find_contains("катапульта 1.0") or find_contains("катапульта 1")

    if "нсу" in text:
        if "2.0" in text or re.search(r"нсу\s*2", text):
            if "ascent" in text:
                return find_contains("нсу 2.0", "ascent") or find_contains("нсу 2.0")
            return next(
                (name for key, name in by_norm.items() if "нсу 2.0" in key and "ascent" not in key),
                find_contains("нсу 2.0"),
            )
        if "ascent" in text or "исполнение" in text:
            return find_contains("исполнение 2")
        # НСУ 1 без ascent → базовая 1.0, не «Исполнение 2»
        return next(
            (
                name
                for key, name in by_norm.items()
                if "нсу 1.0" in key and "исполнение" not in key and "ascent" not in key
            ),
            find_contains("нсу 1.0"),
        )

    return None


def _match_nomenclature_to_sheet(
    schedule_product: str, nomenclature: str, sheet_names: list[str]
) -> tuple[str | None, str]:
    heuristic = _heuristic_sheet(schedule_product, nomenclature, sheet_names)
    if heuristic:
        return heuristic, "эвристика листа"

    candidates = sheet_names
    # ascent только из изделия графика — mapping часто содержит ascent-варианты шире
    prefer_ascent = "ascent" in _match_key(schedule_product) or "1.01" in _match_key(schedule_product)
    scored: list[tuple[str, float]] = []
    for sheet in candidates:
        score = max(
            _product_match_score(nomenclature, sheet),
            _product_match_score(_strip_fpv_prefix(nomenclature), sheet),
            _product_match_score(schedule_product, sheet) * 0.9,
        )
        if prefer_ascent and "ascent" in _match_key(sheet):
            score += 0.08
        if not prefer_ascent and ("ascent" in _match_key(sheet) or "исполнение" in _match_key(sheet)):
            score -= 0.12
        scored.append((sheet, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    if not scored or scored[0][1] < 0.45:
        return None, "лист не найден локально"
    # неоднозначность: два близких результата
    if len(scored) > 1 and scored[0][1] - scored[1][1] < 0.08 and scored[1][1] >= 0.45:
        return None, "неоднозначный выбор листа"
    return scored[0][0], f"локальный матч листа ({scored[0][1]:.2f})"


def _heuristic_sheet(schedule_product: str, nomenclature: str, sheet_names: list[str]) -> str | None:
    sched = _match_key(schedule_product)
    nom = _match_key(nomenclature)
    text = f"{sched} {nom}".strip()
    sheets = {_match_key(name): name for name in sheet_names}
    is_sokol_r = bool(re.search(r"сокол\s*-?\s*р\b", text) or "z8" in text or "z40" in text or "мини" in sched)
    is_is_family = bool("ис" in text or "ист" in text or "сокол с" in text)
    is_i_family = bool(re.search(r"\bи\b", text)) and not is_is_family and not is_sokol_r
    # ascent/исполнение берём в первую очередь из изделия графика, не из mapping
    want_ascent = "ascent" in sched or "1.01" in sched or "исполнение" in sched

    def pick(*needles: str) -> str | None:
        for key, name in sheets.items():
            if all(needle in key for needle in needles):
                return name
        return None

    if is_sokol_r:
        if "z8" in text or "мини" in text:
            return pick("мини", "z8") or pick("мини")
        if "z40" in text:
            return pick("z-40") or pick("z40")
        return pick("сокол р")

    if is_is_family and "ночь" in text:
        return pick("сокол с", "ночь") or pick("ночь", "ascent")
    if is_is_family and "день" in text:
        return pick("ис", "день")

    if is_i_family and "ночь" in text:
        if want_ascent:
            return pick("ночь", "ascent") or pick("ночь", "1.01")
        return next(
            (
                name
                for key, name in sheets.items()
                if "ночь" in key and "ascent" not in key and "сокол с" not in key and "ис" not in key
            ),
            None,
        )
    if is_i_family and "день" in text:
        if want_ascent:
            return pick("день", "ascent") or pick("день", "1.01")
        return next(
            (
                name
                for key, name in sheets.items()
                if "день" in key and "ascent" not in key and "ис" not in key and "сокол с" not in key
            ),
            None,
        )

    if "катапульта" in text:
        return pick("катапульта")
    if "нсу" in text:
        if "2.0" in sched or "2.1" in sched or re.search(r"нсу\s*2", sched):
            if want_ascent:
                return pick("нсу-2.0", "ascent") or pick("2.0", "ascent")
            return next(
                (name for key, name in sheets.items() if "нсу" in key and "2.0" in key and "ascent" not in key),
                pick("нсу-2.0"),
            )
        if want_ascent:
            return pick("исполнение 2")
        return next(
            (
                name
                for key, name in sheets.items()
                if "нсу" in key
                and ("1.0" in key or re.search(r"\b1\b", key))
                and "2.0" not in key
                and "ascent" not in key
                and "исполнение" not in key
            ),
            None,
        )
    return None


async def _match_schedule_to_nomenclatures_with_lm(
    products: list[str], mapping_rows: list[_MappingRow]
) -> dict[str, str]:
    if not products:
        return {}
    payload = _lm_settings()
    if payload is None:
        return {}
    base_url, model = payload
    options = [
        {
            "nomenclature": row.nomenclature,
            "contract_nomenclature": row.contract_nomenclature,
        }
        for row in mapping_rows
    ]
    prompt = (
        "Ты сопоставляешь изделия из графика производства с номенклатурой из таблицы сопоставления. "
        "Для каждого schedule_product выбери ровно одну nomenclature из OPTIONS (поле nomenclature). "
        "Учитывай варианты: И/ИС/ИС-Т/СОКОЛ С, день/ночь, ascent, Z8/Z40, мини, НСУ, Катапульта. "
        "Если нет подходящего — верни nomenclature=null. "
        "Верни строго JSON: "
        '{"matches":[{"schedule_product":"...","nomenclature":"...","reason":"..."}]}'
        f"\n\nSCHEDULE_PRODUCTS={json.dumps(products, ensure_ascii=False)}"
        f"\n\nOPTIONS={json.dumps(options, ensure_ascii=False)}"
    )
    try:
        data = await _post_lm_json(base_url, model, prompt, timeout=settings.AVEON_LM_STUDIO_TIMEOUT_SECONDS)
        matches = data.get("matches")
        if not isinstance(matches, list):
            return {}
        allowed = {_normalize(row.nomenclature): row.nomenclature for row in mapping_rows}
        result: dict[str, str] = {}
        for item in matches:
            if not isinstance(item, dict):
                continue
            product = _clean_text(item.get("schedule_product"))
            nomenclature = _clean_text(item.get("nomenclature"))
            if not product or not nomenclature:
                continue
            resolved = allowed.get(_normalize(nomenclature))
            if resolved:
                result[product] = resolved
        return result
    except Exception as exc:
        logger.warning("document_analysis_agent.schedule_nomenclature_lm_failed", error=str(exc))
        return {}


async def _match_nomenclature_to_sheet_with_lm(
    nomenclature: str, sheet_names: list[str]
) -> str | None:
    payload = _lm_settings()
    if payload is None:
        return None
    base_url, model = payload
    prompt = (
        "Выбери один лист спецификации (sheet_name) для данной номенклатуры. "
        "Имя листа может быть укороченным вариантом номенклатуры. "
        "Верни строго JSON: {\"sheet_name\":\"...\",\"reason\":\"...\"} "
        "или {\"sheet_name\":null}."
        f"\n\nNOMENCLATURE={json.dumps(nomenclature, ensure_ascii=False)}"
        f"\n\nSHEETS={json.dumps(sheet_names, ensure_ascii=False)}"
    )
    try:
        data = await _post_lm_json(base_url, model, prompt, timeout=settings.AVEON_LM_STUDIO_TIMEOUT_SECONDS)
        sheet_name = _clean_text(data.get("sheet_name"))
        if not sheet_name:
            return None
        allowed = {_normalize(name): name for name in sheet_names}
        return allowed.get(_normalize(sheet_name))
    except Exception as exc:
        logger.warning("document_analysis_agent.nomenclature_sheet_lm_failed", error=str(exc))
        return None


def _product_match_score(left: str, right: str) -> float:
    a = _match_key(left)
    b = _match_key(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return 0.72 + 0.2 * (len(shorter) / max(len(longer), 1))

    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return 0.0
    noise = {"fpv", "перехватчик", "бпла", "изделие"}
    a_core = a_tokens - noise
    b_core = b_tokens - noise
    if not a_core or not b_core:
        a_core, b_core = a_tokens, b_tokens
    overlap = len(a_core & b_core) / len(a_core | b_core)

    # бонусы за ключевые маркеры
    markers = ("день", "ночь", "ascent", "ис", "ист", "z8", "z40", "мини", "нсу", "катапульта")
    marker_bonus = 0.0
    for marker in markers:
        if marker in a and marker in b:
            marker_bonus += 0.04
        elif marker in a and marker not in b:
            marker_bonus -= 0.03
        elif marker in b and marker not in a:
            marker_bonus -= 0.02

    # И vs ИС: штраф за путаницу
    if ("ис" in a or "ист" in a or "сокол с" in a) != ("ис" in b or "ист" in b or "сокол с" in b):
        if re.search(r"\bи\b", a) or re.search(r"\bи\b", b):
            marker_bonus -= 0.15

    return max(0.0, min(1.0, overlap + marker_bonus))


def _best_text_match(value: str, candidates: list[str]) -> tuple[str | None, float]:
    best_name: str | None = None
    best_score = 0.0
    for candidate in candidates:
        score = _product_match_score(value, candidate)
        if score > best_score:
            best_score = score
            best_name = candidate
    return best_name, best_score


def _strip_fpv_prefix(value: str) -> str:
    text = _clean_text(value)
    text = re.sub(r"(?i)^fpv[-\s]*перехватчик\s*", "", text).strip()
    text = re.sub(r"(?i)^бпла\s*", "", text).strip()
    return text


def _match_key(value: str) -> str:
    text = _normalize(value)
    text = text.replace("«", " ").replace("»", " ").replace('"', " ").replace("'", " ")
    text = text.replace("ё", "е")
    # нормализация вариантов ИС-Т / ИСТ / СОКОЛ С
    text = re.sub(r"\bис\s*-\s*т\b", " ист ", text)
    text = re.sub(r"\bис\s*т\b", " ист ", text)
    text = re.sub(r"\bсокол\s*с\b", " сокол с ист ", text)
    text = re.sub(r"z\s*-\s*40", "z40", text)
    text = re.sub(r"z\s*40", "z40", text)
    text = re.sub(r"z\s*8", "z8", text)
    text = re.sub(r"[^a-zа-я0-9.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _short(value: str, limit: int = 240) -> str:
    text = _clean_text(value)
    return text if len(text) <= limit else f"{text[:limit].rstrip()}…"


def _normalize(value: Any) -> str:
    return _clean_text(value).replace("ё", "е").lower()
