from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document import DocumentChunk
from app.services.document_processing.chunking.table_rows import (
    build_table_row_display_text,
    build_table_row_embedding_text,
    detect_table_structure,
    is_probably_table_block,
)


class DocumentChunkingError(RuntimeError):
    pass


@dataclass
class ParsedBlock:
    text: str
    block_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    page_number: int | None = None
    section_title: str | None = None
    sheet_name: str | None = None
    cell_range: str | None = None


class DocumentChunkingService:
    """Hybrid chunking for parsed PDF, DOCX, XLSX and OCR documents."""

    # Версия алгоритма chunking. Увеличивается при изменении логики разбиения,
    # чтобы переиндексация перепарсила документы со старыми чанками.
    CHUNKING_VERSION = 2

    DEFAULT_CHUNK_TOKENS = 850
    DEFAULT_OVERLAP_TOKENS = 120
    DEFAULT_MIN_CHUNK_TOKENS = 120

    def __init__(
        self,
        db: AsyncSession,
        *,
        chunk_size_tokens: int = DEFAULT_CHUNK_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
        min_chunk_tokens: int = DEFAULT_MIN_CHUNK_TOKENS,
    ) -> None:
        self.db = db
        self.chunk_size_tokens = chunk_size_tokens
        self.overlap_tokens = overlap_tokens
        self.min_chunk_tokens = min_chunk_tokens

    async def replace_chunks(
        self,
        *,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID,
        blocks: list[ParsedBlock],
        source: str,
        base_metadata: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        normalized_blocks = self._normalize_blocks(blocks)
        if not normalized_blocks:
            raise DocumentChunkingError("Недостаточно текста для chunking")

        expanded_blocks = self._expand_table_blocks(normalized_blocks)
        chunks = self._build_chunks(expanded_blocks, source=source, base_metadata=base_metadata or {})
        if not chunks:
            raise DocumentChunkingError("Не удалось сформировать chunks")

        await self.db.execute(
            delete(DocumentChunk).where(DocumentChunk.document_version_id == document_version_id)
        )

        created: list[DocumentChunk] = []
        for index, chunk in enumerate(chunks):
            text = chunk["text"]
            content = chunk.get("content") or text
            metadata = chunk["metadata"]
            item = DocumentChunk(
                document_id=document_id,
                document_version_id=document_version_id,
                chunk_index=index,
                text=text,
                page_number=metadata.get("page_number"),
                section_title=metadata.get("section_title"),
                token_count=self._count_tokens(text),
                qdrant_collection=settings.QDRANT_COLLECTION,
                embedding_model=settings.LLM_EMBEDDING_MODEL,
                is_indexed=False,
                metadata_=metadata,
                content=content,
                chunk_metadata=metadata,
            )
            self.db.add(item)
            created.append(item)

        await self.db.flush()
        return created

    def _normalize_blocks(self, blocks: list[ParsedBlock]) -> list[ParsedBlock]:
        normalized: list[ParsedBlock] = []
        for block in blocks:
            text = self._clean_text(block.text)
            if not text:
                continue
            normalized.append(
                ParsedBlock(
                    text=text,
                    block_type=block.block_type,
                    metadata=block.metadata,
                    page_number=block.page_number,
                    section_title=block.section_title,
                    sheet_name=block.sheet_name,
                    cell_range=block.cell_range,
                )
            )
        return normalized

    def _expand_table_blocks(self, blocks: list[ParsedBlock]) -> list[ParsedBlock]:
        """Каждая строка таблицы — отдельный блок с разным текстом для embedding и UI."""
        expanded: list[ParsedBlock] = []
        for block in blocks:
            if not is_probably_table_block(block.block_type, block.metadata):
                expanded.append(block)
                continue
            expanded.extend(self._table_block_to_row_blocks(block))
        return expanded

    def _table_block_to_row_blocks(self, block: ParsedBlock) -> list[ParsedBlock]:
        rows = block.metadata.get("rows") or []
        if not rows:
            return [block]

        structure = detect_table_structure(rows)
        if not structure.data_rows:
            return [block]

        table_index = block.metadata.get("table_index")
        sheet_name = block.sheet_name or block.metadata.get("sheet_name")
        table_caption = structure.caption or block.metadata.get("table_caption") or block.section_title
        if block.block_type == "sheet" and sheet_name and not table_caption:
            table_caption = f"Лист: {sheet_name}"

        # Не тащим все строки таблицы в metadata каждой строки.
        base_row_metadata = {key: value for key, value in block.metadata.items() if key != "rows"}

        row_blocks: list[ParsedBlock] = []
        for row_index, row_values in enumerate(structure.data_rows):
            if not any(cell.strip() for cell in row_values):
                continue
            embedding_text = build_table_row_embedding_text(
                section_title=block.section_title,
                table_caption=table_caption,
                headers=structure.headers,
                row_values=row_values,
            )
            display_text = build_table_row_display_text(
                headers=structure.headers,
                row_values=row_values,
            )
            if not embedding_text.strip():
                continue
            row_blocks.append(
                ParsedBlock(
                    text=embedding_text,
                    block_type="table_row",
                    metadata={
                        **base_row_metadata,
                        "chunk_kind": "table_row",
                        "fragment_type": "table_row",
                        "display_text": display_text,
                        "table_index": table_index,
                        "row_index": row_index,
                        "headers": structure.headers,
                        "row_values": row_values,
                        "table_caption": table_caption,
                    },
                    page_number=block.page_number,
                    section_title=block.section_title,
                    sheet_name=block.sheet_name,
                    cell_range=block.cell_range,
                )
            )
        return row_blocks or [block]

    def _build_chunks(
        self,
        blocks: list[ParsedBlock],
        *,
        source: str,
        base_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        current_blocks: list[ParsedBlock] = []
        current_tokens = 0

        def flush() -> None:
            nonlocal current_blocks, current_tokens
            if not current_blocks:
                return
            text = "\n\n".join(block.text for block in current_blocks)
            if self._count_tokens(text) < self.min_chunk_tokens and chunks:
                chunks[-1]["text"] = f"{chunks[-1]['text']}\n\n{text}"
                prev_content = chunks[-1].get("content") or chunks[-1]["text"]
                chunks[-1]["content"] = f"{prev_content}\n\n{text}"
                chunks[-1]["metadata"] = self._merge_chunk_metadata(
                    chunks[-1]["metadata"],
                    self._metadata_for_blocks(current_blocks, source, base_metadata),
                )
            else:
                chunks.append(
                    {
                        "text": text,
                        "content": text,
                        "metadata": self._metadata_for_blocks(current_blocks, source, base_metadata),
                    }
                )
            overlap = self._overlap_blocks(current_blocks)
            current_blocks = overlap
            current_tokens = sum(self._count_tokens(block.text) for block in current_blocks)

        for block in blocks:
            if block.block_type == "table_row":
                flush()
                metadata = self._metadata_for_blocks([block], source, base_metadata)
                chunks.append(
                    {
                        "text": block.text,
                        "content": block.metadata.get("display_text") or block.text,
                        "metadata": metadata,
                    }
                )
                continue

            block_tokens = self._count_tokens(block.text)
            if block_tokens > self.chunk_size_tokens:
                flush()
                for split_block in self._split_large_block(block):
                    split_tokens = self._count_tokens(split_block.text)
                    if current_blocks and current_tokens + split_tokens > self.chunk_size_tokens:
                        flush()
                    current_blocks.append(split_block)
                    current_tokens += split_tokens
                continue

            if current_blocks and current_tokens + block_tokens > self.chunk_size_tokens:
                flush()
            current_blocks.append(block)
            current_tokens += block_tokens

        if current_blocks:
            text = "\n\n".join(block.text for block in current_blocks)
            if text and (not chunks or text != chunks[-1]["text"]):
                chunks.append(
                    {
                        "text": text,
                        "content": text,
                        "metadata": self._metadata_for_blocks(current_blocks, source, base_metadata),
                    }
                )
        return chunks

    def _split_large_block(self, block: ParsedBlock) -> list[ParsedBlock]:
        units = self._split_sentences_or_words(block.text)
        split_blocks: list[ParsedBlock] = []
        current: list[str] = []
        current_tokens = 0
        for unit in units:
            tokens = self._count_tokens(unit)
            if current and current_tokens + tokens > self.chunk_size_tokens:
                split_blocks.append(self._copy_block(block, " ".join(current)))
                tail_text = self._sentence_aligned_tail(" ".join(current))
                current = [tail_text] if tail_text else []
                current_tokens = self._count_tokens(tail_text) if tail_text else 0
            current.append(unit)
            current_tokens += tokens
        if current:
            split_blocks.append(self._copy_block(block, " ".join(current)))
        return split_blocks

    def _split_sentences_or_words(self, text: str) -> list[str]:
        sentences = [item.strip() for item in re.split(r"(?<=[.!?。！？])\s+", text) if item.strip()]
        if len(sentences) > 1:
            return sentences
        words = text.split()
        return [" ".join(words[index : index + self.chunk_size_tokens]) for index in range(0, len(words), self.chunk_size_tokens)]

    def _overlap_blocks(self, blocks: list[ParsedBlock]) -> list[ParsedBlock]:
        overlap: list[ParsedBlock] = []
        tokens = 0
        for block in reversed(blocks):
            block_tokens = self._count_tokens(block.text)
            if tokens + block_tokens > self.overlap_tokens:
                tail_text = self._sentence_aligned_tail(block.text)
                if not overlap and tail_text:
                    overlap.append(self._copy_block(block, tail_text))
                break
            overlap.insert(0, block)
            tokens += block_tokens
        return overlap

    def _sentence_aligned_tail(self, text: str) -> str:
        """Возвращает последние целые предложения в пределах overlap_tokens.

        Перекрытие переносится целыми предложениями, поэтому следующий фрагмент
        начинается с начала предложения, а не с середины. Если в тексте лишь
        одно предложение, переносим последние слова (без разрыва слов)."""
        sentences = self._split_sentences_or_words(text)
        tail: list[str] = []
        tokens = 0
        for sentence in reversed(sentences):
            sentence_tokens = self._count_tokens(sentence)
            if tail and tokens + sentence_tokens > self.overlap_tokens:
                break
            tail.insert(0, sentence)
            tokens += sentence_tokens
        return " ".join(tail)

    def _metadata_for_blocks(
        self,
        blocks: list[ParsedBlock],
        source: str,
        base_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        block_types = sorted({block.block_type for block in blocks})
        pages = sorted({block.page_number for block in blocks if block.page_number is not None})
        sections = [block.section_title for block in blocks if block.section_title]
        sheet_names = [block.sheet_name for block in blocks if block.sheet_name]
        cell_ranges = [block.cell_range for block in blocks if block.cell_range]
        metadata: dict[str, Any] = {
            **base_metadata,
            "source": source,
            "block_types": block_types,
            "page_numbers": pages,
            "section_titles": list(dict.fromkeys(sections)),
            "sheet_names": list(dict.fromkeys(sheet_names)),
            "cell_ranges": list(dict.fromkeys(cell_ranges)),
            "blocks_count": len(blocks),
            "chunking": {
                "algorithm": "hybrid_structure_token_overlap",
                "version": self.CHUNKING_VERSION,
                "chunk_size_tokens": self.chunk_size_tokens,
                "overlap_tokens": self.overlap_tokens,
                "min_chunk_tokens": self.min_chunk_tokens,
            },
        }
        if len(pages) == 1:
            metadata["page_number"] = pages[0]
        if sections:
            metadata["section_title"] = sections[-1]
        if sheet_names:
            metadata["sheet_name"] = sheet_names[-1]
        if cell_ranges:
            metadata["cell_range"] = cell_ranges[-1]

        metadata["source_blocks"] = [block.metadata for block in blocks if block.metadata]
        return metadata

    def _merge_chunk_metadata(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        return {
            **left,
            "block_types": sorted(set(left.get("block_types", [])) | set(right.get("block_types", []))),
            "page_numbers": sorted(set(left.get("page_numbers", [])) | set(right.get("page_numbers", []))),
            "section_titles": list(dict.fromkeys(left.get("section_titles", []) + right.get("section_titles", []))),
            "sheet_names": list(dict.fromkeys(left.get("sheet_names", []) + right.get("sheet_names", []))),
            "cell_ranges": list(dict.fromkeys(left.get("cell_ranges", []) + right.get("cell_ranges", []))),
            "source_blocks": left.get("source_blocks", []) + right.get("source_blocks", []),
            "blocks_count": left.get("blocks_count", 0) + right.get("blocks_count", 0),
        }

    def _clean_text(self, text: str) -> str:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        cleaned_lines = [line for line in lines if line]
        return "\n".join(cleaned_lines).strip()

    def _copy_block(self, block: ParsedBlock, text: str) -> ParsedBlock:
        return ParsedBlock(
            text=text,
            block_type=block.block_type,
            metadata=block.metadata,
            page_number=block.page_number,
            section_title=block.section_title,
            sheet_name=block.sheet_name,
            cell_range=block.cell_range,
        )

    def _count_tokens(self, text: str) -> int:
        return len(text.split())
