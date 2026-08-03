"""Оркестратор постобработки загруженных файлов."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from app.format_processing.detect import detect_format, suffix_of
from app.format_processing.handlers.docx import extract_docx_text
from app.format_processing.handlers.dwg import process_dwg
from app.format_processing.handlers.dxf import process_dxf
from app.format_processing.handlers.kompas import process_cdw, process_spw
from app.format_processing.handlers.pdf import process_pdf
from app.format_processing.handlers.xml_text import extract_xml_text
from app.format_processing.handlers.xlsx import extract_xlsx_text
from app.format_processing.types import PreprocessResult, ProcessedArtifact

_log = logging.getLogger("eskd.preprocess")
_SKIP = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


def process_bytes(name: str, data: bytes) -> PreprocessResult:
    """Конвертирует один файл в текст и/или PNG."""
    fmt = detect_format(name, data)
    source = name.replace("\\", "/")
    result = PreprocessResult(source=source)

    try:
        if fmt == "zip":
            result.artifacts = _process_zip(data, source=source)
            return result

        result.artifacts = _dispatch(fmt, data, source=source)
    except Exception as exc:
        _log.warning("preprocess %s (%s): %s", source, fmt, exc)
        result.warnings.append(f"{source}: {exc}")
        raise

    return result


def process_uploads(
    files: list[tuple[str, bytes]],
) -> tuple[list[tuple[str, bytes, str]], list[dict], list[str]]:
    """
    Нормализует пакет загрузок.

    Returns:
        model_files — для vision-модели (PNG/PDF)
        extracted_texts — метаданные извлечённого текста
        warnings
    """
    model_files: list[tuple[str, bytes, str]] = []
    extracted: list[dict] = []
    warnings: list[str] = []

    for name, data in files:
        try:
            prep = process_bytes(name, data)
        except Exception as exc:
            warnings.append(f"{name}: {exc}")
            # PNG/JPG/PDF напрямую — fallback без конвертации
            ext = suffix_of(name)
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                model_files.append((name, data, f"image/{ext.lstrip('.')}"))
            elif ext == ".pdf":
                model_files.append((name, data, "application/pdf"))
            continue

        warnings.extend(prep.warnings)
        for art in prep.artifacts:
            if art.kind == "text":
                extracted.append(
                    {
                        "source": art.source,
                        "name": art.name,
                        "format": art.format,
                        "chars": len(art.text),
                        "text": art.text[:8000],
                    }
                )
        model_files.extend(prep.model_files())

    # dedupe by filename
    seen: set[str] = set()
    unique: list[tuple[str, bytes, str]] = []
    for item in model_files:
        if item[0] in seen:
            continue
        seen.add(item[0])
        unique.append(item)

    if not unique and not extracted:
        raise ValueError("Не удалось подготовить файлы для проверки")

    return unique, extracted, warnings


def _process_zip(data: bytes, *, source: str) -> list[ProcessedArtifact]:
    arts: list[ProcessedArtifact] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist()):
            norm = name.replace("\\", "/")
            if norm.endswith("/") or "__MACOSX" in norm:
                continue
            base = Path(norm).name
            if base.lower() in _SKIP or base.startswith("."):
                continue
            try:
                prep = process_bytes(norm, zf.read(name))
                arts.extend(prep.artifacts)
            except Exception as exc:
                _log.debug("zip entry skip %s: %s", norm, exc)
    return arts


def _dispatch(fmt: str, data: bytes, *, source: str) -> list[ProcessedArtifact]:
    stem = Path(source).stem

    if fmt == "pdf":
        return process_pdf(data, source=source)
    if fmt == "dxf":
        return process_dxf(data, source=source)
    if fmt == "dwg":
        return process_dwg(data, source=source)
    if fmt == "cdw":
        return process_cdw(data, source=source)
    if fmt == "spw":
        return process_spw(data, source=source)
    if fmt == "docx":
        text = extract_docx_text(data)
        return [
            ProcessedArtifact(
                source=source,
                name=f"{stem}.extracted.txt",
                kind="text",
                data=text.encode("utf-8"),
                mime="text/plain; charset=utf-8",
                format="docx",
            )
        ]
    if fmt == "xlsx":
        text = extract_xlsx_text(data)
        return [
            ProcessedArtifact(
                source=source,
                name=f"{stem}.extracted.txt",
                kind="text",
                data=text.encode("utf-8"),
                mime="text/plain; charset=utf-8",
                format="xlsx",
            )
        ]
    if fmt in {"xml", "text"}:
        text = extract_xml_text(data, filename=source)
        return [
            ProcessedArtifact(
                source=source,
                name=f"{stem}.extracted.txt",
                kind="text",
                data=text.encode("utf-8"),
                mime="text/plain; charset=utf-8",
                format=fmt,
            )
        ]
    if fmt == "image":
        ext = suffix_of(source)
        mime = "image/png" if ext == ".png" else f"image/{ext.lstrip('.')}"
        return [
            ProcessedArtifact(
                source=source,
                name=Path(source).name,
                kind="image",
                data=data,
                mime=mime,
                format="image",
            )
        ]

    raise ValueError(f"Неподдерживаемый формат: {fmt}")
