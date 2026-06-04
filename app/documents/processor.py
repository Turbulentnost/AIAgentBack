from __future__ import annotations
import io

def extract_text(data: bytes, mime_type: str) -> str:
    if "pdf" in mime_type:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    if "word" in mime_type or "docx" in mime_type:
        from docx import Document
        document = Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)
    if "sheet" in mime_type or "xlsx" in mime_type or "excel" in mime_type:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return "\n".join("\t".join("" if c is None else str(c) for c in row) for ws in wb.worksheets for row in ws.iter_rows(values_only=True))
    return data.decode("utf-8", errors="ignore")

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks
