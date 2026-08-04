"""LLM-аудит routing_corrections: match писем, извлечение keywords, обновление JSON."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import PROJECT_ROOT, get_settings
from agent_pochta.db.session import get_session_factory
from agent_pochta.routing.corrections import load_corrections, save_corrections
from agent_pochta.routing.corrections_audit import (
    CorrectionMatch,
    MatchStatus,
    match_all_corrections,
)
from agent_pochta.routing.corrections_llm import extract_correction_keywords_llm
from agent_pochta.routing.engine import reset_route_engine

STATS_DIR = PROJECT_ROOT / "data" / "stats"
VALIDATION_CSV = STATS_DIR / "rag_validation_sample.csv"


def _is_mismatch_row(row: dict) -> bool:
    val = str(
        row.get("совпадение")
        or row.get("match")
        or ""
    ).lower().strip()
    return val in {"no", "нет", "n"}


def _load_mismatch_corr_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    ids: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if _is_mismatch_row(row):
                cid = str(row.get("corr_id") or "").strip()
                if cid:
                    ids.add(cid)
    return ids


def _load_mismatch_departments(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    depts: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if _is_mismatch_row(row):
                name = str(
                    row.get("отдел_по_коррекции")
                    or row.get("expected_department")
                    or ""
                ).strip()
                if name:
                    depts.add(name)
    return depts


def _write_match_report(matches: list[CorrectionMatch], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "corr_id",
                "status",
                "email_id",
                "sender_email",
                "recipient",
                "subject",
                "department_name",
                "body_source",
                "candidate_count",
            ],
        )
        writer.writeheader()
        for m in matches:
            writer.writerow(
                {
                    "corr_id": m.corr_id,
                    "status": m.status.value,
                    "email_id": m.email_id or "",
                    "sender_email": m.sender_email,
                    "recipient": m.recipient or "",
                    "subject": m.subject,
                    "department_name": m.department_name,
                    "body_source": m.body_source.value,
                    "candidate_count": m.candidate_count,
                }
            )


def _write_diff_report(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "corr_id",
                "department_name",
                "keyword_source",
                "old_keywords",
                "new_keywords",
                "body_source",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _backup_corrections(path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak.{ts}")
    shutil.copy2(path, backup)
    return backup


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    corrections_path = Path(settings.routing_corrections_path or PROJECT_ROOT / "data" / "routing_corrections.json")

    Session = get_session_factory()
    with Session() as session:
        all_matches = match_all_corrections(
            session,
            path=str(corrections_path),
            window_days=args.window_days,
            api_base=args.api_base,
            fetch_imap=not args.no_imap,
        )

    _write_match_report(all_matches, STATS_DIR / "routing_corrections_match_report.csv")

    print(f"Всего записей: {len(all_matches)}")
    print(f"Однозначных: {len([m for m in all_matches if m.status == MatchStatus.UNAMBIGUOUS])}")
    print(f"Неоднозначных: {len([m for m in all_matches if m.status == MatchStatus.AMBIGUOUS])}")
    print(f"Не найдено: {len([m for m in all_matches if m.status == MatchStatus.NOT_FOUND])}")
    print(f"Пропущено (нет темы): {len([m for m in all_matches if m.status == MatchStatus.SKIPPED])}")
    print(f"Match report: {STATS_DIR / 'routing_corrections_match_report.csv'}")

    if args.match_only:
        return 0

    unambiguous = [m for m in all_matches if m.status == MatchStatus.UNAMBIGUOUS]

    if args.only_mismatch:
        mismatch_ids = _load_mismatch_corr_ids(VALIDATION_CSV)
        mismatch_depts = _load_mismatch_departments(VALIDATION_CSV)
        if mismatch_ids:
            unambiguous = [m for m in unambiguous if m.corr_id in mismatch_ids]
            print(f"--only-mismatch: {len(unambiguous)} по corr_id из {VALIDATION_CSV.name}")
        elif mismatch_depts:
            unambiguous = [m for m in unambiguous if m.department_name in mismatch_depts]
            print(f"--only-mismatch: {len(unambiguous)} по отделам {sorted(mismatch_depts)}")
        else:
            print(f"WARN: {VALIDATION_CSV} не найден или без mismatch — обрабатываем все")

    if args.departments:
        allowed = {d.strip() for d in args.departments.split(",") if d.strip()}
        unambiguous = [m for m in unambiguous if m.department_name in allowed]
        print(f"--departments filter: {len(unambiguous)}")

    if args.limit > 0:
        unambiguous = unambiguous[: args.limit]

    print(f"Обрабатываем (LLM): {len(unambiguous)}")

    store = load_corrections(corrections_path)
    entries_by_id = {str(e.get("id")): e for e in store.get("entries") or []}
    corpus = list(store.get("entries") or [])

    diff_rows: list[dict] = []
    enriched_path = STATS_DIR / "routing_corrections_enriched.json"
    enriched_records: list[dict] = []

    for match in unambiguous:
        old_keywords = list(match.keywords)
        new_keywords, source = extract_correction_keywords_llm(
            match.subject,
            match.body,
            sender_email=match.sender_email,
            recipient=match.recipient,
            department_id=match.department_id,
            department_name=match.department_name,
            original_department_id=match.original_department_id,
            original_department_name=match.original_department_name,
            current_keywords=old_keywords,
            corpus_entries=corpus,
        )
        entry = entries_by_id.get(match.corr_id)
        if entry is None:
            continue

        diff_rows.append(
            {
                "corr_id": match.corr_id,
                "department_name": match.department_name,
                "keyword_source": source,
                "old_keywords": json.dumps(old_keywords, ensure_ascii=False),
                "new_keywords": json.dumps(new_keywords, ensure_ascii=False),
                "body_source": match.body_source.value,
            }
        )
        enriched_records.append(
            {
                "corr_id": match.corr_id,
                "email_id": match.email_id,
                "subject": match.subject,
                "sender_email": match.sender_email,
                "department_name": match.department_name,
                "keywords": new_keywords,
                "keyword_source": source,
                "body_source": match.body_source.value,
            }
        )

        if args.apply and not args.dry_run:
            entry["keywords"] = new_keywords

        print(
            f"  [{match.corr_id[:8]}] {match.department_name} "
            f"({source}, {match.body_source.value}) "
            f"kw={len(new_keywords)}",
            flush=True,
        )

    _write_diff_report(diff_rows, STATS_DIR / "routing_corrections_keywords_diff.csv")
    enriched_path.write_text(
        json.dumps(enriched_records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.apply and not args.dry_run:
        backup = _backup_corrections(corrections_path)
        save_corrections(store, corrections_path)
        reset_route_engine()
        print(f"Backup: {backup}")
        print(f"Updated: {corrections_path}")
    elif args.dry_run:
        print("Dry-run: JSON не изменён")

    print(f"Match report: {STATS_DIR / 'routing_corrections_match_report.csv'}")
    print(f"Diff report: {STATS_DIR / 'routing_corrections_keywords_diff.csv'}")
    print(f"Enriched: {enriched_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-аудит routing_corrections keywords")
    parser.add_argument("--apply", action="store_true", help="Записать keywords в JSON")
    parser.add_argument("--dry-run", action="store_true", help="Только отчёты, без записи JSON")
    parser.add_argument(
        "--match-only",
        action="store_true",
        help="Только сопоставление с БД (без LLM, быстро)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Лимит однозначных (0=все)")
    parser.add_argument("--window-days", type=int, default=7, help="Окно match по дате")
    parser.add_argument("--api-base", default="http://127.0.0.1:8080", help="API для fetch-body")
    parser.add_argument("--no-imap", action="store_true", help="Не вызывать fetch-body API")
    parser.add_argument(
        "--only-mismatch",
        action="store_true",
        help="Повтор только для corr_id/отделов с match=no из rag_validation_sample.csv",
    )
    parser.add_argument(
        "--departments",
        default="",
        help="CSV имён отделов для selective re-run (фаза 5)",
    )
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
