from __future__ import annotations

import re
import zipfile
from io import BytesIO

from app.schemas.turbo_smk import NdVisioImportResult


class NdVisioService:
    """Импорт/экспорт блок-схем Visio (VSDX) — базовая поддержка для ТЗ п. 5.8."""

    def import_vsdx(self, *, filename: str, content: bytes) -> NdVisioImportResult:
        warnings: list[str] = []
        node_count = 0
        mermaid_preview = "flowchart TD\n  A[Импорт Visio]"
        if not filename.lower().endswith((".vsdx", ".vsd")):
            return NdVisioImportResult(
                filename=filename,
                imported=False,
                diagram_format="unknown",
                node_count=0,
                warnings=["Поддерживаются файлы .vsdx и .vsd"],
            )
        if filename.lower().endswith(".vsdx"):
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    page_names = [name for name in archive.namelist() if name.startswith("visio/pages/page") and name.endswith(".xml")]
                    node_count = len(page_names)
                    if page_names:
                        page_xml = archive.read(page_names[0]).decode("utf-8", errors="ignore")
                        titles = re.findall(r'NameU="([^"]+)"', page_xml)
                        if titles:
                            lines = ["flowchart TD"]
                            for index, title in enumerate(titles[:12], start=1):
                                safe = re.sub(r"[^0-9A-Za-zА-Яа-я _-]", "", title) or f"Step{index}"
                                lines.append(f"  N{index}[{safe}]")
                            mermaid_preview = "\n".join(lines)
            except zipfile.BadZipFile:
                warnings.append("Файл VSDX повреждён или не является ZIP-архивом")
                return NdVisioImportResult(
                    filename=filename,
                    imported=False,
                    diagram_format="vsdx",
                    node_count=0,
                    warnings=warnings,
                )
        else:
            warnings.append("Формат .vsd поддерживается только как вложение; для редактирования используйте .vsdx")
        return NdVisioImportResult(
            filename=filename,
            imported=True,
            diagram_format="vsdx" if filename.lower().endswith(".vsdx") else "vsd",
            node_count=node_count,
            warnings=warnings,
            mermaid_preview=mermaid_preview,
        )

    def export_vsdx_placeholder(self, *, mermaid_source: str, filename: str = "process.vsdx") -> bytes:
        _ = mermaid_source
        return b""
