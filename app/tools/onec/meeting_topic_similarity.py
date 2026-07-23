"""Поиск похожих тем совещаний у одного руководителя.

Правила отбора кандидатов:
- только активные темы (дата закрытия не задана или строго позже сегодня);
- только темы указанного руководителя (Руководитель_Key).

Вид совещания не исключает тему из сравнения.
Участники и описание не штрафуют тему, если в 1С эти данные не заполнены.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

import requests

from app.core.config import settings
from app.core.logging import get_logger
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.lookup_user_ref import is_empty_key, normalize_name, resolve_user_by_fio
from app.tools.onec.meeting_topic_participants import (
    extract_participant_keys,
    fetch_participant_rows,
)
from app.tools.onec.meeting_topics_by_manager import (
    fetch_all_meeting_topics,
    filter_topics_for_manager_similarity,
)

logger = get_logger(__name__)

DEFAULT_TEXT_SIMILARITY_THRESHOLD = 0.82
SimilarityMethod = Literal["embedding", "text", "mixed"]
DimensionName = Literal["topic", "participants", "details"]


@dataclass(frozen=True)
class TopicComparisonInput:
    title: str
    meeting_type: str | None = None
    details: str | None = None
    participant_refs: frozenset[str] = frozenset()


def normalize_topic_title(value: str | None) -> str:
    return normalize_name(value)


def build_topic_embedding_text(
    description: str | None,
    *,
    meeting_type: str | None = None,
) -> str:
    del meeting_type
    return (description or "").strip()


def build_details_embedding_text(details: str | None) -> str:
    return (details or "").strip()


def topic_title_tokens(value: str | None) -> set[str]:
    return {
        token
        for token in normalize_topic_title(value).split()
        if len(token) >= 3
    }


def topic_title_similarity_score(left: str | None, right: str | None) -> float:
    left_norm = normalize_topic_title(left)
    right_norm = normalize_topic_title(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = topic_title_tokens(left)
    right_tokens = topic_title_tokens(right)
    if not left_tokens or not right_tokens:
        return sequence_score
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    contains_bonus = 0.0
    if len(left_norm) >= 10 and len(right_norm) >= 10:
        if left_norm in right_norm or right_norm in left_norm:
            contains_bonus = 0.15
    return min(max(sequence_score, jaccard) + contains_bonus, 1.0)


def text_similarity_score(left: str | None, right: str | None) -> float:
    left_norm = normalize_topic_title(left)
    right_norm = normalize_topic_title(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return min(SequenceMatcher(None, left_norm, right_norm).ratio(), 1.0)


def participant_similarity_score(
    left_refs: set[str] | frozenset[str],
    right_refs: set[str] | frozenset[str],
) -> float:
    left = {ref.strip().lower() for ref in left_refs if not is_empty_key(ref)}
    right = {ref.strip().lower() for ref in right_refs if not is_empty_key(ref)}
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return float(sum(a * b for a, b in zip(left, right, strict=True)))


def similarity_weights() -> dict[DimensionName, float]:
    return {
        "topic": settings.MEETING_TOPIC_SIMILARITY_WEIGHT_TOPIC,
        "participants": settings.MEETING_TOPIC_SIMILARITY_WEIGHT_PARTICIPANTS,
        "details": settings.MEETING_TOPIC_SIMILARITY_WEIGHT_DETAILS,
    }


def compute_weighted_similarity(
    scores: dict[DimensionName, float | None],
    *,
    weights: dict[DimensionName, float] | None = None,
) -> float:
    resolved_weights = weights or similarity_weights()
    active = {name: score for name, score in scores.items() if score is not None}
    if not active:
        return 0.0
    total_weight = sum(resolved_weights[name] for name in active)
    if total_weight <= 0:
        return 0.0
    return sum(active[name] * resolved_weights[name] for name in active) / total_weight


def filter_topics_by_meeting_type(
    topics: list[dict[str, Any]],
    *,
    meeting_type: str | None,
) -> list[dict[str, Any]]:
    if not meeting_type:
        return list(topics)
    return [
        topic
        for topic in topics
        if not topic.get("meeting_type") or topic.get("meeting_type") == meeting_type
    ]


def load_participants_by_topic(
    session: requests.Session,
    config: ODataConfig,
    topics: list[dict[str, Any]],
) -> dict[str, set[str]]:
    participants_by_topic: dict[str, set[str]] = {}
    for topic in topics:
        ref_key = topic.get("ref_key")
        if is_empty_key(ref_key):
            continue
        rows = fetch_participant_rows(session, config, str(ref_key))
        participants_by_topic[str(ref_key)] = set(extract_participant_keys(rows))
    return participants_by_topic


def resolve_comparison_participants(
    session: requests.Session,
    config: ODataConfig,
    *,
    manager_ref_key: str,
    participant_fios: list[str] | None,
) -> frozenset[str]:
    refs = {manager_ref_key}
    if participant_fios:
        for raw_fio in participant_fios:
            fio = (raw_fio or "").strip()
            if not fio:
                continue
            try:
                user_ref, _, _ = resolve_user_by_fio(session, fio, config=config)
            except ValueError:
                logger.warning("meeting_topic_similarity.participant_not_found", fio=fio)
                continue
            if not is_empty_key(user_ref):
                refs.add(user_ref)
    return frozenset(ref for ref in refs if not is_empty_key(ref))


def _score_dimensions_by_text(
    candidate: TopicComparisonInput,
    topic: dict[str, Any],
    *,
    participants_by_topic: dict[str, set[str]],
) -> dict[DimensionName, float]:
    ref_key = str(topic.get("ref_key") or "")
    return {
        "topic": topic_title_similarity_score(candidate.title, topic.get("description")),
        "participants": participant_similarity_score(
            candidate.participant_refs,
            participants_by_topic.get(ref_key, set()),
        ),
        "details": text_similarity_score(candidate.details, topic.get("details")),
    }


async def _embed_text_scores(
    candidate: TopicComparisonInput,
    topics: list[dict[str, Any]],
    *,
    field: Literal["topic", "details"],
) -> list[float]:
    from app.services.embeddings.embedding_service import embedding_service

    if field == "topic":
        query_text = build_topic_embedding_text(candidate.title, meeting_type=candidate.meeting_type)
        topic_texts = [
            build_topic_embedding_text(topic.get("description"), meeting_type=topic.get("meeting_type"))
            for topic in topics
        ]
    else:
        query_text = build_details_embedding_text(candidate.details)
        topic_texts = [build_details_embedding_text(topic.get("details")) for topic in topics]

    if not query_text:
        return [0.0 for _ in topics]

    texts_to_embed = [query_text]
    indexes: list[int] = []
    for index, text in enumerate(topic_texts):
        if text:
            texts_to_embed.append(text)
            indexes.append(index)

    if len(texts_to_embed) == 1:
        return [0.0 for _ in topics]

    batch = await embedding_service.embed_texts(texts_to_embed)
    query_vector = batch.items[0].vector
    scores = [0.0 for _ in topics]
    for offset, topic_index in enumerate(indexes, start=1):
        scores[topic_index] = cosine_similarity(query_vector, batch.items[offset].vector)
    return scores


async def score_topics_for_candidate(
    candidate: TopicComparisonInput,
    topics: list[dict[str, Any]],
    *,
    participants_by_topic: dict[str, set[str]],
    use_embeddings: bool,
) -> list[dict[DimensionName, float]]:
    if not topics:
        return []

    if use_embeddings:
        try:
            topic_scores = await _embed_text_scores(candidate, topics, field="topic")
            details_scores = await _embed_text_scores(candidate, topics, field="details")
            dimension_scores: list[dict[DimensionName, float]] = []
            for index, topic in enumerate(topics):
                ref_key = str(topic.get("ref_key") or "")
                text_topic_score = topic_title_similarity_score(
                    candidate.title,
                    topic.get("description"),
                )
                dimension_scores.append(
                    {
                        "topic": max(topic_scores[index], text_topic_score),
                        "participants": participant_similarity_score(
                            candidate.participant_refs,
                            participants_by_topic.get(ref_key, set()),
                        ),
                        "details": (
                            details_scores[index]
                            if build_details_embedding_text(candidate.details)
                            and build_details_embedding_text(topic.get("details"))
                            else text_similarity_score(candidate.details, topic.get("details"))
                        ),
                    }
                )
            return dimension_scores
        except Exception as exc:
            logger.warning("meeting_topic_similarity.embedding_failed", error=str(exc))

    return [
        _score_dimensions_by_text(
            candidate,
            topic,
            participants_by_topic=participants_by_topic,
        )
        for topic in topics
    ]


def _attach_best_match(
    candidates: list[tuple[float, dict[str, Any], dict[str, float]]],
    *,
    similarity_method: SimilarityMethod,
) -> dict[str, Any] | None:
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    total_score, topic, breakdown = candidates[0]
    best = dict(topic)
    best["similarity_score"] = round(total_score, 4)
    best["similarity_method"] = similarity_method
    best["similarity_breakdown"] = {name: round(value, 4) for name, value in breakdown.items()}
    return best


def resolve_active_dimension_scores(
    candidate: TopicComparisonInput,
    topic: dict[str, Any],
    breakdown: dict[DimensionName, float],
    *,
    participants_by_topic: dict[str, set[str]],
) -> dict[DimensionName, float | None]:
    ref_key = str(topic.get("ref_key") or "")
    topic_participants = participants_by_topic.get(ref_key, set())
    return {
        "topic": breakdown["topic"],
        "participants": (
            breakdown["participants"]
            if candidate.participant_refs and topic_participants
            else None
        ),
        "details": (
            breakdown["details"]
            if build_details_embedding_text(candidate.details)
            and build_details_embedding_text(topic.get("details"))
            else None
        ),
    }


async def find_similar_topic_for_candidate(
    session: requests.Session,
    config: ODataConfig,
    *,
    candidate: TopicComparisonInput,
    topics: list[dict[str, Any]],
    threshold: float | None = None,
    use_embeddings: bool | None = None,
) -> dict[str, Any] | None:
    if not topics:
        return None

    participants_by_topic = load_participants_by_topic(session, config, topics)
    use_embedding_similarity = (
        settings.MEETING_TOPIC_SIMILARITY_USE_EMBEDDINGS
        if use_embeddings is None
        else use_embeddings
    )
    dimension_scores = await score_topics_for_candidate(
        candidate,
        topics,
        participants_by_topic=participants_by_topic,
        use_embeddings=use_embedding_similarity,
    )

    resolved_threshold = (
        threshold if threshold is not None else settings.MEETING_TOPIC_SIMILARITY_THRESHOLD
    )
    min_topic_score = settings.MEETING_TOPIC_SIMILARITY_MIN_TOPIC_SCORE
    method: SimilarityMethod = "embedding" if use_embedding_similarity else "text"
    candidates: list[tuple[float, dict[str, Any], dict[str, float]]] = []

    for topic, breakdown in zip(topics, dimension_scores, strict=True):
        if breakdown["topic"] < min_topic_score:
            continue
        active_scores = resolve_active_dimension_scores(
            candidate,
            topic,
            breakdown,
            participants_by_topic=participants_by_topic,
        )
        total_score = compute_weighted_similarity(active_scores)
        # Сильное совпадение названия достаточно: участники серии часто
        # отличаются от сохранённых в теме 1С и не должны блокировать подсказку.
        qualifying_score = max(total_score, breakdown["topic"])
        if qualifying_score >= resolved_threshold:
            candidates.append((qualifying_score, topic, breakdown))

    return _attach_best_match(candidates, similarity_method=method)


async def find_similar_topic_for_manager_async(
    session: requests.Session,
    config: ODataConfig,
    *,
    manager_ref_key: str,
    description: str,
    meeting_type: str | None = None,
    topic_details: str | None = None,
    participant_fios: list[str] | None = None,
    active_only: bool = True,
    threshold: float | None = None,
    use_embeddings: bool | None = None,
) -> dict[str, Any] | None:
    topics = fetch_all_meeting_topics(
        session,
        config,
        active_only=active_only,
        manager_ref_key=manager_ref_key,
        expand_related=False,
    )
    topics = filter_topics_for_manager_similarity(
        topics,
        manager_ref_key=manager_ref_key,
    )
    if not topics:
        return None

    candidate = TopicComparisonInput(
        title=description,
        meeting_type=meeting_type,
        details=topic_details,
        participant_refs=resolve_comparison_participants(
            session,
            config,
            manager_ref_key=manager_ref_key,
            participant_fios=participant_fios,
        ),
    )
    return await find_similar_topic_for_candidate(
        session,
        config,
        candidate=candidate,
        topics=topics,
        threshold=threshold,
        use_embeddings=use_embeddings,
    )


def find_similar_topic_for_manager(
    session: requests.Session,
    config: ODataConfig,
    *,
    manager_ref_key: str,
    description: str,
    meeting_type: str | None = None,
    topic_details: str | None = None,
    participant_fios: list[str] | None = None,
    active_only: bool = True,
    threshold: float | None = None,
    use_embeddings: bool | None = None,
) -> dict[str, Any] | None:
    return _run_coroutine(
        find_similar_topic_for_manager_async(
            session,
            config,
            manager_ref_key=manager_ref_key,
            description=description,
            meeting_type=meeting_type,
            topic_details=topic_details,
            participant_fios=participant_fios,
            active_only=active_only,
            threshold=threshold,
            use_embeddings=use_embeddings,
        )
    )


def _run_coroutine(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "find_similar_topic_for_manager нельзя вызывать из активного event loop; "
        "используйте find_similar_topic_for_manager_async"
    )


def topic_titles_similar(
    left: str | None,
    right: str | None,
    *,
    threshold: float = DEFAULT_TEXT_SIMILARITY_THRESHOLD,
) -> bool:
    return topic_title_similarity_score(left, right) >= threshold


def find_similar_topic_by_text(
    topics: list[dict[str, Any]],
    *,
    description: str,
    meeting_type: str | None = None,
    threshold: float = DEFAULT_TEXT_SIMILARITY_THRESHOLD,
) -> dict[str, Any] | None:
    session = create_session(CONFIG)
    candidate = TopicComparisonInput(title=description, meeting_type=meeting_type)
    return _run_coroutine(
        find_similar_topic_for_candidate(
            session,
            CONFIG,
            candidate=candidate,
            topics=topics,
            threshold=threshold,
            use_embeddings=False,
        )
    )


async def find_similar_topic_by_embeddings(
    topics: list[dict[str, Any]],
    *,
    description: str,
    meeting_type: str | None = None,
    threshold: float | None = None,
) -> dict[str, Any] | None:
    session = create_session(CONFIG)
    candidate = TopicComparisonInput(title=description, meeting_type=meeting_type)
    return await find_similar_topic_for_candidate(
        session,
        CONFIG,
        candidate=candidate,
        topics=topics,
        threshold=threshold,
        use_embeddings=True,
    )


def find_similar_topic(
    topics: list[dict[str, Any]],
    *,
    description: str,
    meeting_type: str | None = None,
    threshold: float = DEFAULT_TEXT_SIMILARITY_THRESHOLD,
) -> dict[str, Any] | None:
    return find_similar_topic_by_text(
        topics,
        description=description,
        meeting_type=meeting_type,
        threshold=threshold,
    )
