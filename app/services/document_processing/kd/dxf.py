"""Парсинг DXF/DWG конструкторской документации через ezdxf."""

from __future__ import annotations

import base64
import io
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.entities import Attrib, Insert, MText, Text

from app.eskd.designation import ESKD_STANDARD_DESIGNATION_RE, normalize_designation, parse_designation
from app.schemas.kd_parse import KDPageResult, KDRegionBounds, KDRegionText, KDParseResult
from app.services.document_processing.kd.formats import detect_kd_format
from app.services.document_processing.kd.pdf import _build_eskd_document_text
from app.services.document_processing.kd.regions import TITLE_BLOCK_HEIGHT_RATIO, TITLE_BLOCK_WIDTH_RATIO

_UFG_DESIGNATION_RE = re.compile(
    r"UFG[\-\s]*800[\-\s]*[\d\.]+(?:\s*[СC][БB])?",
    re.IGNORECASE,
)
_TITLE_BLOCK_ATTR_TAGS = frozenset(
    {
        "DESIGNATION",
        "DESIGNATION1",
        "ОБОЗНАЧЕНИЕ",
        "NUMBER",
        "DRAWING_NO",
        "TITLE",
        "NAME",
        "SCALE",
        "МАСШТАБ",
    }
)


class KDDxfParsingError(RuntimeError):
    pass


class KDDxfNotImplementedError(NotImplementedError):
    pass


@dataclass(frozen=True)
class _DxfTextItem:
    text: str
    x: float
    y: float
    source: str
    tag: str | None = None


