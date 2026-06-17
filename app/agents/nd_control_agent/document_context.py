from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DocumentContextChunk(BaseModel):
    chunk_id: str
    text: str
    page_number: int | None = None
    section: str | None = None
    chunk_index: int | None = None


class DocumentContext(BaseModel):
    mode: Literal["full_text", "chunked"]
    full_text: str | None = None
    chunks: list[DocumentContextChunk] = Field(default_factory=list)
    total_chars: int = 0
    total_chunks: int = 0
    warnings: list[str] = Field(default_factory=list)
