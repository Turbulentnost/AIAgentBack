"""BGE-based department routing via department_corrections_bge."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agent_pochta.config import Settings, get_settings
from agent_pochta.services.embedding_client import EmbeddingClientError, embed_texts
from agent_pochta.services.email_rag_qdrant import search_department_corrections
from agent_pochta.services.routing_departments import resolve_department_display_name


@dataclass
class BgeDepartmentPrediction:
    ok: bool
    dept_id: str = ""
    dept_name: str = ""
    score: float | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


def _hits_to_candidates(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        dept_id = str(hit.get("dept_correct_id") or "").strip()
        if not dept_id or dept_id in seen:
            continue
        seen.add(dept_id)
        dept_name = str(hit.get("dept_correct_name") or "").strip()
        if not dept_name:
            dept_name = resolve_department_display_name(dept_id, dept_id)
        candidates.append(
            {
                "department_id": dept_id,
                "department_name": dept_name,
                "responsibility": "BGE correction match",
                "score": hit.get("score"),
            }
        )
    return candidates


def predict_department_bge(
    embed_text: str,
    recipient: str,
    *,
    settings: Settings | None = None,
    top_k: int | None = None,
    allowed_departments: set[str] | None = None,
    exclude_email_ids: set[str] | None = None,
) -> BgeDepartmentPrediction:
    settings = settings or get_settings()
    text = (embed_text or "").strip()
    if not text:
        return BgeDepartmentPrediction(ok=False, reason="empty_text")
    if not settings.embedding_base_url:
        return BgeDepartmentPrediction(ok=False, reason="no_embedding_url")

    limit = top_k or settings.bge_dept_top_k
    try:
        vector = embed_texts([text], settings=settings)[0]
        hits = search_department_corrections(
            url=settings.qdrant_url,
            query_vector=vector,
            limit=limit,
            recipient=recipient or None,
        )
    except EmbeddingClientError as exc:
        return BgeDepartmentPrediction(ok=False, reason=f"bge_error:{exc}")

    if not hits:
        return BgeDepartmentPrediction(ok=False, reason="no_hits")

    if exclude_email_ids:
        excluded = {str(item).strip() for item in exclude_email_ids if str(item).strip()}
        if excluded:
            hits = [
                h
                for h in hits
                if str(h.get("record_id") or h.get("email_id") or "") not in excluded
            ]
            if not hits:
                return BgeDepartmentPrediction(ok=False, reason="no_hits_after_exclude")

    if allowed_departments:
        hits = [
            h
            for h in hits
            if str(h.get("dept_correct_id") or "") in allowed_departments
        ]
        if not hits:
            return BgeDepartmentPrediction(ok=False, reason="no_allowed_candidates")

    candidates = _hits_to_candidates(hits)
    if not candidates:
        return BgeDepartmentPrediction(ok=False, reason="no_candidates")

    votes = Counter(str(h.get("dept_correct_id") or "") for h in hits if h.get("dept_correct_id"))
    winner_id, _ = votes.most_common(1)[0]
    best_hit = next(h for h in hits if str(h.get("dept_correct_id") or "") == winner_id)
    winner_name = str(best_hit.get("dept_correct_name") or "")
    if not winner_name:
        winner_name = resolve_department_display_name(winner_id, winner_id)

    return BgeDepartmentPrediction(
        ok=True,
        dept_id=winner_id,
        dept_name=winner_name,
        score=float(best_hit.get("score") or 0.0),
        candidates=candidates,
        reason="vote",
    )
