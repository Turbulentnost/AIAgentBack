"""КОМПАС CDW / SPW — текст и PNG через внешний экспорт или fallback."""

from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
from pathlib import Path

from app.format_processing.handlers.dxf import process_dxf
from app.format_processing.handlers.pdf import process_pdf
from app.format_processing.handlers.xml_text import extract_xml_text
from app.format_processing.types import ProcessedArtifact

# Читаемые строки из бинарника (обозначения, штамп)
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e\xc0-\xff]{4,}")


def _strings_from_binary(data: bytes) -> str:
    parts: list[str] = []
    for m in _PRINTABLE_RUN.finditer(data):
        raw = m.group(0)
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                s = raw.decode(enc).strip()
                if len(s) >= 4 and not s.startswith("PK"):
                    parts.append(s)
                break
            except UnicodeDecodeError:
                continue
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "\n".join(out[:500])


def _run_export_cmd(data: bytes, *, source: str, fmt: str) -> list[ProcessedArtifact]:
    cmd_template = (os.environ.get("KOMPAS_EXPORT_CMD") or "").strip()
    if not cmd_template:
        return []

    with tempfile.TemporaryDirectory(prefix="eskd_kompas_") as tmp:
        src_path = Path(tmp) / Path(source.replace("\\", "/")).name
        src_path.write_bytes(data)
        out_png = Path(tmp) / "out.png"
        out_pdf = Path(tmp) / "out.pdf"
        out_dxf = Path(tmp) / "out.dxf"

        cmd = cmd_template.format(
            input=str(src_path),
            png=str(out_png),
            pdf=str(out_pdf),
            dxf=str(out_dxf),
            format=fmt,
        )
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0:
            raise ValueError(f"KOMPAS export failed: {proc.stderr[-400:]}")

        if out_png.is_file():
            return [
                ProcessedArtifact(
                    source=source,
                    name=f"{src_path.stem}.png",
                    kind="image",
                    data=out_png.read_bytes(),
                    mime="image/png",
                    format=fmt,
                    meta={"via": "kompas_export"},
                )
            ]
        if out_pdf.is_file():
            return process_pdf(out_pdf.read_bytes(), source=source)
        if out_dxf.is_file():
            return process_dxf(out_dxf.read_bytes(), source=source)
    return []


def process_spw(data: bytes, *, source: str) -> list[ProcessedArtifact]:
    stem = Path(source.replace("\\", "/")).stem
    text = extract_xml_text(data, filename=source)
    if not text:
        text = _strings_from_binary(data)
    if not text:
        raise ValueError("SPW: не удалось извлечь текст")
    return [
        ProcessedArtifact(
            source=source,
            name=f"{stem}.extracted.txt",
            kind="text",
            data=text.encode("utf-8"),
            mime="text/plain; charset=utf-8",
            format="spw",
        )
    ]


def process_cdw(data: bytes, *, source: str) -> list[ProcessedArtifact]:
    stem = Path(source.replace("\\", "/")).stem
    warnings: list[str] = []
    artifacts: list[ProcessedArtifact] = []

    try:
        exported = _run_export_cmd(data, source=source, fmt="cdw")
        if exported:
            return exported
    except ValueError as exc:
        warnings.append(str(exc))

    # ZIP/XML внутри (некоторые версии КОМПАС)
    if data.startswith(b"PK\x03\x04"):
        text = extract_xml_text(data, filename=source)
        if text:
            artifacts.append(
                ProcessedArtifact(
                    source=source,
                    name=f"{stem}.extracted.txt",
                    kind="text",
                    data=text.encode("utf-8"),
                    mime="text/plain; charset=utf-8",
                    format="cdw",
                    meta={"method": "zip_xml"},
                )
            )

    strings = _strings_from_binary(data)
    if strings:
        artifacts.append(
            ProcessedArtifact(
                source=source,
                name=f"{stem}.strings.txt",
                kind="text",
                data=strings.encode("utf-8"),
                mime="text/plain; charset=utf-8",
                format="cdw",
                meta={"method": "strings"},
            )
        )

    if not artifacts:
        raise ValueError(
            "CDW: для PNG нужен KOMPAS_EXPORT_CMD или ODA; извлечён только текст через strings"
        )

    if not any(a.kind == "image" for a in artifacts):
        warnings.append(
            "CDW: PNG не получен — задайте KOMPAS_EXPORT_CMD для экспорта чертежа в PNG/PDF/DXF"
        )
    return artifacts
