"""Итеративное обучение BGE: промах → upsert в Qdrant → повторная оценка (цель 90%)."""

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
from agent_pochta.db.repository import EmailRepository  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.services.bge_train_eval import (  # noqa: E402
    TARGET_ACCURACY,
    collect_operator_labeled_rows,
    evaluate_operator_labels,
    run_iterative_operator_training,
)
from agent_pochta.services.email_rag_qdrant import ensure_department_corrections_collection  # noqa: E402


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def run(
    *,
    target: float = TARGET_ACCURACY,
    max_iterations: int = 20,
    reextract: bool = False,
    holdout_limit: int = 100,
    skip_initial_backfill: bool = False,
) -> dict:
    settings = get_settings()
    ensure_department_corrections_collection(settings.qdrant_url)
    started_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    initial_train: dict = {"reextract": reextract}
    if skip_initial_backfill:
        initial_train["skipped"] = True
    else:
        initial_train["corrections"] = backfill_corrections(reextract=reextract, limit=None)
        initial_train["verified"] = backfill_verified(
            reextract=reextract,
            limit=None,
            skip_indexed=False,
            force=True,
        )

    factory = get_session_factory()
    with factory() as session:
        repo = EmailRepository(session)
        labeled_rows = collect_operator_labeled_rows(session)
        print(f"Размечено оператором: {len(labeled_rows)} писем", flush=True)

        def holdout_fn() -> dict:
            return evaluate_holdout(limit=holdout_limit)

        iterative = run_iterative_operator_training(
            session,
            labeled_rows,
            repo=repo,
            settings=settings,
            target=target,
            max_iterations=max_iterations,
            reextract=reextract,
            holdout_eval_fn=holdout_fn,
        )

        for step in iterative.get("iterations") or []:
            print(
                f"[iter {step['iteration']}] "
                f"operator={_pct(step['operator_accuracy'])} "
                f"({step['operator_correct']}/{step['operator_evaluated']}), "
                f"holdout={_pct(step.get('holdout_accuracy'))}, "
                f"upserted={step['upserted']}",
                flush=True,
            )

        operator_eval = evaluate_operator_labels(
            labeled_rows,
            split_mode="hash8020",
            settings=settings,
        )
        holdout_final = evaluate_holdout(limit=holdout_limit)
        indexed_count = len(_indexed_email_ids(settings.qdrant_url))

    finished_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    report = {
        "started_at": started_at,
        "finished_at": finished_at,
        "target_accuracy": target,
        "initial_train": initial_train,
        "iterative": iterative,
        "indexed_email_ids_count": indexed_count,
        "eval_operator_labels_holdout": operator_eval,
        "eval_holdout_final": holdout_final,
        "operator_meets_target": bool(iterative.get("meets_target")),
        "holdout_meets_target": bool(holdout_final.get("meets_target")),
        "meets_target": bool(iterative.get("meets_target")),
    }

    out_dir = PROJECT_ROOT / "data" / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "bge_iterative_train_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_path"] = str(out_path)

    print()
    print("=" * 72)
    print("ИТОГ ИТЕРАТИВНОГО ОБУЧЕНИЯ BGE")
    print("=" * 72)
    print(f"  Итераций: {iterative.get('iterations_run')}")
    print(
        f"  Operator (in-sample): {_pct(iterative.get('accuracy'))} "
        f"(цель {int(target * 100)}%)"
    )
    print(
        f"  Operator holdout 80/20: {_pct(operator_eval.get('accuracy'))} "
        f"({operator_eval.get('correct')}/{operator_eval.get('evaluated')})"
    )
    print(
        f"  Holdout ERP: {_pct(holdout_final.get('accuracy'))} "
        f"({holdout_final.get('correct')}/{holdout_final.get('holdout_size')})"
    )
    print(f"  Точек в индексе: {indexed_count}")
    print(f"  Отчёт: {out_path}")
    print("=" * 72)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Итеративное обучение BGE до целевой точности")
    parser.add_argument("--target", type=float, default=TARGET_ACCURACY)
    parser.add_argument("--max-iterations", type=int, default=20)
    parser.add_argument("--reextract", action="store_true", help="IMAP reextract (тяжело для памяти)")
    parser.add_argument("--holdout-limit", type=int, default=100)
    parser.add_argument("--skip-initial-backfill", action="store_true")
    args = parser.parse_args()

    report = run(
        target=args.target,
        max_iterations=args.max_iterations,
        reextract=args.reextract,
        holdout_limit=args.holdout_limit,
        skip_initial_backfill=args.skip_initial_backfill,
    )
    if not report.get("meets_target"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
