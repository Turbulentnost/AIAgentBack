from __future__ import annotations

import json
import re
from typing import Any


def strip_code_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def repair_truncated_json(text: str) -> str:
    candidate = text.rstrip().rstrip(",")
    open_braces = candidate.count("{") - candidate.count("}")
    open_brackets = candidate.count("[") - candidate.count("]")
    if open_braces <= 0 and open_brackets <= 0:
        return candidate
    return candidate + ("]" * max(0, open_brackets)) + ("}" * max(0, open_braces))


def try_load_vision_payload(text: str) -> dict[str, Any] | None:
    stripped = strip_code_fence(text)
    if not stripped.startswith("{"):
        return None
    for candidate in (stripped, repair_truncated_json(stripped)):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _parse_json_array(raw: str) -> list[Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, list) else None


def salvage_pages_payload(text: str) -> dict[str, Any] | None:
    """Извлекает страницы из обрезанного или частично битого JSON ответа vision-модели."""
    stripped = strip_code_fence(text)
    pages: list[dict[str, Any]] = []
    page_pattern = re.compile(
        r'"page_number"\s*:\s*(?P<num>\d+)\s*,\s*"text_blocks"\s*:\s*(?P<blocks>\[[\s\S]*?\])\s*,\s*"tables"\s*:\s*(?P<tables>\[[\s\S]*?\])',
        re.MULTILINE,
    )
    for match in page_pattern.finditer(stripped):
        text_blocks_raw = _parse_json_array(match.group("blocks")) or []
        tables_raw = _parse_json_array(match.group("tables")) or []
        pages.append(
            {
                "page_number": int(match.group("num")),
                "text_blocks": text_blocks_raw,
                "tables": tables_raw,
                "quality_notes": "",
            }
        )
    if pages:
        return {"pages": pages}

    single_pattern = re.compile(
        r'"text_blocks"\s*:\s*(?P<blocks>\[[\s\S]*?\])\s*,\s*"tables"\s*:\s*(?P<tables>\[[\s\S]*?\])',
        re.MULTILINE,
    )
    single = single_pattern.search(stripped)
    if single:
        return {
            "text_blocks": _parse_json_array(single.group("blocks")) or [],
            "tables": _parse_json_array(single.group("tables")) or [],
            "quality_notes": "",
        }
    return None


def normalize_text_blocks(payload: dict[str, Any]) -> list[str]:
    blocks_raw = payload.get("text_blocks")
    if isinstance(blocks_raw, list):
        blocks = [str(item).strip() for item in blocks_raw if str(item).strip()]
        if blocks:
            return blocks
    text_raw = payload.get("text")
    if isinstance(text_raw, str) and text_raw.strip():
        return [paragraph.strip() for paragraph in text_raw.split("\n\n") if paragraph.strip()]
    return []


def normalize_vision_tables(tables_raw: Any) -> list[dict[str, Any]]:
    if not isinstance(tables_raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for table_index, table in enumerate(tables_raw):
        if not isinstance(table, dict):
            continue
        rows_input = table.get("rows")
        if not isinstance(rows_input, list):
            continue
        rows: list[list[str]] = []
        for row in rows_input:
            if not isinstance(row, list):
                continue
            rows.append(["" if cell is None else str(cell).strip() for cell in row])
        rows = [row for row in rows if any(cell for cell in row)]
        if not rows:
            continue
        headers_input = table.get("headers")
        if isinstance(headers_input, list) and headers_input:
            headers = [str(item).strip() for item in headers_input]
            rows = [headers] + rows
        caption = table.get("caption")
        normalized.append(
            {
                "table_index": table_index,
                "rows": rows,
                "caption": str(caption).strip() if isinstance(caption, str) and caption.strip() else None,
            }
        )
    return normalized


def parse_pdf_vision_response(response: str, page_numbers: list[int]) -> dict[int, dict[str, Any]]:
    payload = try_load_vision_payload(response) or salvage_pages_payload(response)
    if payload is not None:
        pages = payload.get("pages")
        if isinstance(pages, list):
            parsed: dict[int, dict[str, Any]] = {}
            for index, page in enumerate(pages):
                if not isinstance(page, dict):
                    continue
                if "page_number" in page:
                    page_num = int(page["page_number"])
                elif index < len(page_numbers):
                    page_num = page_numbers[index]
                elif page_numbers:
                    page_num = page_numbers[0] + index
                else:
                    page_num = index + 1
                text_blocks = normalize_text_blocks(page)
                tables = normalize_vision_tables(page.get("tables"))
                joined_text = "\n\n".join(text_blocks).strip()
                parsed[page_num] = {
                    "text": joined_text or str(page.get("text", "")).strip(),
                    "text_blocks": text_blocks,
                    "tables": tables,
                }
            if parsed:
                return parsed

        text_blocks = normalize_text_blocks(payload)
        tables = normalize_vision_tables(payload.get("tables"))
        joined_text = "\n\n".join(text_blocks).strip() or str(payload.get("text", "")).strip()
        if joined_text or text_blocks or tables:
            page_number = page_numbers[0]
            if isinstance(payload.get("page_number"), int):
                page_number = int(payload["page_number"])
            return {
                page_number: {
                    "text": joined_text,
                    "text_blocks": text_blocks,
                    "tables": tables,
                }
            }

    stripped = strip_code_fence(response).strip()
    if len(stripped) > 40 and not stripped.startswith("{"):
        paragraphs = [paragraph.strip() for paragraph in stripped.split("\n\n") if paragraph.strip()]
        if not paragraphs:
            paragraphs = [line.strip() for line in stripped.splitlines() if line.strip()]
        if paragraphs:
            joined = "\n\n".join(paragraphs)
            return {
                page_numbers[0]: {
                    "text": joined,
                    "text_blocks": paragraphs,
                    "tables": [],
                }
            }

    return {
        page_numbers[0]: {
            "text": "",
            "text_blocks": [],
            "tables": [],
            "error": "Не удалось разобрать ответ OCR",
        }
    }


def parse_image_vision_response(
    response: str,
) -> tuple[str, str | None, list[str], list[dict[str, Any]]]:
    payload = try_load_vision_payload(response) or salvage_pages_payload(response)
    if payload is not None:
        if isinstance(payload.get("pages"), list) and payload["pages"]:
            first_page = payload["pages"][0]
            if isinstance(first_page, dict):
                payload = first_page
        text_blocks = normalize_text_blocks(payload)
        tables = normalize_vision_tables(payload.get("tables"))
        quality_notes = payload.get("quality_notes")
        joined_text = "\n\n".join(text_blocks).strip()
        if not joined_text:
            joined_text = str(payload.get("text", "")).strip()
        if joined_text or text_blocks or tables:
            return (
                joined_text,
                str(quality_notes) if isinstance(quality_notes, str) and quality_notes.strip() else None,
                text_blocks,
                tables,
            )
    return "", "Не удалось разобрать ответ OCR", [], []
