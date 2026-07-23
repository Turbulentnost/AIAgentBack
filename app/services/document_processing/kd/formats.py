"""Определение формата входного файла КД."""

from __future__ import annotations

from pathlib import Path

from app.schemas.kd_parse import KDSourceFormat


class KDFormatError(ValueError):
    pass


_PDF_MAGIC = b"%PDF"
_DXF_MARKERS = (b"  0\nSECTION", b"  0\r\nSECTION", b"999\nDXF", b"999\r\nDXF")
_DWG_MAGIC = (b"AC10", b"AC1.", b"AC2.")


def detect_kd_format(
    data: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> KDSourceFormat:
    """Определяет формат КД по magic bytes, content-type и расширению файла."""
    if content_type:
        lowered = content_type.lower()
        if "pdf" in lowered:
            return "pdf"
        if "dxf" in lowered or "dwg" in lowered:
            return "dxf" if "dxf" in lowered else "dwg"

    if data.startswith(_PDF_MAGIC):
        return "pdf"

    head = data[:512]
    if any(marker in head for marker in _DXF_MARKERS):
        return "dxf"

    if any(head.startswith(magic) for magic in _DWG_MAGIC):
        return "dwg"

    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf":
            return "pdf"
        if suffix == ".dxf":
            return "dxf"
        if suffix == ".dwg":
            return "dwg"

    raise KDFormatError("Не удалось определить формат КД (ожидается PDF, DXF или DWG)")
