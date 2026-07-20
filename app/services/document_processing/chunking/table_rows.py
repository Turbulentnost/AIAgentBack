from __future__ import annotations

from dataclasses import dataclass


_HEADER_HINT_WORDS = {
    "уровень",
    "назначение",
    "технология",
    "компонент",
    "название",
    "описание",
    "тип",
    "рекомендуемая",
    "платформы",
    "категория",
    "функция",
    "модуль",
    "сервис",
    "параметр",
}


@dataclass(frozen=True)
class TableStructure:
    caption: str | None
    headers: list[str]
    data_rows: list[list[str]]


def normalize_table_rows(rows: list[list[str | object]]) -> list[list[str]]:
    return [["" if cell is None else str(cell).strip() for cell in row] for row in rows]


def detect_table_structure(rows: list[list[str | object]]) -> TableStructure:
    """Определяет заголовки и строки данных в таблице DOCX/XLSX."""
    cleaned = normalize_table_rows(rows)
    non_empty = [row for row in cleaned if any(cell for cell in row)]
    if not non_empty:
        return TableStructure(caption=None, headers=[], data_rows=[])

    if len(non_empty) == 1:
        row = non_empty[0]
        return TableStructure(
            caption=None,
            headers=_generic_headers(len(row)),
            data_rows=[row],
        )

    header_index = _find_header_row_index(non_empty)
    caption = _join_caption_rows(non_empty[:header_index]) if header_index > 0 else None
    headers = _normalize_headers(non_empty[header_index])
    data_rows = non_empty[header_index + 1 :]

    if not data_rows:
        # Одна строка без явных данных — считаем её строкой таблицы с generic-заголовками.
        return TableStructure(
            caption=caption,
            headers=_generic_headers(len(non_empty[0])),
            data_rows=[non_empty[0]],
        )

    return TableStructure(caption=caption, headers=headers, data_rows=data_rows)


def build_table_row_embedding_text(
    *,
    section_title: str | None,
    table_caption: str | None,
    headers: list[str],
    row_values: list[str],
) -> str:
    """Текст для embedding: связь колонок и значений в осмысленном контексте."""
    lines: list[str] = []
    if section_title:
        lines.append(f"Раздел {section_title.rstrip('.')}.")
    if table_caption:
        lines.append(f"{table_caption.rstrip('.')}.")
    for header, value in _zip_columns(headers, row_values):
        label = header.strip() or "Значение"
        cell = value.strip()
        if cell:
            lines.append(f"{label}: {cell.rstrip('.')}.")
    return "\n".join(lines)


def build_table_row_display_text(
    *,
    headers: list[str],
    row_values: list[str],
) -> str:
    """Текст для UI: строка таблицы в читаемом виде (пара «колонка — значение»)."""
    pairs: list[str] = []
    for header, value in _zip_columns(headers, row_values):
        label = header.strip() or "—"
        cell = value.strip() or "—"
        pairs.append(f"{label} — {cell}")
    return "\n".join(pairs)


def _find_header_row_index(rows: list[list[str]]) -> int:
    best_index = 0
    best_score = -1.0
    for index, row in enumerate(rows[:4]):
        next_row = rows[index + 1] if index + 1 < len(rows) else None
        score = _header_row_score(row, next_row)
        if score > best_score:
            best_score = score
            best_index = index
    return best_index


def _header_row_score(row: list[str], next_row: list[str] | None) -> float:
    filled = [cell for cell in row if cell]
    if len(filled) < 2:
        return 0.0

    score = 1.0
    avg_len = sum(len(cell) for cell in filled) / len(filled)
    if avg_len > 120:
        score -= 0.45
    if any(cell.endswith(".") and len(cell) > 40 for cell in filled):
        score -= 0.35
    if any(any(word in cell.lower() for word in _HEADER_HINT_WORDS) for cell in filled):
        score += 0.45
    if next_row:
        next_filled = [cell for cell in next_row if cell]
        if next_filled:
            next_avg = sum(len(cell) for cell in next_filled) / len(next_filled)
            if avg_len < next_avg:
                score += 0.25
    return score


def _join_caption_rows(rows: list[list[str]]) -> str | None:
    parts: list[str] = []
    for row in rows:
        filled = [cell for cell in row if cell]
        if filled:
            parts.append(" ".join(filled))
    caption = " ".join(parts).strip()
    return caption or None


def _normalize_headers(row: list[str]) -> list[str]:
    headers = [cell.strip() for cell in row]
    if not any(headers):
        return _generic_headers(len(row))
    return [header or f"Колонка {index + 1}" for index, header in enumerate(headers)]


def _generic_headers(count: int) -> list[str]:
    return [f"Колонка {index + 1}" for index in range(max(count, 1))]


def _zip_columns(headers: list[str], values: list[str]) -> list[tuple[str, str]]:
    count = max(len(headers), len(values), 1)
    padded_headers = headers + [""] * max(0, count - len(headers))
    padded_values = values + [""] * max(0, count - len(values))
    pairs: list[tuple[str, str]] = []
    for header, value in zip(padded_headers[:count], padded_values[:count], strict=False):
        if header.strip() or value.strip():
            pairs.append((header, value))
    return pairs


def is_probably_table_block(block_type: str, metadata: dict) -> bool:
    if block_type == "table" and metadata.get("rows"):
        return True
    if block_type == "sheet" and metadata.get("rows"):
        return True
    return False
