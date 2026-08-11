"""BGE train/eval helpers: deterministic split and operator-label evaluation."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from agent_pochta.config import Settings, get_settings
from agent_pochta.db.message_filters import (
    load_payload_dict,
    operator_review_state_sql_flags,
    resolved_turbo_recipient,
)
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.routing.bge_department import predict_department_bge
from agent_pochta.services.email_indexing import build_indexing_text_from_row

TARGET_ACCURACY = 0.90
DEFAULT_SPLIT_SEED = "bge_train_eval_v1"
DEFAULT_TEST_RATIO = 0.2


def email_id_hash_bucket(email_id: str, *, seed: str = DEFAULT_SPLIT_SEED) -> float:
    """Deterministic bucket in [0, 1) from email_id."""
    digest = hashlib.sha256(f"{seed}:{email_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def is_test_split(
    email_id: str,
    *,
    test_ratio: float = DEFAULT_TEST_RATIO,
    seed: str = DEFAULT_SPLIT_SEED,
) -> bool:
    """True if email_id belongs to the held-out test partition."""
    if test_ratio <= 0:
        return False
    if test_ratio >= 1:
        return True
    return email_id_hash_bucket(email_id, seed=seed) < test_ratio


def split_email_ids(
    email_ids: Iterable[str],
    *,
    test_ratio: float = DEFAULT_TEST_RATIO,
    seed: str = DEFAULT_SPLIT_SEED,
) -> tuple[list[str], list[str]]:
    """Split ids into train/test partitions without overlap."""
    train: list[str] = []
    test: list[str] = []
    seen: set[str] = set()
    for raw_id in email_ids:
        email_id = str(raw_id).strip()
        if not email_id or email_id in seen:
            continue
        seen.add(email_id)
        if is_test_split(email_id, test_ratio=test_ratio, seed=seed):
            test.append(email_id)
        else:
            train.append(email_id)
    return train, test


def collect_operator_labeled_rows(session: Session) -> list[EmailMessageRow]:
    """Rows with operator correction or operator verification labels."""
    is_corrected, is_verified, _ = operator_review_state_sql_flags(
        EmailMessageRow.raw_payload_json,
        EmailMessageRow.id,
    )
    query = (
        select(EmailMessageRow)
        .where(or_(is_corrected, is_verified))
        .where(EmailMessageRow.department_id.isnot(None))
        .where(EmailMessageRow.department_id != "")
        .where(EmailMessageRow.is_spam.is_(False))
        .order_by(EmailMessageRow.received_at.desc())
        .options(selectinload(EmailMessageRow.attachments))
    )
    return list(session.scalars(query))


def _email_content(row: EmailMessageRow, payload: dict[str, Any]) -> str:
    stored = str(payload.get("embedding_source_text") or "").strip()
    if stored:
        return stored
    return build_indexing_text_from_row(row)


def _label_source(row: EmailMessageRow, payload: dict[str, Any]) -> str:
    if payload.get("operator_corrected"):
        return "operator_corrected"
    return "operator_verified"


def _department_stats(
    samples: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_dept: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "correct": 0})
    for sample in samples:
        dept_id = str(sample.get("actual") or "")
        if not dept_id:
            continue
        by_dept[dept_id]["total"] += 1
        if sample.get("correct"):
            by_dept[dept_id]["correct"] += 1
    for dept_id, stats in by_dept.items():
        total = stats["total"]
        correct = stats["correct"]
        stats["accuracy"] = round(correct / total, 4) if total else 0.0
        stats["department_id"] = dept_id
    return dict(by_dept)


def evaluate_operator_labels(
    rows: list[EmailMessageRow],
    *,
    split_mode: str = "hash8020",
    test_ratio: float = DEFAULT_TEST_RATIO,
    seed: str = DEFAULT_SPLIT_SEED,
    settings: Settings | None = None,
    min_score: float | None = None,
) -> dict[str, Any]:
    """Evaluate BGE routing on operator-labeled emails."""
    settings = settings or get_settings()
    min_score = min_score if min_score is not None else settings.bge_dept_min_score

    email_ids = [str(row.id) for row in rows]
    train_ids, test_ids = split_email_ids(email_ids, test_ratio=test_ratio, seed=seed)
    test_id_set = set(test_ids)

    if split_mode == "loo":
        eval_rows = rows
        exclude_self_only = True
    elif split_mode == "hash8020":
        eval_rows = [row for row in rows if str(row.id) in test_id_set]
        exclude_self_only = True
    else:
        raise ValueError(f"unsupported split_mode: {split_mode}")

    samples: list[dict[str, Any]] = []
    correct = 0
    direct = 0

    for row in eval_rows:
        payload = load_payload_dict(row.raw_payload_json) or {}
        recipient = resolved_turbo_recipient(mailbox=row.mailbox, payload=payload)
        if not recipient:
            recipient = str(payload.get("routing_recipient") or row.mailbox or "")
        content = _email_content(row, payload)
        exclude_ids = {str(row.id)} if exclude_self_only else set()
        prediction = predict_department_bge(
            content,
            recipient,
            settings=settings,
            exclude_email_ids=exclude_ids,
        )
        actual = str(row.department_id or "")
        predicted = prediction.dept_id if prediction.ok else ""
        is_correct = bool(predicted and predicted == actual)
        if is_correct:
            correct += 1
        if prediction.ok and prediction.score is not None and prediction.score >= min_score:
            direct += 1
        samples.append(
            {
                "email_id": str(row.id),
                "label_source": _label_source(row, payload),
                "actual": actual,
                "actual_name": row.department_name or "",
                "predicted": predicted,
                "predicted_name": prediction.dept_name if prediction.ok else "",
                "score": prediction.score,
                "correct": is_correct,
                "reason": prediction.reason,
            }
        )

    total = len(eval_rows)
    accuracy = round(correct / total, 4) if total else 0.0
    by_department = _department_stats(samples)

    return {
        "evaluated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "split_mode": split_mode,
        "test_ratio": test_ratio,
        "seed": seed,
        "labeled_total": len(rows),
        "train_size": len(train_ids),
        "test_size": len(test_ids) if split_mode == "hash8020" else len(rows),
        "evaluated": total,
        "correct": correct,
        "accuracy": accuracy,
        "direct_routes": direct,
        "min_score": min_score,
        "target_accuracy": TARGET_ACCURACY,
        "meets_target": accuracy >= TARGET_ACCURACY,
        "by_department": by_department,
        "samples": samples[:15],
    }


def evaluate_labeled_rows(
    rows: list[EmailMessageRow],
    *,
    settings: Settings | None = None,
    min_score: float | None = None,
    exclude_self: bool = False,
) -> dict[str, Any]:
    """Оценка BGE на списке размеченных писем; возвращает wrong_rows для дообучения."""
    settings = settings or get_settings()
    min_score = min_score if min_score is not None else settings.bge_dept_min_score

    samples: list[dict[str, Any]] = []
    wrong_rows: list[tuple[EmailMessageRow, dict[str, Any]]] = []
    correct = 0
    direct = 0
    skipped = 0

    for row in rows:
        payload = load_payload_dict(row.raw_payload_json) or {}
        recipient = resolved_turbo_recipient(mailbox=row.mailbox, payload=payload)
        if not recipient:
            recipient = str(payload.get("routing_recipient") or row.mailbox or "")
        content = _email_content(row, payload)
        if len(content.strip()) < settings.email_rag_min_chars:
            skipped += 1
            continue

        exclude_ids = {str(row.id)} if exclude_self else set()
        prediction = predict_department_bge(
            content,
            recipient,
            settings=settings,
            exclude_email_ids=exclude_ids,
        )
        actual = str(row.department_id or "")
        predicted = prediction.dept_id if prediction.ok else ""
        is_correct = bool(predicted and predicted == actual)
        sample = {
            "email_id": str(row.id),
            "label_source": _label_source(row, payload),
            "actual": actual,
            "predicted": predicted,
            "score": prediction.score,
            "correct": is_correct,
            "reason": prediction.reason,
        }
        samples.append(sample)
        if is_correct:
            correct += 1
            if prediction.ok and prediction.score is not None and prediction.score >= min_score:
                direct += 1
        else:
            wrong_rows.append((row, sample))

    evaluated = len(samples)
    accuracy = round(correct / evaluated, 4) if evaluated else 0.0
    return {
        "evaluated": evaluated,
        "skipped": skipped,
        "correct": correct,
        "wrong": len(wrong_rows),
        "accuracy": accuracy,
        "direct_routes": direct,
        "min_score": min_score,
        "target_accuracy": TARGET_ACCURACY,
        "meets_target": accuracy >= TARGET_ACCURACY,
        "samples": samples[:15],
        "wrong_rows": wrong_rows,
    }


def run_iterative_operator_training(
    session: Session,
    rows: list[EmailMessageRow],
    *,
    repo: Any,
    settings: Settings | None = None,
    target: float = TARGET_ACCURACY,
    max_iterations: int = 20,
    reextract: bool = False,
    holdout_eval_fn: Any | None = None,
) -> dict[str, Any]:
    """Итерация: eval → upsert промахов → eval снова, пока не target или max_iterations."""
    from agent_pochta.services.bge_correction_learning import upsert_correction_from_row

    settings = settings or get_settings()
    iterations: list[dict[str, Any]] = []
    accuracy = 0.0

    for iteration in range(1, max_iterations + 1):
        batch = evaluate_labeled_rows(rows, settings=settings, exclude_self=False)
        accuracy = float(batch["accuracy"])
        upserted = 0
        upsert_skipped = 0
        upsert_errors = 0

        for row, sample in batch["wrong_rows"]:
            actual_id = str(row.department_id or "")
            predicted_id = str(sample.get("predicted") or "")
            result = upsert_correction_from_row(
                repo,
                row,
                wrong_dept_id=predicted_id or None,
                wrong_dept_name="",
                correct_dept_id=actual_id,
                correct_dept_name=row.department_name or "",
                reextract=reextract,
                settings=settings,
            )
            if int(result.get("indexed") or 0) > 0:
                upserted += 1
            elif result.get("skipped"):
                upsert_skipped += 1
            else:
                upsert_errors += 1

        if upserted:
            session.commit()

        holdout: dict[str, Any] | None = None
        if holdout_eval_fn is not None:
            holdout = holdout_eval_fn()

        step = {
            "iteration": iteration,
            "operator_accuracy": accuracy,
            "operator_evaluated": batch["evaluated"],
            "operator_correct": batch["correct"],
            "operator_wrong": batch["wrong"],
            "upserted": upserted,
            "upsert_skipped": upsert_skipped,
            "upsert_errors": upsert_errors,
            "holdout_accuracy": holdout.get("accuracy") if holdout else None,
            "meets_target": accuracy >= target,
        }
        iterations.append(step)

        if accuracy >= target:
            break

    return {
        "target_accuracy": target,
        "accuracy": accuracy,
        "meets_target": accuracy >= target,
        "iterations_run": len(iterations),
        "labeled_total": len(rows),
        "iterations": iterations,
    }
