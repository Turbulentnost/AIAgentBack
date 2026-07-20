from app.services.document_processing.chunking.service import DocumentChunkingService
from app.services.document_processing.chunking.table_rows import (
    build_table_row_display_text,
    build_table_row_embedding_text,
    detect_table_structure,
)


def test_detect_table_structure_with_headers():
    rows = [
        ["Уровень платформы", "Рекомендуемая технология", "Назначение"],
        [
            "Векторная БД",
            "Qdrant или pgvector",
            "семантический поиск по регламентам, ГОСТам, шаблонам",
        ],
    ]
    structure = detect_table_structure(rows)
    assert structure.headers[0] == "Уровень платформы"
    assert len(structure.data_rows) == 1
    assert structure.data_rows[0][1] == "Qdrant или pgvector"


def test_build_table_row_texts():
    headers = ["Уровень платформы", "Рекомендуемая технология", "Назначение"]
    values = ["Векторная БД", "Qdrant или pgvector", "семантический поиск"]
    embedding = build_table_row_embedding_text(
        section_title="6. Технологический стек",
        table_caption=None,
        headers=headers,
        row_values=values,
    )
    display = build_table_row_display_text(headers=headers, row_values=values)

    assert "Раздел 6. Технологический стек." in embedding
    assert "Уровень платформы: Векторная БД." in embedding
    assert "Рекомендуемая технология: Qdrant или pgvector." in embedding
    assert "Уровень платформы — Векторная БД" in display
    assert "Qdrant или pgvector" in display
    assert embedding != display


def test_table_row_becomes_separate_chunks():
    service = DocumentChunkingService(db=None)  # type: ignore[arg-type]
    from app.services.document_processing.chunking import ParsedBlock

    block = ParsedBlock(
        text="Таблица 1\nheader | tech",
        block_type="table",
        section_title="6. Технологический стек",
        metadata={
            "rows": [
                ["Уровень платформы", "Рекомендуемая технология", "Назначение"],
                ["Векторная БД", "Qdrant или pgvector", "семантический поиск"],
                ["Backend", "FastAPI", "API и RAG"],
            ]
        },
    )
    row_blocks = service._table_block_to_row_blocks(block)
    assert len(row_blocks) == 2
    assert row_blocks[0].block_type == "table_row"
    assert "Qdrant или pgvector" in row_blocks[0].text
    assert row_blocks[0].metadata["display_text"] != row_blocks[0].text

    chunks = service._build_chunks(row_blocks, source="test", base_metadata={})
    assert len(chunks) == 2
    assert chunks[0]["text"] != chunks[0]["content"]
