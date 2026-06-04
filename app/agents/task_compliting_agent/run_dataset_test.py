"""
Проверка агента на записях из tasks_llm_dataset_correct_simplified_result.json.

Примеры:
  python -m app.agents.task_compliting_agent.run_dataset_test --ids task_00001
  python -m app.agents.task_compliting_agent.run_dataset_test --limit 3
  python -m app.agents.task_compliting_agent.run_dataset_test --ids task_00001 task_00003 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.agents.task_compliting_agent.agent_settings import get_agent_settings
from app.agents.task_compliting_agent.dataset import get_records, record_to_agent_payload
from app.agents.task_compliting_agent.schemas import TaskCompletingInput
from app.agents.task_compliting_agent.service import TaskCompletingAgent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Тест task_compliting_agent на JSON-датасете")
    parser.add_argument(
        "--ids",
        nargs="*",
        help="ID записей (например task_00001). Без флага — первые записи по --limit",
    )
    parser.add_argument("--limit", type=int, default=1, help="Сколько записей обработать (по умолчанию 1)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать вход для LLM, без вызова модели",
    )
    return parser.parse_args()


async def _run() -> int:
    get_agent_settings.cache_clear()
    args = _parse_args()
    records = get_records(record_ids=args.ids or None, limit=None if args.ids else args.limit)
    if not records:
        print("Записи не найдены. Проверьте --ids или путь к JSON.", file=sys.stderr)
        return 1

    agent = TaskCompletingAgent()
    for record in records:
        payload = record_to_agent_payload(record)
        normalized = TaskCompletingInput(**payload)
        print("\n" + "=" * 80)
        print(f"id: {record.get('id')}")
        print(f"task_name: {normalized.task_name}")
        print(f"comment (execution_result.raw): {normalized.comment_text!r}")

        if args.dry_run:
            continue

        result = await agent.run(payload)
        print("agent status:", result.status)
        print("requires_human_review:", result.requires_human_review)
        print("summary:", result.summary)
        if result.findings:
            print("finding status:", result.findings[0].source)

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
