"""Sync department corrections to Qdrant (BGE) collection department_corrections_bge."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import PROJECT_ROOT, get_settings  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.services.department_correction_indexing import (  # noqa: E402
    sync_department_correction_records,
)
from agent_pochta.services.department_knowledge import (  # noqa: E402
    DepartmentCorrectionRecord,
    collect_department_correction_records,
)
from agent_pochta.services.email_rag_qdrant import ensure_department_corrections_collection  # noqa: E402


def load_records_from_analysis(path: Path) -> list[DepartmentCorrectionRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [DepartmentCorrectionRecord(**item) for item in data.get("records", [])]


def sync_all(*, limit: int | None = None, from_analysis: Path | None = None) -> dict:
    settings = get_settings()
    ensure_department_corrections_collection(settings.qdrant_url)

    if from_analysis and from_analysis.is_file():
        records = load_records_from_analysis(from_analysis)
    else:
        factory = get_session_factory()
        with factory() as session:
            records = collect_department_correction_records(session)

    return sync_department_correction_records(records, limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync department corrections to Qdrant")
    parser.add_argument("--all", action="store_true", help="Sync all correction records")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--from-analysis",
        type=Path,
        default=PROJECT_ROOT / "data" / "stats" / "department_knowledge_analysis.json",
    )
    args = parser.parse_args()

    if not args.all and args.limit is None:
        args.limit = get_settings().dept_corrections_sync_batch_size

    result = sync_all(limit=args.limit, from_analysis=args.from_analysis if args.from_analysis.exists() else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
