from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentChunk
from app.models.enums import NdChangeLocationType
from app.services.document_editing.schemas import LocatedChange

SECTION_RE = re.compile(r"(?:раздел|пункт|п\.|подпункт)\s+([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE)


class ChangeLocator:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def locate(
        self,
        *,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID | None,
        change_text: str,
    ) -> list[LocatedChange]:
        chunks = await self._load_chunks(document_id, document_version_id)
        if not chunks:
            return []

        section_numbers = self.extract_section_numbers(change_text)
        matches: list[LocatedChange] = []
        for section_number in section_numbers:
            for chunk in chunks:
                metadata = chunk.metadata_ or chunk.chunk_metadata or {}
                chunk_section = str(metadata.get("section_number") or metadata.get("clause_number") or "")
                text = chunk.text or chunk.content or ""
                if chunk_section == section_number or re.search(rf"\b{re.escape(section_number)}\b", text):
                    matches.append(self._from_chunk(chunk, confidence=0.86, section_number=section_number))

        if matches:
            return self._dedupe(matches)[:5]

        keywords = self._keywords(change_text)
        scored: list[tuple[int, DocumentChunk]] = []
        for chunk in chunks:
            text = (chunk.text or chunk.content or "").lower()
            score = sum(1 for keyword in keywords if keyword in text)
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._from_chunk(chunk, confidence=min(0.55 + score * 0.08, 0.78)) for score, chunk in scored[:5]]

    def extract_section_numbers(self, text: str) -> list[str]:
        return list(dict.fromkeys(match.group(1) for match in SECTION_RE.finditer(text or "")))

    async def _load_chunks(self, document_id: uuid.UUID, document_version_id: uuid.UUID | None) -> list[DocumentChunk]:
        stmt = select(DocumentChunk).where(DocumentChunk.document_id == document_id).order_by(DocumentChunk.chunk_index.asc())
        if document_version_id:
            stmt = stmt.where(DocumentChunk.document_version_id == document_version_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    def _from_chunk(
        self,
        chunk: DocumentChunk,
        *,
        confidence: float,
        section_number: str | None = None,
    ) -> LocatedChange:
        metadata = chunk.metadata_ or chunk.chunk_metadata or {}
        return LocatedChange(
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            section_number=section_number or metadata.get("section_number") or metadata.get("clause_number"),
            section_title=chunk.section_title or metadata.get("section_title"),
            page_number=chunk.page_number,
            chunk_id=chunk.id,
            location_type=NdChangeLocationType.PARAGRAPH if section_number else NdChangeLocationType.BLOCK_TEXT,
            current_text=chunk.text or chunk.content or "",
            confidence=confidence,
            status="found" if confidence >= 0.8 else "candidate",
        )

    def _keywords(self, text: str) -> list[str]:
        words = re.findall(r"[а-яА-Яa-zA-Z0-9]{5,}", text.lower())
        stop_words = {"изложить", "следующей", "редакции", "добавить", "заменить", "исключить", "раздел", "пункт"}
        return [word for word in dict.fromkeys(words) if word not in stop_words][:12]

    def _dedupe(self, items: list[LocatedChange]) -> list[LocatedChange]:
        seen: set[uuid.UUID | None] = set()
        result: list[LocatedChange] = []
        for item in items:
            if item.chunk_id in seen:
                continue
            seen.add(item.chunk_id)
            result.append(item)
        return result