def parse_kd_dxf_bytes(
    data: bytes,
    *,
    filename: str | None = None,
    render_scheme: bool = True,
    scheme_zoom: float = 1.0,
) -> KDParseResult:
    """Разбирает DXF КД: текст штампа, PNG схемы, сводный текст для ЕСКД."""
    started = time.perf_counter()
    source_format = detect_kd_format(data, filename=filename, content_type="application/dxf")
    if source_format != "dxf":
        raise KDDxfParsingError(f"Ожидался DXF, получен {source_format}")

    try:
        doc = _load_dxf_document(data)
    except Exception as exc:
        raise KDDxfParsingError(f"Не удалось прочитать DXF: {exc}") from exc

    text_items = _collect_text_entities(doc)
    extents = _layout_extents(doc, text_items)
    title_items, drawing_items = _split_title_and_drawing(text_items, extents)
    title_text = _join_text_items(title_items)
    drawing_text = _join_text_items(drawing_items)
    detected_designation = _detect_designation(title_items, drawing_items, filename=filename)

    width = max(extents[2] - extents[0], 1.0)
    height = max(extents[3] - extents[1], 1.0)
    title_bounds = _title_block_bounds(extents)
    drawing_bounds = _drawing_bounds(extents)

    scheme_png: bytes | None = None
    if render_scheme:
        scheme_png = _render_dxf_png(doc, dpi=max(72, int(72 * scheme_zoom)))

    eskd_page_text = title_text or drawing_text
    if detected_designation and detected_designation.upper() not in eskd_page_text.upper():
        eskd_page_text = f"Обозначение {detected_designation}\n{eskd_page_text}".strip()

    page = KDPageResult(
        page_number=1,
        width=width,
        height=height,
        is_scan=False,
        requires_ocr=False,
        title_block=KDRegionText(
            region="title_block",
            text=title_text,
            method="dxf",
            char_count=len(title_text),
            bbox=title_bounds,
        ),
        drawing=KDRegionText(
            region="drawing",
            text=drawing_text,
            method="dxf" if drawing_text else "none",
            char_count=len(drawing_text),
            bbox=drawing_bounds,
        ),
        scheme_png_base64=base64.b64encode(scheme_png).decode("ascii") if scheme_png else None,
        eskd_text=eskd_page_text,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    metadata: dict[str, Any] = {
        "text_entity_count": len(text_items),
        "parsed_page_numbers": [1],
        "scheme_zoom": scheme_zoom,
    }
    if detected_designation:
        metadata["detected_designation"] = detected_designation
        parsed = parse_designation(detected_designation)
        if parsed:
            metadata["eskd"] = {
                "designation": parsed["full"],
                "designation_org": parsed["org"],
                "designation_serial": parsed["serial"],
                "designation_sheet": parsed["sheet"],
                "designation_suffix": parsed["suffix"],
            }

    return KDParseResult(
        source_format="dxf",
        source_filename=filename,
        pages_count=1,
        pages=[page],
        eskd_document_text=_build_eskd_document_text([page]),
        requires_ocr=False,
        ocr_used=False,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def parse_kd_dxf_path(
    path: Path | str,
    *,
    render_scheme: bool = True,
    scheme_zoom: float = 1.0,
) -> KDParseResult:
    file_path = Path(path)
    return parse_kd_dxf_bytes(
        file_path.read_bytes(),
        filename=file_path.name,
        render_scheme=render_scheme,
        scheme_zoom=scheme_zoom,
    )


def parse_kd_dwg_bytes(
    data: bytes,
    *,
    filename: str | None = None,
) -> KDParseResult:
    """DWG не читается напрямую — нужна конвертация ODA File Converter → DXF."""
    raise KDDxfNotImplementedError(
        "Парсинг DWG не реализован без ODA File Converter. "
        "Конвертируйте DWG→DXF (ODAFileConverter / TeighaFileConverter) "
        "и вызовите parse_kd_dxf_bytes или KDParser с файлом .dxf."
    )


def _load_dxf_document(data: bytes):
    stripped = data.lstrip()
    if stripped.startswith(b"  0") or b"SECTION" in data[:512]:
        text = data.decode("utf-8", errors="replace")
        return ezdxf.read(io.StringIO(text))
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return ezdxf.readfile(tmp_path)
    finally:
        os.unlink(tmp_path)


def _collect_text_entities(doc: ezdxf.document.Drawing) -> list[_DxfTextItem]:
    items: list[_DxfTextItem] = []
    msp = doc.modelspace()

    def add_text(text: str, x: float, y: float, source: str, tag: str | None = None) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        items.append(_DxfTextItem(text=cleaned, x=x, y=y, source=source, tag=tag))

    for entity in msp:
        _extract_entity_text(entity, add_text)

    for layout in doc.layouts:
        if layout.name.lower() == "model":
            continue
        for entity in layout:
            _extract_entity_text(entity, add_text, source_prefix=f"layout:{layout.name}")

    return items


def _extract_entity_text(entity, add_text, source_prefix: str = "modelspace") -> None:
    if isinstance(entity, Text):
        insert = entity.dxf.insert
        add_text(entity.dxf.text, insert.x, insert.y, f"{source_prefix}:TEXT")
        return
    if isinstance(entity, MText):
        insert = entity.dxf.insert
        add_text(entity.plain_text(), insert.x, insert.y, f"{source_prefix}:MTEXT")
        return
    if isinstance(entity, Attrib):
        insert = entity.dxf.insert
        tag = (entity.dxf.tag or "").strip().upper()
        add_text(entity.dxf.text, insert.x, insert.y, f"{source_prefix}:ATTRIB", tag=tag)
        return
    if isinstance(entity, Insert):
        for attrib in entity.attribs:
            insert = attrib.dxf.insert
            tag = (attrib.dxf.tag or "").strip().upper()
            add_text(attrib.dxf.text, insert.x, insert.y, f"{source_prefix}:INSERT.ATTRIB", tag=tag)


def _layout_extents(
    doc: ezdxf.document.Drawing,
    text_items: list[_DxfTextItem],
) -> tuple[float, float, float, float]:
    try:
        from ezdxf import bbox

        extents = bbox.extents(doc.modelspace(), fast=True)
        if extents.has_data:
            return (extents.extmin.x, extents.extmin.y, extents.extmax.x, extents.extmax.y)
    except Exception:
        pass

    if text_items:
        xs = [item.x for item in text_items]
        ys = [item.y for item in text_items]
        return (min(xs), min(ys), max(xs), max(ys))
    return (0.0, 0.0, 420.0, 297.0)


def _split_title_and_drawing(
    text_items: list[_DxfTextItem],
    extents: tuple[float, float, float, float],
) -> tuple[list[_DxfTextItem], list[_DxfTextItem]]:
    x0, y0, x1, y1 = extents
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    tb_x0 = x0 + width * (1 - TITLE_BLOCK_WIDTH_RATIO)
    tb_y0 = y0 + height * (1 - TITLE_BLOCK_HEIGHT_RATIO)

    title: list[_DxfTextItem] = []
    drawing: list[_DxfTextItem] = []
    for item in text_items:
        in_title = item.x >= tb_x0 and item.y >= tb_y0
        if in_title or (item.tag and item.tag in _TITLE_BLOCK_ATTR_TAGS):
            title.append(item)
        else:
            drawing.append(item)
    return title, drawing


def _title_block_bounds(extents: tuple[float, float, float, float]) -> KDRegionBounds:
    x0, y0, x1, y1 = extents
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    return KDRegionBounds(
        x0=x0 + width * (1 - TITLE_BLOCK_WIDTH_RATIO),
        y0=y0 + height * (1 - TITLE_BLOCK_HEIGHT_RATIO),
        x1=x1,
        y1=y1,
    )


def _drawing_bounds(extents: tuple[float, float, float, float]) -> KDRegionBounds:
    x0, y0, x1, y1 = extents
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    tb_x0 = x0 + width * (1 - TITLE_BLOCK_WIDTH_RATIO)
    tb_y0 = y0 + height * (1 - TITLE_BLOCK_HEIGHT_RATIO)
    return KDRegionBounds(x0=x0, y0=y0, x1=tb_x0, y1=tb_y0)


def _join_text_items(items: list[_DxfTextItem]) -> str:
    if not items:
        return ""
    ordered = sorted(items, key=lambda item: (-item.y, item.x))
    lines: list[str] = []
    seen: set[str] = set()
    for item in ordered:
        line = item.text.strip()
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines)


def _detect_designation(
    title_items: list[_DxfTextItem],
    drawing_items: list[_DxfTextItem],
    *,
    filename: str | None,
) -> str | None:
    tagged = [
        item.text.strip()
        for item in title_items
        if item.tag and item.tag in _TITLE_BLOCK_ATTR_TAGS and item.text.strip()
    ]
    for text in tagged:
        found = _designation_from_text(text, prefer_gost=True)
        if found:
            return found

    for item in title_items:
        found = _designation_from_text(item.text, prefer_gost=True)
        if found:
            return found

    for item in drawing_items:
        found = _designation_from_text(item.text, prefer_gost=False)
        if found:
            return found

    if filename:
        stem = Path(filename).stem
        found = _designation_from_text(stem, prefer_gost=True)
        if found:
            return found
    return None


def _designation_from_text(text: str, *, prefer_gost: bool) -> str | None:
    if prefer_gost:
        for part in re.split(r"[\s,;:]+", text):
            normalized = normalize_designation(part.strip('."«»'))
            if normalized and ESKD_STANDARD_DESIGNATION_RE.match(normalized):
                return normalized

    ufg_match = _UFG_DESIGNATION_RE.search(text)
    if ufg_match:
        raw = ufg_match.group(0).upper().replace(" ", "")
        raw = re.sub(r"UFG[\-\s]*800[\-\s]*", "UFG-800-", raw, flags=re.IGNORECASE)
        raw = raw.replace("CB", "СБ")
        if not raw.endswith("СБ") and re.search(r"\d$", raw):
            raw = f"{raw} СБ"
        return raw.strip()

    if not prefer_gost:
        for part in re.split(r"[\s,;:]+", text):
            normalized = normalize_designation(part.strip('."«»'))
            if normalized and ESKD_STANDARD_DESIGNATION_RE.match(normalized):
                return normalized
    return None


def _render_dxf_png(doc: ezdxf.document.Drawing, *, dpi: int = 150) -> bytes | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    except ImportError:
        return None

    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    Frontend(ctx, backend).draw_layout(doc.modelspace(), finalize=True)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    return buffer.getvalue()
