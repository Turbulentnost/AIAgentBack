"""Итеративное обучение BGE по эталону 1С (документы агента после cutoff)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import PROJECT_ROOT, get_settings  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.routing.bge_department import predict_department_bge  # noqa: E402
from agent_pochta.services.bge_correction_learning import upsert_correction_from_1c_oracle  # noqa: E402
from agent_pochta.services.email_corpus_resolver import resolve_email_for_doc  # noqa: E402
from agent_pochta.services.email_rag_qdrant import (  # noqa: E402
    ensure_department_corrections_collection,
    purge_department_corrections_collection,
)
from agent_pochta.services.onec_routing_corpus import (  # noqa: E402
    doc_number,
    fetch_agent_incoming_docs,
    load_guid_maps,
    resolve_dept_from_1c_doc,
)

_ILLEGAL_XLSX = re.compile(r"[\000-\010]|\013|\014|\016-\037")


def _xlsx_safe(value: object) -> str:
    text = "" if value is None else str(value)
    cleaned: list[str] = []
    for ch in text:
        code = ord(ch)
        if code in (9, 10, 13) or 32 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD:
            cleaned.append(ch)
    return "".join(cleaned)[:32000]


def purge_index(*, fresh: bool, since: str, settings) -> dict[str, int]:
    ensure_department_corrections_collection(settings.qdrant_url)
    if fresh:
        return purge_department_corrections_collection(
            url=settings.qdrant_url,
            delete_all=True,
        )
    since_iso = f"{since}T00:00:00" if "T" not in since else since
    return purge_department_corrections_collection(
        url=settings.qdrant_url,
        before_iso=since_iso,
    )


def run_iteration(
    docs: list[dict[str, Any]],
    *,
    settings,
    session,
    code_by_guid,
    name_by_code,
    reextract: bool,
    upsert_on_miss: bool,
    min_score: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    correct = 0
    wrong = 0
    skipped = 0
    upserted = 0
    evaluated = 0
    direct = 0
    rows_out: list[dict[str, Any]] = []

    for doc in docs:
        number = doc_number(doc)
        dept_info = resolve_dept_from_1c_doc(
            doc,
            code_by_guid=code_by_guid,
            name_by_code=name_by_code,
        )
        actual_id = dept_info["department_code"]
        actual_name = dept_info["department_name"]
        if not actual_id:
            skipped += 1
            rows_out.append(
                {
                    "number": number,
                    "action": "skip_no_dept",
                    "destination_source": dept_info.get("destination_source"),
                }
            )
            continue

        resolved = resolve_email_for_doc(
            doc,
            session=session,
            settings=settings,
            reextract=reextract,
        )
        if len(resolved.embed_text.strip()) < settings.email_rag_min_chars:
            skipped += 1
            rows_out.append(
                {
                    "number": number,
                    "action": "skip_no_text",
                    "resolution_source": resolved.resolution_source,
                }
            )
            continue

        prediction = predict_department_bge(
            resolved.embed_text,
            resolved.recipient,
            settings=settings,
        )
        predicted_id = prediction.dept_id if prediction.ok else ""
        predicted_name = prediction.dept_name if prediction.ok else ""
        score = prediction.score
        is_correct = bool(predicted_id and predicted_id == actual_id)
        evaluated += 1

        action = "correct"
        upsert_result: dict[str, Any] | None = None
        if is_correct:
            correct += 1
            if score is not None and score >= min_score:
                direct += 1
        else:
            wrong += 1
            action = "wrong"
            if upsert_on_miss:
                upsert_result = upsert_correction_from_1c_oracle(
                    embed_text=resolved.embed_text,
                    recipient=resolved.recipient,
                    sender_email=resolved.sender_email,
                    subject=resolved.subject,
                    wrong_dept_id=predicted_id or None,
                    wrong_dept_name=predicted_name or "",
                    correct_dept_id=actual_id,
                    correct_dept_name=actual_name,
                    row=resolved.row,
                    message_id=resolved.message_id,
                    email_id=str(resolved.row.id) if resolved.row else None,
                    settings=settings,
                )
                if upsert_result.get("indexed"):
                    upserted += int(upsert_result.get("indexed") or 0)
                    action = "upserted"

        rows_out.append(
            {
                "number": number,
                "subject": resolved.subject,
                "recipient": resolved.recipient,
                "actual_id": actual_id,
                "actual_name": actual_name,
                "predicted_id": predicted_id,
                "predicted_name": predicted_name,
                "score": score,
                "bge_reason": prediction.reason,
                "resolution_source": resolved.resolution_source,
                "correct": is_correct,
                "action": action,
                "upsert": upsert_result,
            }
        )

    accuracy = round(correct / evaluated, 4) if evaluated else 0.0
    summary = {
        "evaluated": evaluated,
        "correct": correct,
        "wrong": wrong,
        "skipped": skipped,
        "upserted": upserted,
        "direct_routes": direct,
        "accuracy": accuracy,
        "min_score": min_score,
    }
    return summary, rows_out


def export_excel(rows: list[dict[str, Any]], path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "1C oracle train"
    headers = [
        "Номер 1С",
        "Тема",
        "Получатель",
        "Факт (1С)",
        "BGE прогноз",
        "Score",
        "Верно",
        "Действие",
        "Источник текста",
    ]
    ws.append(headers)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        ws.append(
            [
                _xlsx_safe(row.get("number")),
                _xlsx_safe(row.get("subject")),
                _xlsx_safe(row.get("recipient")),
                _xlsx_safe(f"{row.get('actual_id')} — {row.get('actual_name')}"),
                _xlsx_safe(f"{row.get('predicted_id')} — {row.get('predicted_name')}"),
                row.get("score"),
                "да" if row.get("correct") else "нет",
                _xlsx_safe(row.get("action")),
                _xlsx_safe(row.get("resolution_source")),
            ]
        )
    for col in ws.columns:
        for cell in col:
            cell.alignment = wrap
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def train_until_target(
    *,
    since: str = "2026-07-20",
    target: float = 0.90,
    max_iterations: int = 20,
    limit: int | None = None,
    min_score: float | None = None,
    fresh_index: bool = False,
    dry_run: bool = False,
    reextract: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    min_score = min_score if min_score is not None else settings.bge_dept_min_score
    code_by_guid, name_by_code = load_guid_maps(settings)

    purge_stats = purge_index(fresh=fresh_index, since=since, settings=settings)

    docs = fetch_agent_incoming_docs(since, settings=settings, limit=limit)
    print(f"Corpus: {len(docs)} docs from 1C (since {since})", flush=True)
    iterations: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    accuracy = 0.0

    factory = get_session_factory()
    with factory() as session:
        for iteration in range(1, max_iterations + 1):
            summary, rows = run_iteration(
                docs,
                settings=settings,
                session=session,
                code_by_guid=code_by_guid,
                name_by_code=name_by_code,
                reextract=reextract,
                upsert_on_miss=not dry_run,
                min_score=min_score,
            )
            summary["iteration"] = iteration
            iterations.append(summary)
            all_rows = rows
            accuracy = float(summary["accuracy"])
            print(
                f"[iter {iteration}] "
                f"accuracy={summary['accuracy']:.1%} "
                f"({summary['correct']}/{summary['evaluated']} correct, "
                f"{summary['wrong']} wrong, {summary['skipped']} skipped, "
                f"{summary['upserted']} upserted)",
                flush=True,
            )
            if accuracy >= target:
                print(f"[done] target {target:.0%} reached on iteration {iteration}", flush=True)
                break
        else:
            print(
                f"[stop] target {target:.0%} not reached after {max_iterations} iterations "
                f"(last accuracy={accuracy:.1%})",
                flush=True,
            )

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = PROJECT_ROOT / "data" / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "bge_1c_oracle_train.json"
    xlsx_path = out_dir / f"bge_1c_oracle_train_{stamp}.xlsx"

    result = {
        "trained_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "since": since,
        "target_accuracy": target,
        "meets_target": accuracy >= target,
        "accuracy": accuracy,
        "iterations_run": len(iterations),
        "corpus_size": len(docs),
        "dry_run": dry_run,
        "fresh_index": fresh_index,
        "purge": purge_stats,
        "iterations": iterations,
        "json_path": str(json_path),
        "xlsx_path": str(xlsx_path),
    }
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    export_excel(all_rows, xlsx_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Iterative BGE train vs 1C oracle")
    parser.add_argument("--since", default="2026-07-20")
    parser.add_argument("--target", type=float, default=0.90)
    parser.add_argument("--max-iterations", type=int, default=20)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--fresh-index", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reextract", action="store_true")
    args = parser.parse_args()

    result = train_until_target(
        since=args.since,
        target=args.target,
        max_iterations=args.max_iterations,
        limit=args.limit,
        min_score=args.min_score,
        fresh_index=args.fresh_index,
        dry_run=args.dry_run,
        reextract=args.reextract,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("meets_target"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
