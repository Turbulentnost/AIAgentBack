"""DWG → DXF (ODA) → PNG + текст."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from app.format_processing.handlers.dxf import process_dxf
from app.format_processing.types import ProcessedArtifact


def _convert_dwg_to_dxf(data: bytes, *, source: str) -> bytes:
    oda = (os.environ.get("ODA_CONVERTER_PATH") or "").strip()
    if not oda or not Path(oda).is_file():
        raise ValueError(
            "DWG: установите ODA File Converter и задайте ODA_CONVERTER_PATH "
            "(конвертация DWG→DXF→PNG)"
        )

    with tempfile.TemporaryDirectory(prefix="eskd_dwg_") as tmp:
        inp = Path(tmp) / "input"
        out = Path(tmp) / "output"
        inp.mkdir()
        out.mkdir()
        src = inp / Path(source.replace("\\", "/")).name
        src.write_bytes(data)

        cmd = [
            oda,
            str(inp),
            str(out),
            "ACAD2018",
            "DXF",
            "0",
            "1",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if proc.returncode != 0:
            raise ValueError(f"ODA converter failed: {proc.stderr[-500:]}")

        dxf_files = list(out.rglob("*.dxf"))
        if not dxf_files:
            raise ValueError("DWG: ODA не создал DXF")
        return dxf_files[0].read_bytes()


def process_dwg(data: bytes, *, source: str) -> list[ProcessedArtifact]:
    dxf_bytes = _convert_dwg_to_dxf(data, source=source)
    arts = process_dxf(dxf_bytes, source=source.rsplit(".", 1)[0] + ".dxf")
    return [
        ProcessedArtifact(
            source=a.source,
            name=a.name,
            kind=a.kind,
            data=a.data,
            mime=a.mime,
            format="dwg",
            meta={**a.meta, "converted_via": "oda"},
        )
        for a in arts
    ]
