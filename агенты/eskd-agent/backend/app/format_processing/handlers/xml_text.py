"""Извлечение текста из XML / SPW."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _xml_to_lines(elem: ET.Element, depth: int = 0) -> list[str]:
    lines: list[str] = []
    text = (elem.text or "").strip()
    tail = (elem.tail or "").strip()
    tag = _strip_ns(elem.tag)
    if text:
        lines.append(f"{tag}: {text}")
    for child in list(elem):
        lines.extend(_xml_to_lines(child, depth + 1))
    if tail and not list(elem):
        lines.append(tail)
    return lines


def extract_xml_text(data: bytes, *, filename: str = "") -> str:
    # Office Open XML внутри docx/xlsx — не сюда
    for encoding in ("utf-8", "utf-16", "cp1251", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    stripped = text.lstrip()
    if stripped.startswith("<") or stripped.startswith("<?xml"):
        try:
            root = ET.fromstring(data)
            lines = _xml_to_lines(root)
            return "\n".join(lines).strip() or text.strip()
        except ET.ParseError:
            pass

    if data.startswith(b"PK\x03\x04"):
        return _extract_xml_from_zip(data)

    # SPW / прочие текстовые КОМПАС-файлы
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def _extract_xml_from_zip(data: bytes) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist()):
            if not name.lower().endswith((".xml", ".txt", ".spw")):
                continue
            try:
                raw = zf.read(name)
                parts.append(f"--- {name} ---")
                parts.append(extract_xml_text(raw, filename=name))
            except Exception:
                continue
    return "\n".join(p for p in parts if p.strip()).strip()
