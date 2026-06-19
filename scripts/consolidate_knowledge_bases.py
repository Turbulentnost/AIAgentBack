"""Оставить одну основную БЗ и настроить полный доступ.

Пример:
  python scripts/consolidate_knowledge_bases.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEEP_KB_ID = uuid.UUID("a29b5e2f-5ea1-4994-b8bc-16f6661ba254")
ARCHIVE_KB_IDS = (
    uuid.UUID("bf788771-75b5-4f27-941e-16f4b0675e2d"),
    uuid.UUID("b1c09d6d-271b-4002-9375-c6f902295389"),
    uuid.UUID("bdc07270-9a32-4892-bd49-21564cc549bf"),
)
DEFAULT_USER_EMAIL = "temp.nd@local.dev"
DEFAULT_AGENT_SLUG = "nd_control_agent"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Консолидация баз знаний")
    parser.add_argument("--owner-email", default=DEFAULT_USER_EMAIL)
    parser.add_argument("--skip-reindex", action="store_true")
    args = parser.parse_args()

    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.agent import Agent
    from app.models.enums import (
        KnowledgeBaseAccessType,
        KnowledgeBaseAgentAccessMode,
        KnowledgeBaseGrantType,
        KnowledgeBaseIndexJobStatus,
        KnowledgeBaseIndexJobType,
        KnowledgeBaseStatus,
    )
    from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseIndexingJob
    from app.schemas.knowledge_base import (
        KnowledgeBaseAccessGrantInput,
        KnowledgeBaseAccessUpdate,
        KnowledgeBaseAgentBindingInput,
    )
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService
    from app.services.knowledge_base_service import KnowledgeBaseService
    from app.services.user_service import UserService
    from app.workers.tasks import index_knowledge_base_full

    async with AsyncSessionLocal() as session:
        owner = await UserService(session).get_by_email(args.owner_email)
        if owner is None:
            raise SystemExit(f"Пользователь не найден: {args.owner_email}")

        archived: list[str] = []
        now = datetime.now(timezone.utc)
        for kb_id in ARCHIVE_KB_IDS:
            kb = await session.get(KnowledgeBase, kb_id)
            if kb is None or kb.deleted_at is not None:
                continue
            kb.deleted_at = now
            kb.deleted_by_user_id = owner.id
            kb.status = KnowledgeBaseStatus.ARCHIVED
            archived.append(str(kb_id))

        keep = await session.get(KnowledgeBase, KEEP_KB_ID)
        if keep is None:
            print(json.dumps({"status": "error", "message": "Основная БЗ не найдена"}, ensure_ascii=False))
            return 1

        keep.deleted_at = None
        keep.deleted_by_user_id = None
        keep.is_public = True
        keep.status = KnowledgeBaseStatus.NEEDS_REVIEW
        await session.flush()

        service = KnowledgeBaseService(session)
        await service.replace_access(
            keep.id,
            KnowledgeBaseAccessUpdate(
                grants=[
                    KnowledgeBaseAccessGrantInput(
                        grantee_type=KnowledgeBaseGrantType.ORGANIZATION,
                        grantee_id=None,
                        access_type=KnowledgeBaseAccessType.SEARCH,
                    ),
                    KnowledgeBaseAccessGrantInput(
                        grantee_type=KnowledgeBaseGrantType.ORGANIZATION,
                        grantee_id=None,
                        access_type=KnowledgeBaseAccessType.USE_VIA_AGENT,
                    ),
                    KnowledgeBaseAccessGrantInput(
                        grantee_type=KnowledgeBaseGrantType.ORGANIZATION,
                        grantee_id=None,
                        access_type=KnowledgeBaseAccessType.READ,
                    ),
                    KnowledgeBaseAccessGrantInput(
                        grantee_type=KnowledgeBaseGrantType.USER,
                        grantee_id=owner.id,
                        access_type=KnowledgeBaseAccessType.ADMIN,
                    ),
                ]
            ),
            current_user=owner,
        )

        agent = await session.scalar(select(Agent).where(Agent.slug == DEFAULT_AGENT_SLUG))
        if agent is not None:
            await service.replace_agents(
                keep.id,
                [
                    KnowledgeBaseAgentBindingInput(
                        agent_id=agent.id,
                        access_mode=KnowledgeBaseAgentAccessMode.SEARCH_AND_CITE,
                        is_enabled=True,
                    )
                ],
                current_user=owner,
            )

        indexing = None
        if not args.skip_reindex:
            active = await session.scalar(
                select(KnowledgeBaseIndexingJob.id).where(
                    KnowledgeBaseIndexingJob.knowledge_base_id == keep.id,
                    KnowledgeBaseIndexingJob.status.in_(
                        [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                    ),
                )
            )
            if active is None:
                job = await KnowledgeBaseIndexingService(session).create_job(
                    keep.id,
                    job_type=KnowledgeBaseIndexJobType.FULL,
                    started_by_user_id=owner.id,
                )
                await session.flush()
                async_result = index_knowledge_base_full.delay(str(keep.id), str(job.id))
                indexing = {"job_id": str(job.id), "celery_task_id": async_result.id}
            else:
                indexing = {"job_id": str(active), "status": "already_running"}

        await session.commit()

        print(
            json.dumps(
                {
                    "status": "ok",
                    "kept_kb": {
                        "id": str(keep.id),
                        "name": keep.name,
                        "sources_count": keep.sources_count,
                        "is_public": keep.is_public,
                    },
                    "archived_kb_ids": archived,
                    "indexing": indexing,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
