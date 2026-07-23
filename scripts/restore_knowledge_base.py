"""Разархивировать базу знаний и поставить полную индексацию в очередь.

Пример:
  python scripts/restore_knowledge_base.py
  python scripts/restore_knowledge_base.py --process-slug org_normative_by_department --kb-name "Нормативные документы по подразделениям"
  python scripts/restore_knowledge_base.py --all-normative
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PROCESS_SLUG = "org_normative_documents"
DEFAULT_KB_NAME = "Нормативные документы организации"
DEFAULT_USER_EMAIL = "temp.nd@local.dev"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Разархивировать БЗ и запустить индексацию")
    parser.add_argument("--kb-id", type=uuid.UUID, default=None)
    parser.add_argument("--process-slug", default=DEFAULT_PROCESS_SLUG)
    parser.add_argument("--kb-name", default=DEFAULT_KB_NAME)
    parser.add_argument("--owner-email", default=DEFAULT_USER_EMAIL)
    args = parser.parse_args()

    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.enums import KnowledgeBaseIndexJobType, KnowledgeBaseStatus
    from app.models.knowledge_base import KnowledgeBase
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService
    from app.services.user_service import UserService
    from app.workers.tasks import index_knowledge_base_full

    async with AsyncSessionLocal() as session:
        if args.kb_id:
            kb = await session.get(KnowledgeBase, args.kb_id)
        else:
            kb = await session.scalar(
                select(KnowledgeBase)
                .where(
                    (KnowledgeBase.process_slug == args.process_slug)
                    | (KnowledgeBase.name == args.kb_name)
                )
                .order_by(KnowledgeBase.created_at.asc())
            )
        if kb is None:
            print(json.dumps({"status": "empty", "message": "База знаний не найдена"}, ensure_ascii=False))
            return 1

        owner = await UserService(session).get_by_email(args.owner_email)
        if owner is None:
            raise SystemExit(f"Пользователь не найден: {args.owner_email}")

        kb.deleted_at = None
        kb.deleted_by_user_id = None
        if kb.status == KnowledgeBaseStatus.ARCHIVED:
            kb.status = KnowledgeBaseStatus.NEEDS_REVIEW

        job = await KnowledgeBaseIndexingService(session).create_job(
            kb.id,
            job_type=KnowledgeBaseIndexJobType.FULL,
            started_by_user_id=owner.id,
        )
        await session.commit()

        async_result = index_knowledge_base_full.delay(str(kb.id), str(job.id))
        print(
            json.dumps(
                {
                    "status": "restored",
                    "knowledge_base_id": str(kb.id),
                    "knowledge_base_name": kb.name,
                    "sources_count": kb.sources_count,
                    "indexing": {
                        "job_id": str(job.id),
                        "celery_task_id": async_result.id,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
