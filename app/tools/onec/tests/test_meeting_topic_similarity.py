from unittest.mock import AsyncMock, patch

import pytest

from app.services.embeddings.schemas import EmbeddingBatchResult, EmbeddingResult
from app.tools.onec.meeting_topic_similarity import (
    TopicComparisonInput,
    build_topic_embedding_text,
    compute_weighted_similarity,
    cosine_similarity,
    find_similar_topic_by_text,
    find_similar_topic_for_candidate,
    participant_similarity_score,
    text_similarity_score,
    topic_title_similarity_score,
    topic_titles_similar,
)


def test_topic_titles_similar_exact_match() -> None:
    title = "Еженедельное совещание с главным метрологом"
    assert topic_titles_similar(title, title) is True


def test_compute_weighted_similarity_uses_importance_order() -> None:
    score = compute_weighted_similarity(
        {
            "topic": 0.9,
            "participants": 0.5,
            "details": 0.5,
        },
        weights={"topic": 0.5, "participants": 0.3, "details": 0.2},
    )

    assert score == pytest.approx(0.9 * 0.5 + 0.5 * 0.3 + 0.5 * 0.2)


def test_participant_similarity_score_jaccard() -> None:
    assert participant_similarity_score({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_find_similar_topic_by_text_prefers_best_score() -> None:
    topics = [
        {
            "ref_key": "a",
            "code": "0001",
            "description": "Совещание с главным метрологом",
            "meeting_type": "Отчетное",
        },
        {
            "ref_key": "b",
            "code": "0002",
            "description": "Еженедельное совещание с главным метрологом",
            "meeting_type": "Отчетное",
        },
    ]

    with patch(
        "app.tools.onec.meeting_topic_similarity.load_participants_by_topic",
        return_value={"a": set(), "b": set()},
    ):
        match = find_similar_topic_by_text(
            topics,
            description="Еженедельное совещание с главным метрологом",
            meeting_type="Отчетное",
            threshold=0.7,
        )

    assert match is not None
    assert match["ref_key"] == "b"
    assert match["similarity_breakdown"]["topic"] == 1.0


def test_build_topic_embedding_text_uses_title_only() -> None:
    text = build_topic_embedding_text(
        "Еженедельное совещание с главным метрологом",
        meeting_type="Отчетное",
    )
    assert text == "Еженедельное совещание с главным метрологом"
    assert "Отчетное" not in text


def test_cosine_similarity_for_normalized_vectors() -> None:
    vector = [1.0, 0.0, 0.0]
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)


def test_text_similarity_score_empty_both() -> None:
    assert text_similarity_score("", "") == 1.0


@pytest.mark.asyncio
async def test_find_similar_topic_for_candidate_uses_participants_and_details() -> None:
    topics = [
        {
            "ref_key": "b",
            "code": "0002",
            "description": "Совещание с главным метрологом",
            "details": "Обсуждение показателей метрологической службы",
            "meeting_type": "Отчетное",
        }
    ]
    candidate = TopicComparisonInput(
        title="Еженедельное совещание с главным метрологом",
        meeting_type="Отчетное",
        details="Обсуждение показателей метрологической службы",
        participant_refs=frozenset({"user-1", "user-2"}),
    )

    async def fake_embed_texts(texts: list[str]) -> EmbeddingBatchResult:
        vectors = [[1.0, 0.0] if "метролог" in text.lower() else [0.0, 1.0] for text in texts]
        return EmbeddingBatchResult(
            items=[
                EmbeddingResult(
                    text_hash=f"hash-{index}",
                    vector=vector,
                    provider="test",
                    model="test",
                    vector_size=len(vector),
                    status="completed",
                )
                for index, vector in enumerate(vectors)
            ],
            provider="test",
            model="test",
            vector_size=2,
            total=len(vectors),
            failed_count=0,
        )

    with patch(
        "app.services.embeddings.embedding_service.embedding_service.embed_texts",
        new=AsyncMock(side_effect=fake_embed_texts),
    ):
        with patch(
            "app.tools.onec.meeting_topic_similarity.load_participants_by_topic",
            return_value={"b": {"user-1", "user-2"}},
        ):
            with patch(
                "app.tools.onec.meeting_topic_similarity.topic_title_similarity_score",
                return_value=0.95,
            ):
                match = await find_similar_topic_for_candidate(
                    session=AsyncMock(),
                    config=AsyncMock(),
                    candidate=candidate,
                    topics=topics,
                    threshold=0.85,
                    use_embeddings=True,
                )

    assert match is not None
    assert match["ref_key"] == "b"
    assert match["similarity_breakdown"]["participants"] == 1.0
    assert match["similarity_breakdown"]["details"] == 1.0


def test_topic_title_similarity_score_range() -> None:
    score = topic_title_similarity_score("abc", "abd")
    assert 0.0 < score < 1.0


@pytest.mark.asyncio
async def test_find_similar_topic_matches_exact_title_across_meeting_types() -> None:
    topics = [
        {
            "ref_key": "selector",
            "code": "000010332",
            "description": "Производственный селектор",
            "details": "",
            "meeting_type": "Отчетное",
        }
    ]
    candidate = TopicComparisonInput(
        title="Производственный селектор",
        meeting_type="Внеплановое",
        details="Производственный селектор",
        participant_refs=frozenset({"manager-ref"}),
    )

    with patch(
        "app.tools.onec.meeting_topic_similarity.load_participants_by_topic",
        return_value={"selector": set()},
    ):
        match = await find_similar_topic_for_candidate(
            session=AsyncMock(),
            config=AsyncMock(),
            candidate=candidate,
            topics=topics,
            threshold=0.85,
            use_embeddings=False,
        )

    assert match is not None
    assert match["code"] == "000010332"
    assert match["similarity_score"] == pytest.approx(1.0)
