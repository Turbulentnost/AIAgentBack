"""Создание базы знаний из импортированных документов (папка «Документы по сварке»).

Пример:
  python scripts/create_knowledge_base_from_welding_documents.py --import --queue-index
  python scripts/create_knowledge_base_from_welding_documents.py --dry-run
  python scripts/create_knowledge_base_from_welding_documents.py --index
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

DEFAULT_FOLDER = (
    r"\\192.168.1.198\Files\10.СКТБ\НОРМАТИВНЫЕ ДОКУМЕНТЫ ОРГАНИЗАЦИИ\НОРМАТИВНЫЕ ДОКУМЕНТЫ\Документы по сварке"
)
DEFAULT_FOLDER_MARKER = "Документы по сварке"
DEFAULT_KB_NAME = "Документы по сварке"
DEFAULT_KB_TOPIC = "нормативная документация по сварке"
DEFAULT_PROCESS_SLUG = "welding_documents"
DEFAULT_USER_EMAIL = "temp.nd@local.dev"
DEFAULT_AGENT_SLUG = "nd_control_agent"


async def _load_owner_user(session, email: str):
    from app.services.user_service import UserService

    user = await UserService(session).get_by_email(email)
    if user is None:
        raise SystemExit(f"Пользователь не найден: {email}. Сначала выполните scripts/create_temp_user.py")
    return user


async def _find_document_ids(session, folder_marker: str) -> list[uuid.UUID]:
    from sqlalchemy import or_, select
    from sqlalchemy.exc import ProgrammingError

    from app.models.document import Document

    ids: set[uuid.UUID] = set()

    try:
        from app.models.document_card import QmsDocumentCard

        card_stmt = select(QmsDocumentCard.document_id).where(
            or_(
                QmsDocumentCard.original_storage_location.ilike(f"%{folder_marker}%"),
                QmsDocumentCard.electronic_storage_location.ilike(f"%{folder_marker}%"),
            )
        )
        ids.update(row[0] for row in (await session.execute(card_stmt)).all())
    except ProgrammingError:
        await session.rollback()

    doc_stmt = select(Document.id).where(
        or_(
            Document.metadata_["import_folder_root"].as_string().ilike(f"%{folder_marker}%"),
            Document.metadata_["original_storage_location"].as_string().ilike(f"%{folder_marker}%"),
        )
    )
    ids.update(row[0] for row in (await session.execute(doc_stmt)).all())

    return sorted(ids, key=str)


async def _import_folder(session, folder_path: str, owner) -> dict:
    from app.services.document_folder_import_service import DocumentFolderImportService

    service = DocumentFolderImportService(session)
    result = await service.import_folder(
        folder_path,
        uploaded_by_user_id=owner.id,
        is_knowledge_base=True,
    )
    return result.model_dump()


async def _get_or_create_kb(
    session,
    *,
    owner_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    name: str,
    topic: str,
    process_slug: str,
):
    from sqlalchemy import select

    from app.models.enums import KnowledgeBaseAccessType, KnowledgeBaseGrantType, KnowledgeBaseStatus
    from app.models.knowledge_base import KnowledgeBase
    from app.schemas.knowledge_base import (
        KnowledgeBaseAccessGrantInput,
        KnowledgeBaseAgentBindingInput,
        KnowledgeBaseCreate,
    )
    from app.services.knowledge_base_service import KnowledgeBaseService
    from app.services.user_service import UserService

    owner = await UserService(session).get(owner_id)
    if owner is None:
        raise SystemExit(f"Пользователь не найден: {owner_id}")

    existing = await session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.name == name,
            KnowledgeBase.deleted_at.is_(None),
        )
    )
    service = KnowledgeBaseService(session)
    if existing is None:
        kb = await service.create(
            KnowledgeBaseCreate(
                name=name,
                description="База знаний по нормативным документам организации из папки «Документы по сварке».",
                topic=topic,
                process_slug=process_slug,
                metadata={"source_folder_marker": DEFAULT_FOLDER_MARKER},
                access_grants=[
                    KnowledgeBaseAccessGrantInput(
                        grantee_type=KnowledgeBaseGrantType.ORGANIZATION,
                        grantee_id=None,
                        access_type=KnowledgeBaseAccessType.SEARCH,
                    ),
                    KnowledgeBaseAccessGrantInput(
                        grantee_type=KnowledgeBaseGrantType.USER,
                        grantee_id=owner_id,
                        access_type=KnowledgeBaseAccessType.ADMIN,
                    ),
                ],
                source_document_ids=document_ids,
            ),
            current_user=owner,
        )
        created = True
    else:
        kb = existing
        created = False
        for document_id in document_ids:
            from app.schemas.knowledge_base import KnowledgeBaseSourceCreate

            await service.add_source(
                kb.id,
                KnowledgeBaseSourceCreate(document_id=document_id),
                current_user=owner,
            )

    from app.models.agent import Agent

    agent = await session.scalar(select(Agent).where(Agent.slug == DEFAULT_AGENT_SLUG))
    if agent is not None:
        bindings = await service.list_agents(kb.id)
        if not any(item.agent_id == agent.id for item in bindings):
            await service.replace_agents(
                kb.id,
                [
                    *[
                        KnowledgeBaseAgentBindingInput(
                            agent_id=item.agent_id,
                            access_mode=item.access_mode,
                            expires_at=item.expires_at,
                            is_enabled=item.is_enabled,
                        )
                        for item in bindings
                    ],
                    KnowledgeBaseAgentBindingInput(agent_id=agent.id),
                ],
                current_user=owner,
            )

    if kb.status == KnowledgeBaseStatus.DRAFT and not created:
        kb.status = KnowledgeBaseStatus.NEEDS_REVIEW
    return kb, created


async def _index_kb(session, kb_id: uuid.UUID, owner) -> dict:
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService

    service = KnowledgeBaseIndexingService(session)
    return await service.index_knowledge_base(kb_id, started_by_user_id=owner.id, user=owner)


async def _queue_index(kb_id: uuid.UUID, owner_id: uuid.UUID) -> dict:
    from app.models.enums import KnowledgeBaseIndexJobType
    from app.db.session import AsyncSessionLocal
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService
    from app.workers.tasks import index_knowledge_base_full

    async with AsyncSessionLocal() as session:
        job = await KnowledgeBaseIndexingService(session).create_job(
            kb_id,
            job_type=KnowledgeBaseIndexJobType.FULL,
            started_by_user_id=owner_id,
        )
        await session.commit()
        async_result = index_knowledge_base_full.delay(str(kb_id), str(job.id))
        return {"job_id": str(job.id), "celery_task_id": async_result.id}


async def main() -> int:
    parser = argparse.ArgumentParser(description="Создать базу знаний из документов по сварке")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Путь к папке с PDF")
    parser.add_argument("--folder-marker", default=DEFAULT_FOLDER_MARKER)
    parser.add_argument("--kb-name", default=DEFAULT_KB_NAME)
    parser.add_argument("--owner-email", default=DEFAULT_USER_EMAIL)
    parser.add_argument("--import", dest="do_import", action="store_true", help="Импортировать файлы из папки в documents")
    parser.add_argument("--index", action="store_true", help="Сразу запустить полную индексацию локально (тяжёлая операция)")
    parser.add_argument("--queue-index", action="store_true", help="Поставить индексацию в Celery на сервере")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет создано")
    args = parser.parse_args()

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        owner = await _load_owner_user(session, args.owner_email)
        owner_id = owner.id

        if args.do_import and not args.dry_run:
            import_result = await _import_folder(session, args.folder, owner)
            await session.commit()
            owner = await _load_owner_user(session, args.owner_email)
        else:
            import_result = None

        document_ids = await _find_document_ids(session, args.folder_marker)
        if not document_ids:
            print(
                json.dumps(
                    {
                        "status": "empty",
                        "message": "Документы не найдены. Запустите с флагом --import",
                        "folder": args.folder,
                        "folder_marker": args.folder_marker,
                        "import_result": import_result,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "dry_run",
                        "kb_name": args.kb_name,
                        "documents_count": len(document_ids),
                        "document_ids": [str(item) for item in document_ids],
                        "import_result": import_result,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        kb, created = await _get_or_create_kb(
            session,
            owner_id=owner_id,
            document_ids=document_ids,
            name=args.kb_name,
            topic=DEFAULT_KB_TOPIC,
            process_slug=DEFAULT_PROCESS_SLUG,
        )
        await session.commit()

        result = {
            "status": "created" if created else "updated",
            "knowledge_base_id": str(kb.id),
            "knowledge_base_name": kb.name,
            "qdrant_collection": kb.qdrant_collection,
            "sources_count": kb.sources_count,
            "documents_linked": len(document_ids),
            "import_result": import_result,
        }

        if args.index:
            index_result = await _index_kb(session, kb.id, owner)
            await session.commit()
            result["indexing"] = index_result
        elif args.queue_index:
            result["indexing"] = await _queue_index(kb.id, owner_id)

        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
