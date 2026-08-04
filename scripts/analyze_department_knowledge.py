"""Анализ базы знаний по коррекциям отделов (Postgres + routing_corrections.json)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.services.department_knowledge import (  # noqa: E402
    collect_department_correction_records,
    write_analysis_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze department knowledge base")
    parser.add_argument("--postgres-only", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    if args.json_only:
        from agent_pochta.services.department_knowledge import (
            _records_from_routing_corrections,
            load_department_display_names,
        )

        records = _records_from_routing_corrections(load_department_display_names())
    elif args.postgres_only:
        from agent_pochta.services.department_knowledge import (
            _records_from_postgres,
            load_department_display_names,
        )

        factory = get_session_factory()
        with factory() as session:
            records = _records_from_postgres(session, load_department_display_names())
    else:
        factory = get_session_factory()
        with factory() as session:
            records = collect_department_correction_records(session)

    json_path, csv_path = write_analysis_outputs(records)
    print(f"records={len(records)}")
    print(f"json={json_path}")
    print(f"csv={csv_path}")


if __name__ == "__main__":
    main()
