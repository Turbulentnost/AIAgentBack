"""Определение формата по расширению и magic bytes."""

from __future__ import annotations

from pathlib import Path

DRAWING_EXTENSIONS = frozenset({".pdf", ".dxf", ".dwg", ".cdw"})
TEXT_EXTENSIONS = frozenset({".docx", ".xlsx", ".xml", ".spw", ".txt"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
SUPPORTED_EXTENSIONS = DRAWING_EXTENSIONS | TEXT_EXTENSIONS | IMAGE_EXTENSIONS | frozenset({".zip"})

_PDF = b"%PDF"
_DXF_MARKERS = (b"  0\nSECTION", b"  0\r\nSECTION", b"999\nDXF", b"999\r\nDXF")
_DWG_MAGIC = (b"AC10", b"AC1.", b"AC2.")
_ZIP = b"PK\x03\x04"


def suffix_of(name: str) -> str:
    return Path(name.replace("\\", "/")).suffix.lower()


def detect_format(name: str, data: bytes) -> str:
    ext = suffix_of(name)
    head = data[:512]

    if data.startswith(_PDF) or ext == ".pdf":
        return "pdf"
    if any(m in head for m in _DXF_MARKERS) or ext == ".dxf":
        return "dxf"
    if any(head.startswith(m) for m in _DWG_MAGIC) or ext == ".dwg":
        return "dwg"
    if ext == ".cdw":
        return "cdw"
    if ext == ".spw":
        return "spw"
    if ext == ".docx":
        return "docx"
    if ext == ".xlsx":
        return "xlsx"
    if data.startswith(_ZIP) and ext in {".docx", ".xlsx"}:
        return "docx" if ext == ".docx" else "xlsx"
    if ext in {".xml", ".spw"}:
        return "xml" if ext == ".xml" else "spw"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if data.startswith(_ZIP) or ext == ".zip":
        return "zip"
    if ext == ".txt":
        return "text"
    return ext.lstrip(".") or "unknown"


def is_drawing_format(fmt: str) -> bool:
    return fmt in {"pdf", "dxf", "dwg", "cdw", "image"}
