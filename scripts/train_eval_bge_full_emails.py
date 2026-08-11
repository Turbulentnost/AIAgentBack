"""Полный цикл обучения BGE на полных письмах и оценки точности маршрутизации."""

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
sys.path.insert(0, str(ROOT / "scripts"))

from backfill_bge_corrections import backfill as backfill_corrections  # noqa: E402
from backfill_bge_verified import backfill as backfill_verified  # noqa: E402
from eval_bge_routing_holdout import _indexed_email_ids, evaluate as evaluate_holdout  # noqa: E402

from agent_pochta.config import PROJECT_ROOT, get_settings  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.services.bge_train_eval import (  # noqa: E402
    TARGET_ACCURACY,
    collect_operator_labeled_rows,
    evaluate_operator_labels,
)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _print_department_breakdown(by_department: dict[str, dict]) -> None:
    if not by_department:
        print("  (нет данных по отделам)")
        return
    for dept_id in sorted(by_department, key=lambda k: by_department[k].get("total", 0), reverse=True):
        stats = by_department[dept_id]
        total = stats.get("total", 0)
        correct = stats.get("correct", 0)
        acc = stats.get("accuracy", 0.0)
        print(f"  • {dept_id}: {correct}/{total} ({_pct(acc)})")


def print_russian_summary(report: dict) -> None:
    target_pct = int(TARGET_ACCURACY * 100)
    print()
    print("=" * 72)
    print("BGE: обучение на полных письмах и оценка точности маршрутизации")
    print("=" * 72)

    train = report.get("train") or {}
    corr = train.get("corrections") or {}
    ver = train.get("verified") or {}
    print("\n[1] ОБУЧЕНИЕ (индексация в department_corrections_bge)")
    print(
        f"  Правки оператора: upserted={corr.get('upserted', 0)}, "
        f"skipped={corr.get('skipped', 0)}, failed={corr.get('failed', 0)}"
    )
    print(
        f"  Подтверждения оператора: indexed={ver.get('indexed', 0)}, "
        f"skipped={ver.get('skipped', 0)}, failed={ver.get('failed', 0)}"
    )
    print(f"  IMAP reextract: {bool(train.get('reextract'))}")
    print(f"  Всего email_id в индексе: {report.get('indexed_email_ids_count', 0)}")

    holdout = report.get("eval_holdout") or {}
    holdout_acc = float(holdout.get("accuracy") or 0.0)
    print("\n[2] HOLDOUT (письма с ERP, не в индексе обучения)")
    print(
        f"  Выборка: {holdout.get('holdout_size', 0)}, "
        f"верно: {holdout.get('correct', 0)}, "
        f"точность: {_pct(holdout_acc)} (цель {target_pct}%)"
    )
    print(f"  meets_target: {bool(holdout.get('meets_target'))}")

    op = report.get("eval_operator_labels") or {}
    op_acc = float(op.get("accuracy") or 0.0)
    print("\n[3] МЕТКИ ОПЕРАТОРА (corrected + verified, split без утечки)")
    print(
        f"  Режим: {op.get('split_mode')}, "
        f"train/test: {op.get('train_size', 0)}/{op.get('test_size', 0)}, "
        f"оценено: {op.get('evaluated', 0)}"
    )
    print(
        f"  Верно: {op.get('correct', 0)}, "
        f"точность: {_pct(op_acc)} (цель {target_pct}%)"
    )
    print(f"  meets_target: {bool(op.get('meets_target'))}")
    print("  По отделам:")
    _print_department_breakdown(op.get("by_department") or {})

    print("\nИТОГ")
    print(f"  Целевая точность: {target_pct}%")
    print(f"  Holdout meets_target: {bool(report.get('holdout_meets_target'))}")
    print(f"  Operator labels meets_target: {bool(report.get('operator_meets_target'))}")
    print(f"  Общий meets_target (holdout): {bool(report.get('meets_target'))}")
    print(f"  Отчёт: {report.get('output_path')}")
    print("=" * 72)


def run_pipeline(
    *,
    reextract: bool = True,
    force_reindex_verified: bool = False,
    holdout_limit: int = 100,
    split_mode: str = "hash8020",
    test_ratio: float = 0.2,
    min_score: float | None = None,
    skip_train: bool = False,
) -> dict:
    settings = get_settings()
    started_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    train_report: dict = {"reextract": reextract}
    if skip_train:
        train_report["skipped"] = True
        corrections_result = {"upserted": 0, "skipped": 0, "failed": 0, "processed": 0}
        verified_result = {"indexed": 0, "skipped": 0, "failed": 0, "total_verified": 0}
    else:
        corrections_result = backfill_corrections(reextract=reextract, limit=None)
        verified_result = backfill_verified(
            reextract=reextract,
            limit=None,
            skip_indexed=not force_reindex_verified,
            force=force_reindex_verified,
        )
    train_report["corrections"] = corrections_result
    train_report["verified"] = verified_result

    indexed_ids = _indexed_email_ids(settings.qdrant_url)
    holdout_result = evaluate_holdout(limit=holdout_limit, min_score=min_score)

    factory = get_session_factory()
    with factory() as session:
        labeled_rows = collect_operator_labeled_rows(session)
        operator_result = evaluate_operator_labels(
            labeled_rows,
            split_mode=split_mode,
            test_ratio=test_ratio,
            settings=settings,
            min_score=min_score,
        )

    holdout_meets = bool(holdout_result.get("meets_target"))
    operator_meets = bool(operator_result.get("meets_target"))

    report = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "target_accuracy": TARGET_ACCURACY,
        "train": train_report,
        "indexed_email_ids_count": len(indexed_ids),
        "indexed_email_ids_sample": sorted(indexed_ids)[:20],
        "eval_holdout": holdout_result,
        "eval_operator_labels": operator_result,
        "holdout_meets_target": holdout_meets,
        "operator_meets_target": operator_meets,
        "meets_target": holdout_meets,
    }

    out_dir = PROJECT_ROOT / "data" / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "bge_train_eval_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_path"] = str(out_path)

    print_russian_summary(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Обучение BGE на полных письмах + holdout/operator eval",
    )
    parser.add_argument(
        "--no-reextract",
        action="store_true",
        help="Не перезагружать тело письма через IMAP",
    )
    parser.add_argument(
        "--force-verified",
        action="store_true",
        help="Переиндексировать уже проиндексированные verified-письма",
    )
    parser.add_argument("--holdout-limit", type=int, default=100)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument(
        "--split-mode",
        choices=("hash8020", "loo"),
        default="hash8020",
        help="hash8020: 80/20 по hash email_id; loo: leave-one-out на всех метках",
    )
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--skip-train", action="store_true", help="Только eval без индексации")
    args = parser.parse_args()

    report = run_pipeline(
        reextract=not args.no_reextract,
        force_reindex_verified=args.force_verified,
        holdout_limit=args.holdout_limit,
        split_mode=args.split_mode,
        test_ratio=args.test_ratio,
        min_score=args.min_score,
        skip_train=args.skip_train,
    )
    if not report.get("meets_target"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
