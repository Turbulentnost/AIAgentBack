"""Backfill operator-verified emails into department_corrections_bge (dedup by email_id)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.repository import EmailRepository  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.services.bge_correction_learning import (  # noqa: E402
    is_already_bge_verified_indexed,
    iter_operator_verified_rows,
    upsert_verified_from_row,
)
from agent_pochta.services.email_rag_qdrant import ensure_department_corrections_collection  # noqa: E402


def backfill(
    *,
    reextract: bool = False,
    limit: int | None = None,
    skip_indexed: bool = True,
    force: bool = False,
) -> dict:
    settings = get_settings()
    ensure_department_corrections_collection(settings.qdrant_url)

    factory = get_session_factory()
    total_verified = 0
    indexed = 0
    skipped = 0
    failed = 0
    details: list[dict] = []

    with factory() as session:
        repo = EmailRepository(session)
        rows = list(
            iter_operator_verified_rows(
                session,
                limit=limit,
                skip_indexed=skip_indexed and not force,
            )
        )
        total_verified = len(rows)

        for row in rows:
            if force and is_already_bge_verified_indexed(row):
                pass  # re-index even if flagged

            result = upsert_verified_from_row(
                repo,
                row,
                reextract=reextract,
                settings=settings,
            )
            if result.get("ok"):
                session.commit()
                indexed += 1
            elif result.get("skipped"):
                skipped += 1
            else:
                failed += 1
            details.append(
                {
                    "email_id": str(row.id),
                    "department_id": row.department_id,
                    **result,
                }
            )

    return {
        "ok": failed == 0,
        "total_verified": total_verified,
        "indexed": indexed,
        "skipped": skipped,
        "failed": failed,
        "reextract": reextract,
        "skip_indexed": skip_indexed and not force,
        "details": details[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill operator-verified emails into BGE department_corrections_bge",
    )
    parser.add_argument(
        "--reextract",
        action="store_true",
        help="Re-fetch full body via IMAP when embedding text is short or missing",
    )
    parser.add_argument("--all", action="store_true", help="Process all verified emails")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index even if bge_verified_indexed_at is already set",
    )
    parser.add_argument(
        "--include-indexed",
        action="store_true",
        help="Include rows already marked bge_verified_indexed_at (alias for not skip_indexed)",
    )
    args = parser.parse_args()
    if not args.all and args.limit is None:
        args.limit = 50

    result = backfill(
        reextract=args.reextract,
        limit=args.limit,
        skip_indexed=not args.include_indexed,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
