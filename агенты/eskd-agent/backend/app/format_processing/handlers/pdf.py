"""PDF: страницы → PNG, плюс извлечённый текст."""

from __future__ import annotations

import io

import fitz

from app.format_processing.types import ProcessedArtifact


def process_pdf(data: bytes, *, source: str, scale: float = 2.0) -> list[ProcessedArtifact]:
    doc = fitz.open(stream=data, filetype="pdf")
    artifacts: list[ProcessedArtifact] = []
    text_parts: list[str] = []
    stem = source.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    try:
        for idx in range(doc.page_count):
            page = doc[idx]
            page_no = idx + 1
            page_text = (page.get_text("text") or "").strip()
            if page_text:
                text_parts.append(f"=== {stem} · стр. {page_no} ===\n{page_text}")

            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
            png = pix.tobytes("png")
            artifacts.append(
                ProcessedArtifact(
                    source=source,
                    name=f"{stem}_p{page_no:02d}.png",
                    kind="image",
                    data=png,
                    mime="image/png",
                    format="pdf",
                    meta={"page": page_no, "pages_total": doc.page_count},
                )
            )
    finally:
        doc.close()

    if text_parts:
        combined = "\n\n".join(text_parts)
        artifacts.insert(
            0,
            ProcessedArtifact(
                source=source,
                name=f"{stem}.extracted.txt",
                kind="text",
                data=combined.encode("utf-8"),
                mime="text/plain; charset=utf-8",
                format="pdf",
                meta={"pages": len(text_parts)},
            ),
        )
    return artifacts
