"""Holdout eval: BGE routing accuracy vs 1С (exclude emails in corrections index)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qdrant_client import QdrantClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from agent_pochta.config import PROJECT_ROOT, get_settings  # noqa: E402
from agent_pochta.db.message_filters import load_payload_dict, resolved_turbo_recipient  # noqa: E402
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.routing.bge_department import predict_department_bge  # noqa: E402
from agent_pochta.services.email_indexing import build_indexing_text_from_row  # noqa: E402
from agent_pochta.services.email_rag_qdrant import DEPARTMENT_CORRECTIONS_COLLECTION  # noqa: E402


def _indexed_email_ids(url: str) -> set[str]:
    client = QdrantClient(url=url, prefer_grpc=False)
    ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=DEPARTMENT_CORRECTIONS_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=["email_id"],
            with_vectors=False,
        )
        for point in points:
            email_id = (point.payload or {}).get("email_id")
            if email_id:
                ids.add(str(email_id))
        if offset is None:
            break
    return ids


def _email_content(row: EmailMessageRow, payload: dict) -> str:
    stored = str(payload.get("embedding_source_text") or "").strip()
    if stored:
        return stored
    return build_indexing_text_from_row(row)


def evaluate(*, limit: int = 100, min_score: float | None = None) -> dict:
    settings = get_settings()
    min_score = min_score if min_score is not None else settings.bge_dept_min_score
    indexed = _indexed_email_ids(settings.qdrant_url)

    factory = get_session_factory()
    with factory() as session:
        rows = session.scalars(
            select(EmailMessageRow)
            .where(EmailMessageRow.status == "done")
            .where(EmailMessageRow.erp_document_number.isnot(None))
            .where(EmailMessageRow.erp_document_number != "SKIP-ERP")
            .where(EmailMessageRow.department_id.isnot(None))
            .where(EmailMessageRow.is_spam.is_(False))
            .order_by(EmailMessageRow.processed_at.desc())
            .limit(limit * 3)
            .options(selectinload(EmailMessageRow.attachments))
        ).all()

    holdout: list[EmailMessageRow] = []
    for row in rows:
        if str(row.id) in indexed:
            continue
        holdout.append(row)
        if len(holdout) >= limit:
            break

    correct = 0
    direct = 0
    low_conf = 0
    samples: list[dict] = []

    for row in holdout:
        payload = load_payload_dict(row.raw_payload_json) or {}
        recipient = resolved_turbo_recipient(mailbox=row.mailbox, payload=payload)
        if not recipient:
            recipient = str(payload.get("routing_recipient") or row.mailbox or "")
        content = _email_content(row, payload)
        prediction = predict_department_bge(content, recipient, settings=settings)
        actual = str(row.department_id or "")
        predicted = prediction.dept_id if prediction.ok else ""
        is_correct = bool(predicted and predicted == actual)
        if is_correct:
            correct += 1
        if prediction.ok and prediction.score is not None and prediction.score >= min_score:
            direct += 1
        elif prediction.ok:
            low_conf += 1
        samples.append(
            {
                "email_id": str(row.id),
                "actual": actual,
                "predicted": predicted,
                "score": prediction.score,
                "correct": is_correct,
                "reason": prediction.reason,
            }
        )

    total = len(holdout)
    accuracy = round(correct / total, 4) if total else 0.0
    result = {
        "evaluated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "holdout_size": total,
        "correct": correct,
        "accuracy": accuracy,
        "direct_routes": direct,
        "low_confidence": low_conf,
        "min_score": min_score,
        "target_accuracy": 0.90,
        "meets_target": accuracy >= 0.90,
        "samples": samples[:10],
    }

    out_dir = PROJECT_ROOT / "data" / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "bge_holdout_eval.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(out_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate BGE holdout routing accuracy")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-score", type=float, default=None)
    args = parser.parse_args()
    result = evaluate(limit=args.limit, min_score=args.min_score)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("meets_target"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
