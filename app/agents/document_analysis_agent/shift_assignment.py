"""Сменное задание менеджеру по закупкам: критичные точки логистики + дефицит на неделе."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.agents.document_analysis_agent.excel_service import (
    DetailedScheduleExtract,
    LogisticsRiskBoard,
    MergedNomenclatureRow,
    _lm_settings,
    _post_lm_json,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

SHIFT_ASSIGNMENT_FILE_NAME = "сменное_задание_закупки.xlsx"
_RISK_LEVELS_INCLUDE = frozenset({"critical", "high"})
_LM_TIMEOUT_SECONDS = 40

_TITLE_FILL = PatternFill(start_color="FF1F4E78", end_color="FF1F4E78", fill_type="solid")
_TITLE_FONT = Font(bold=True, color="FFFFFFFF", size=14)
_HEADER_FILL = PatternFill(start_color="FF5B9BD5", end_color="FF5B9BD5", fill_type="solid")
_HEADER_FONT = Font(bold=True, color="FFFFFFFF", size=11)
_SUBTITLE_FILL = PatternFill(start_color="FFD9EAF7", end_color="FFD9EAF7", fill_type="solid")
_SUBTITLE_FONT = Font(color="FF1F1F1F", size=11)
_DEFICIT_FILL = PatternFill(start_color="FFF4CCCC", end_color="FFF4CCCC", fill_type="solid")
_DEFICIT_FONT = Font(color="FF9C0006")
_THIN = Border(
    left=Side(style="thin", color="B0B0B0"),
    right=Side(style="thin", color="B0B0B0"),
    top=Side(style="thin", color="B0B0B0"),
    bottom=Side(style="thin", color="B0B0B0"),
)
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

_RISK_LEVEL_LABELS = {
    "critical": "Критический",
    "high": "Высокий",
    "medium": "Средний",
    "low": "Низкий",
}

# Официальные шаблоны действий: (stage_key, risk_level) → текст
_ACTION_TEMPLATES: dict[tuple[str, str], str] = {
    (
        "loading_dispatch",
        "critical",
    ): (
        "Немедленно связаться с поставщиком, подтвердить факт отгрузки и актуальный ETA; "
        "при отсутствии подтверждения — эскалировать вопрос руководителю закупок."
    ),
    (
        "loading_dispatch",
        "high",
    ): (
        "Связаться с поставщиком, уточнить готовность к отгрузке и плановую дату отправки; "
        "зафиксировать ответ в переписке."
    ),
    (
        "msk_arrival",
        "critical",
    ): (
        "Срочно уточнить у поставщика/перевозчика статус прибытия в Москву и причины задержки; "
        "согласовать корректирующие меры и обновить контрольную дату."
    ),
    (
        "msk_arrival",
        "high",
    ): (
        "Проверить статус поставки на участке прибытия в Москву; "
        "убедиться в соблюдении согласованного окна и при отклонении запросить план восстановления."
    ),
    (
        "customs_clearance",
        "critical",
    ): (
        "Срочно проверить статус таможенного оформления, запросить у ответственных актуальные "
        "документы и прогноз выпуска; при блокировке — эскалировать."
    ),
    (
        "customs_clearance",
        "high",
    ): (
        "Уточнить ход таможенного оформления и комплектность документов; "
        "убедиться, что выпуск ожидается в пределах контрольного окна."
    ),
    (
        "rostov_arrival",
        "critical",
    ): (
        "Немедленно уточнить статус прибытия в Ростов и готовность к приёмке; "
        "согласовать с логистикой и складом приоритетную обработку партии."
    ),
    (
        "rostov_arrival",
        "high",
    ): (
        "Проверить график прибытия в Ростов и готовность склада к приёмке; "
        "при риске срыва — согласовать ускоренную доставку/приёмку."
    ),
}

_DEFAULT_ACTION_CRITICAL = (
    "Срочно связаться с поставщиком, подтвердить статус поставки и принять меры "
    "по предотвращению срыва обеспечения производства."
)
_DEFAULT_ACTION_HIGH = (
    "Связаться с поставщиком, проверить статус поставки и убедиться в соблюдении "
    "согласованных сроков; при отклонениях запросить корректирующий план."
)


@dataclass
class ShiftRiskRow:
    id: str
    nomenclature: str
    supplier: str
    quantity: float
    stage_key: str
    stage_label: str
    window_start: str
    window_end: str
    days_remaining: int
    risk_level: str
    action: str = ""


@dataclass
class ShiftDeficitRow:
    nomenclature: str
    supplier: str
    min_forecast: float
    deficit_days: list[str] = field(default_factory=list)
    recommendation: str = ""


def _current_week_bounds(as_of: date | None = None) -> tuple[date, date]:
    as_of = as_of or date.today()
    start = as_of - timedelta(days=as_of.weekday())
    end = start + timedelta(days=6)
    return start, end


def _week_day_keys(
    detailed: DetailedScheduleExtract | None,
    as_of: date | None = None,
) -> tuple[list[str], bool, str]:
    """Дни текущей недели ∩ day_keys дневного листа. in_period=False если пересечения нет."""
    as_of = as_of or date.today()
    week_start, week_end = _current_week_bounds(as_of)
    period_note = f"{week_start.isoformat()} — {week_end.isoformat()}"
    if detailed is None or not detailed.day_keys:
        return [], False, period_note
    keys = [
        key
        for key in detailed.day_keys
        if week_start <= date.fromisoformat(key) <= week_end
    ]
    return keys, bool(keys), period_note


def _template_action(stage_key: str, risk_level: str) -> str:
    text = _ACTION_TEMPLATES.get((stage_key, risk_level))
    if text:
        return text
    if risk_level == "critical":
        return _DEFAULT_ACTION_CRITICAL
    return _DEFAULT_ACTION_HIGH


def collect_critical_risk_rows(board: LogisticsRiskBoard | None) -> list[ShiftRiskRow]:
    if board is None:
        return []
    rows: list[ShiftRiskRow] = []
    for stage in board.stages:
        for index, item in enumerate(stage.items):
            if item.risk_level not in _RISK_LEVELS_INCLUDE:
                continue
            row_id = f"{stage.key}:{index}:{item.nomenclature[:40]}"
            rows.append(
                ShiftRiskRow(
                    id=row_id,
                    nomenclature=item.nomenclature,
                    supplier=(item.supplier or "").strip() or "не указан",
                    quantity=float(item.quantity),
                    stage_key=stage.key,
                    stage_label=stage.label,
                    window_start=item.window_start or "",
                    window_end=item.window_end or item.milestone_date or "",
                    days_remaining=int(item.days_remaining),
                    risk_level=item.risk_level,
                    action=_template_action(stage.key, item.risk_level),
                )
            )
    rows.sort(key=lambda r: (0 if r.risk_level == "critical" else 1, r.days_remaining, r.nomenclature))
    return rows


def collect_week_deficit_rows(
    merged: list[MergedNomenclatureRow],
    detailed: DetailedScheduleExtract | None,
    as_of: date | None = None,
) -> tuple[list[ShiftDeficitRow], bool, str]:
    week_keys, in_period, period_note = _week_day_keys(detailed, as_of)
    if not in_period:
        return [], False, period_note

    rows: list[ShiftDeficitRow] = []
    for row in merged:
        day_vals: list[tuple[str, float]] = []
        for key in week_keys:
            value = float(row.daily_forecast.get(key, 0.0))
            if value < 0:
                day_vals.append((key, value))
        if not day_vals:
            continue
        min_forecast = min(v for _, v in day_vals)
        deficit_days = [d for d, _ in day_vals]
        nom = row.nomenclature
        supplier = (row.supplier or "").strip() or "не указан"
        days_label = ", ".join(
            date.fromisoformat(d).strftime("%d.%m.%Y") for d in deficit_days[:7]
        )
        if len(deficit_days) > 7:
            days_label += f" и ещё {len(deficit_days) - 7}"
        recommendation = (
            f"По номенклатуре «{nom}» на текущей календарной неделе ({period_note}) "
            f"плановая потребность превышает обеспеченность (остаток и ожидаемые поступления). "
            f"Прогнозируемый остаток отрицателен в следующие даты: {days_label}. "
            f"Минимальный прогноз на неделе: {min_forecast:g}. "
            f"Рекомендуется связаться с поставщиком ({supplier}), уточнить возможность "
            f"ускоренной поставки/переноса отгрузки и согласовать меры по закрытию дефицита."
        )
        rows.append(
            ShiftDeficitRow(
                nomenclature=nom,
                supplier=supplier,
                min_forecast=min_forecast,
                deficit_days=deficit_days,
                recommendation=recommendation,
            )
        )
    rows.sort(key=lambda r: (r.min_forecast, r.nomenclature))
    return rows, True, period_note


async def enrich_risk_actions_with_lm(rows: list[ShiftRiskRow]) -> None:
    """Уточняет формулировки действий через LM; при сбое оставляет шаблоны."""
    if not rows:
        return
    settings = _lm_settings()
    if settings is None:
        return
    base_url, model = settings
    payload = [
        {
            "id": row.id,
            "nomenclature": row.nomenclature,
            "supplier": row.supplier,
            "stage": row.stage_label,
            "risk_level": row.risk_level,
            "days_remaining": row.days_remaining,
            "window": f"{row.window_start} — {row.window_end}",
            "default_action": row.action,
        }
        for row in rows[:80]
    ]
    prompt = (
        "Ты помощник менеджера по закупкам производственной компании. "
        "По списку критичных точек логистики сформулируй краткие официальные "
        "рекомендуемые действия на смену (1–2 предложения, деловой русский язык). "
        "Не используй разговорный стиль. Верни JSON объекта вида:\n"
        '{"actions":[{"id":"...","action":"..."}]}\n'
        "id должен совпадать с входным. Список:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        data = await _post_lm_json(base_url, model, prompt, timeout=_LM_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — fallback на шаблоны
        logger.warning("shift_assignment.lm_actions_failed", error=str(exc))
        return

    actions = data.get("actions")
    if not isinstance(actions, list):
        return
    by_id = {
        str(item.get("id")): str(item.get("action") or "").strip()
        for item in actions
        if isinstance(item, dict) and item.get("id") and item.get("action")
    }
    updated = 0
    for row in rows:
        text = by_id.get(row.id)
        if text:
            row.action = text
            updated += 1
    logger.info("shift_assignment.lm_actions_applied", updated=updated, total=len(rows))


def _style_title_row(ws: Any, last_col: int, title: str) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    cell = ws.cell(1, 1, title)
    cell.fill = _TITLE_FILL
    cell.font = _TITLE_FONT
    cell.alignment = _CENTER
    for col in range(2, last_col + 1):
        c = ws.cell(1, col)
        c.fill = _TITLE_FILL
        c.font = _TITLE_FONT


def _write_header_row(ws: Any, row_idx: int, headers: list[str]) -> None:
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row_idx, col, text)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _CENTER
        cell.border = _THIN


def _autosize(ws: Any, widths: list[float]) -> None:
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _write_summary_sheet(
    wb: Workbook,
    *,
    as_of: date,
    detailed: DetailedScheduleExtract | None,
    risk_rows: list[ShiftRiskRow],
    deficit_rows: list[ShiftDeficitRow],
    week_in_period: bool,
    week_period: str,
) -> None:
    ws = wb.active
    ws.title = "Сводка смены"
    _style_title_row(ws, 2, "Сменное задание менеджеру по закупкам")
    ws.merge_cells("A2:B2")
    sub = ws["A2"]
    sub.value = (
        "Документ сформирован автоматически по результатам анализа обеспеченности и "
        "графиков отгрузок. Предназначен для приоритетных действий на смене."
    )
    sub.fill = _SUBTITLE_FILL
    sub.font = _SUBTITLE_FONT
    sub.alignment = _LEFT
    ws["B2"].fill = _SUBTITLE_FILL

    daily_period = "не определён"
    if detailed and detailed.year and detailed.month:
        daily_period = f"{detailed.year:04d}-{detailed.month:02d} ({len(detailed.day_keys)} дн.)"

    critical_n = sum(1 for r in risk_rows if r.risk_level == "critical")
    high_n = sum(1 for r in risk_rows if r.risk_level == "high")

    facts = [
        ("Дата формирования", as_of.strftime("%d.%m.%Y")),
        ("Период дневного обеспечения", daily_period),
        ("Текущая календарная неделя", week_period),
        (
            "Неделя в периоде дневного листа",
            "да" if week_in_period else "нет (вне периода — лист дефицита без позиций)",
        ),
        ("Критических точек логистики", str(critical_n)),
        ("Точек высокого риска", str(high_n)),
        ("Номенклатур с дефицитом на неделе", str(len(deficit_rows))),
    ]
    _write_header_row(ws, 4, ["Показатель", "Значение"])
    for offset, (label, value) in enumerate(facts):
        r = 5 + offset
        ws.cell(r, 1, label).border = _THIN
        ws.cell(r, 2, value).border = _THIN
        ws.cell(r, 1).alignment = _LEFT
        ws.cell(r, 2).alignment = _LEFT

    instr_row = 5 + len(facts) + 1
    ws.merge_cells(start_row=instr_row, start_column=1, end_row=instr_row, end_column=2)
    instr = ws.cell(
        instr_row,
        1,
        "Порядок работы: 1) отработать лист «Критические точки»; "
        "2) проверить лист «Дефицит на неделе» (строки выделены) и согласовать поставки; "
        "3) зафиксировать результаты переговоров с поставщиками.",
    )
    instr.fill = _SUBTITLE_FILL
    instr.font = _SUBTITLE_FONT
    instr.alignment = _LEFT
    ws.cell(instr_row, 2).fill = _SUBTITLE_FILL
    _autosize(ws, [42, 56])
    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = 36


def _write_risks_sheet(wb: Workbook, rows: list[ShiftRiskRow]) -> None:
    ws = wb.create_sheet("Критические точки")
    headers = [
        "Номенклатура",
        "Поставщик",
        "Кол-во",
        "Стадия",
        "Окно с",
        "Окно по",
        "Дней до края",
        "Уровень риска",
        "Рекомендуемое действие",
    ]
    _style_title_row(ws, len(headers), "Критические и высокие риски логистики")
    _write_header_row(ws, 2, headers)
    if not rows:
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=len(headers))
        cell = ws.cell(3, 1, "На расчётную дату критичных и высоких точек риска не выявлено.")
        cell.alignment = _LEFT
        cell.fill = _SUBTITLE_FILL
    else:
        for offset, row in enumerate(rows):
            r = 3 + offset
            values = [
                row.nomenclature,
                row.supplier,
                row.quantity,
                row.stage_label,
                row.window_start,
                row.window_end,
                row.days_remaining,
                _RISK_LEVEL_LABELS.get(row.risk_level, row.risk_level),
                row.action,
            ]
            for col, value in enumerate(values, start=1):
                cell = ws.cell(r, col, value)
                cell.border = _THIN
                cell.alignment = _LEFT if col in (1, 2, 4, 9) else _CENTER
                if row.risk_level == "critical":
                    cell.fill = _DEFICIT_FILL
                    if col == 9:
                        cell.font = _DEFICIT_FONT
    _autosize(ws, [36, 22, 10, 18, 12, 12, 12, 14, 52])
    ws.freeze_panes = "A3"
    ws.row_dimensions[1].height = 22


def _write_deficit_sheet(
    wb: Workbook,
    rows: list[ShiftDeficitRow],
    *,
    week_in_period: bool,
    week_period: str,
) -> None:
    ws = wb.create_sheet("Дефицит на неделе")
    headers = [
        "Номенклатура",
        "Поставщик",
        "Мин. прогноз на неделе",
        "Дни дефицита",
        "Рекомендация",
    ]
    _style_title_row(
        ws,
        len(headers),
        f"Дефицит прогнозируемого остатка на текущей неделе ({week_period})",
    )
    _write_header_row(ws, 2, headers)
    if not week_in_period:
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=len(headers))
        cell = ws.cell(
            3,
            1,
            "Текущая календарная неделя находится вне периода листа «обеспечение (месяц)». "
            "Позиции дефицита по дням для этой недели не формировались.",
        )
        cell.alignment = _LEFT
        cell.fill = _SUBTITLE_FILL
    elif not rows:
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=len(headers))
        cell = ws.cell(
            3,
            1,
            "На текущей календарной неделе номенклатур с отрицательным прогнозируемым остатком не выявлено.",
        )
        cell.alignment = _LEFT
        cell.fill = _SUBTITLE_FILL
    else:
        for offset, row in enumerate(rows):
            r = 3 + offset
            days_label = "; ".join(
                date.fromisoformat(d).strftime("%d.%m.%Y") for d in row.deficit_days
            )
            values = [
                row.nomenclature,
                row.supplier,
                row.min_forecast,
                days_label,
                row.recommendation,
            ]
            for col, value in enumerate(values, start=1):
                cell = ws.cell(r, col, value)
                cell.border = _THIN
                cell.fill = _DEFICIT_FILL
                cell.font = _DEFICIT_FONT
                cell.alignment = _LEFT if col in (1, 2, 4, 5) else _CENTER
            ws.row_dimensions[r].height = 48
    _autosize(ws, [36, 22, 14, 28, 60])
    ws.freeze_panes = "A3"
    ws.row_dimensions[1].height = 22


def write_shift_assignment_xlsx(
    *,
    as_of: date,
    detailed: DetailedScheduleExtract | None,
    risk_rows: list[ShiftRiskRow],
    deficit_rows: list[ShiftDeficitRow],
    week_in_period: bool,
    week_period: str,
) -> bytes:
    wb = Workbook()
    _write_summary_sheet(
        wb,
        as_of=as_of,
        detailed=detailed,
        risk_rows=risk_rows,
        deficit_rows=deficit_rows,
        week_in_period=week_in_period,
        week_period=week_period,
    )
    _write_risks_sheet(wb, risk_rows)
    _write_deficit_sheet(
        wb,
        deficit_rows,
        week_in_period=week_in_period,
        week_period=week_period,
    )
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def build_shift_assignment_xlsx(
    merged: list[MergedNomenclatureRow],
    logistics_risks: LogisticsRiskBoard | None,
    detailed: DetailedScheduleExtract | None,
    as_of: date | None = None,
) -> bytes:
    """Собирает xlsx сменного задания (шаблоны + опционально LM для действий)."""
    as_of = as_of or date.today()
    risk_rows = collect_critical_risk_rows(logistics_risks)
    deficit_rows, week_in_period, week_period = collect_week_deficit_rows(
        merged, detailed, as_of
    )
    await enrich_risk_actions_with_lm(risk_rows)
    data = write_shift_assignment_xlsx(
        as_of=as_of,
        detailed=detailed,
        risk_rows=risk_rows,
        deficit_rows=deficit_rows,
        week_in_period=week_in_period,
        week_period=week_period,
    )
    logger.info(
        "document_analysis_agent.shift_assignment_built",
        bytes=len(data),
        risk_rows=len(risk_rows),
        deficit_rows=len(deficit_rows),
        week_in_period=week_in_period,
        week_period=week_period,
    )
    return data
