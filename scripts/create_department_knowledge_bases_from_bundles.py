"""Создание баз знаний по комплектам нормативных документов подразделений.

Пример:
  python scripts/create_department_knowledge_bases_from_bundles.py --dry-run
  python scripts/create_department_knowledge_bases_from_bundles.py --queue-index
  python scripts/create_department_knowledge_bases_from_bundles.py --reindex-only --queue-index
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REPORT = ROOT / "reports" / "department_normative_bundles.json"
DEFAULT_USER_EMAIL = "temp.nd@local.dev"
DEFAULT_AGENT_SLUG = "nd_control_agent"
PROCESS_SLUG_PREFIX = "dept_normative_"


def _slugify(value: str) -> str:
    normalized = value.lower().strip()
    slug = re.sub(r"[^a-zа-я0-9]+", "-", normalized, flags=re.IGNORECASE).strip("-")
    return slug[:80] or "unknown"


def _bundle_department_name(bundle: dict) -> str:
    return (
        bundle.get("enterprise_department_name")
        or bundle.get("folder_department")
        or bundle.get("department_key", "unknown")
    )


def _bundle_process_slug(bundle: dict) -> str:
    enterprise_id = bundle.get("enterprise_department_id")
    if enterprise_id:
        compact = str(enterprise_id).replace("-", "")
        return f"{PROCESS_SLUG_PREFIX}{compact}"[:128]
    folder = bundle.get("folder_department") or "unknown"
    suffix = _slugify(folder)
    return f"{PROCESS_SLUG_PREFIX}unmatched_{suffix}"[:128]


def _kb_name(bundle: dict) -> str:
    return f"НД подразделения: {_bundle_department_name(bundle)}"


def _kb_description(bundle: dict) -> str:
    name = _bundle_department_name(bundle)
    path = bundle.get("enterprise_department_path")
    if path:
        return f"Нормативные документы подразделения «{name}» ({path})."
    return f"Нормативные документы подразделения «{name}» (папка без привязки к 1С)."


def _kb_topic(bundle: dict) -> str:
    return f"нормативная документация подразделения {_bundle_department_name(bundle)}"


def _bundle_document_ids(bundle: dict) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for doc in bundle.get("documents", []):
        raw = doc.get("document_id")
        if raw:
            ids.append(uuid.UUID(str(raw)))
    return sorted(ids, key=str)


async def _load_owner_user(session, email: str):
    from app.services.user_service import UserService

    user = await UserService(session).get_by_email(email)
    if user is None:
        raise SystemExit(f"Пользователь не найден: {email}. Сначала выполните scripts/create_temp_user.py")
    return user


def _load_bundles_from_report(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("bundles", [])


async def _load_bundles_from_service(session, *, folder_marker: str) -> list[dict]:
    from app.services.department_normative_bundle_service import DepartmentNormativeBundleService

    report = await DepartmentNormativeBundleService(session).build_report(
        persist_cards=False,
        folder_marker=folder_marker,
    )
    return [bundle.model_dump(mode="json") for bundle in report.bundles]


async def _ensure_kb_access_and_agent(session, kb, *, owner) -> None:
    from sqlalchemy import select

    from app.models.agent import Agent
    from app.models.enums import (
        KnowledgeBaseAccessType,
        KnowledgeBaseAgentAccessMode,
        KnowledgeBaseGrantType,
        KnowledgeBaseStatus,
    )
    from app.schemas.knowledge_base import (
        KnowledgeBaseAccessGrantInput,
        KnowledgeBaseAccessUpdate,
        KnowledgeBaseAgentBindingInput,
    )
    from app.services.knowledge_base_service import KnowledgeBaseService

    kb.is_public = True
    if kb.status == KnowledgeBaseStatus.DRAFT:
        kb.status = KnowledgeBaseStatus.NEEDS_REVIEW

    service = KnowledgeBaseService(session)
    await service.replace_access(
        kb.id,
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
            kb.id,
            [
                KnowledgeBaseAgentBindingInput(
                    agent_id=agent.id,
                    access_mode=KnowledgeBaseAgentAccessMode.SEARCH_AND_CITE,
                    is_enabled=True,
                )
            ],
            current_user=owner,
        )


async def _get_or_create_kb(
    session,
    *,
    owner,
    bundle: dict,
    document_ids: list[uuid.UUID],
) -> tuple[object, bool, int]:
    from sqlalchemy import select

    from app.models.enums import KnowledgeBaseGrantType, KnowledgeBaseAccessType
    from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseSource
    from app.schemas.knowledge_base import (
        KnowledgeBaseAccessGrantInput,
        KnowledgeBaseCreate,
        KnowledgeBaseSourceCreate,
    )
    from app.services.knowledge_base_service import KnowledgeBaseService

    process_slug = _bundle_process_slug(bundle)
    name = _kb_name(bundle)

    existing = await session.scalar(
        select(KnowledgeBase)
        .where(
            KnowledgeBase.deleted_at.is_(None),
            (KnowledgeBase.process_slug == process_slug) | (KnowledgeBase.name == name),
        )
        .order_by(KnowledgeBase.created_at.asc())
    )

    service = KnowledgeBaseService(session)
    metadata = {
        "department_key": bundle.get("department_key"),
        "enterprise_department_id": bundle.get("enterprise_department_id"),
        "enterprise_department_name": bundle.get("enterprise_department_name"),
        "folder_department": bundle.get("folder_department"),
        "bundle_source": "department_normative_bundles",
    }

    if existing is None:
        kb = await service.create(
            KnowledgeBaseCreate(
                name=name,
                description=_kb_description(bundle),
                topic=_kb_topic(bundle),
                process_slug=process_slug,
                metadata=metadata,
                access_grants=[
                    KnowledgeBaseAccessGrantInput(
                        grantee_type=KnowledgeBaseGrantType.ORGANIZATION,
                        grantee_id=None,
                        access_type=KnowledgeBaseAccessType.SEARCH,
                    ),
                    KnowledgeBaseAccessGrantInput(
                        grantee_type=KnowledgeBaseGrantType.USER,
                        grantee_id=owner.id,
                        access_type=KnowledgeBaseAccessType.ADMIN,
                    ),
                ],
                source_document_ids=document_ids,
            ),
            current_user=owner,
        )
        await _ensure_kb_access_and_agent(session, kb, owner=owner)
        return kb, True, len(document_ids)

    kb = existing
    kb.metadata_ = {**(kb.metadata_ or {}), **metadata}
    added = 0
    existing_doc_ids = set(
        (
            await session.execute(
                select(KnowledgeBaseSource.document_id).where(KnowledgeBaseSource.knowledge_base_id == kb.id)
            )
        )
        .scalars()
        .all()
    )
    for document_id in document_ids:
        if document_id in existing_doc_ids:
            continue
        await service.add_source(
            kb.id,
            KnowledgeBaseSourceCreate(document_id=document_id),
            current_user=owner,
        )
        added += 1

    await _ensure_kb_access_and_agent(session, kb, owner=owner)
    return kb, False, added


async def _queue_index(kb_id: uuid.UUID, owner_id: uuid.UUID) -> dict:
    from app.db.session import AsyncSessionLocal
    from app.models.enums import KnowledgeBaseIndexJobStatus, KnowledgeBaseIndexJobType
    from app.models.knowledge_base import KnowledgeBaseIndexingJob
    from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService
    from app.workers.tasks import index_knowledge_base_full
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        active = await session.scalar(
            select(KnowledgeBaseIndexingJob.id).where(
                KnowledgeBaseIndexingJob.knowledge_base_id == kb_id,
                KnowledgeBaseIndexingJob.status.in_(
                    [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                ),
            )
        )
        if active is not None:
            return {"job_id": str(active), "status": "already_running"}

        job = await KnowledgeBaseIndexingService(session).create_job(
            kb_id,
            job_type=KnowledgeBaseIndexJobType.FULL,
            started_by_user_id=owner_id,
        )
        await session.commit()
        async_result = index_knowledge_base_full.delay(str(kb_id), str(job.id))
        return {"job_id": str(job.id), "celery_task_id": async_result.id}


async def _find_department_kbs(session) -> list:
    from sqlalchemy import select

    from app.models.knowledge_base import KnowledgeBase

    rows = (
        await session.execute(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.deleted_at.is_(None),
                KnowledgeBase.process_slug.like(f"{PROCESS_SLUG_PREFIX}%"),
            )
            .order_by(KnowledgeBase.name.asc())
        )
    ).scalars().all()
    return list(rows)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Создание БЗ по комплектам НД подразделений")
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="JSON-отчёт department_normative_bundles (по умолчанию reports/department_normative_bundles.json)",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Пересобрать комплекты из БД вместо JSON-отчёта",
    )
    parser.add_argument(
        "--folder-marker",
        default="НОРМАТИВНЫЕ ДОКУМЕНТЫ ОРГАНИЗАЦИИ",
        help="Маркер корневой папки (только с --from-db)",
    )
    parser.add_argument("--owner-email", default=DEFAULT_USER_EMAIL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reindex-only",
        action="store_true",
        help="Не создавать БЗ — только поставить индексацию существующих dept_normative_*",
    )
    parser.add_argument("--queue-index", action="store_true", help="Поставить полную индексацию в Celery")
    parser.add_argument(
        "--department-key",
        default=None,
        help="Обработать только один комплект (department_key из отчёта)",
    )
    args = parser.parse_args()

    import app.models.document_card  # noqa: F401 — регистрация QmsDocumentCard для relationship Document

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        owner = await _load_owner_user(session, args.owner_email)
        owner_id = owner.id

        if args.reindex_only:
            kbs = await _find_department_kbs(session)
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "status": "dry_run",
                            "mode": "reindex_only",
                            "knowledge_bases_count": len(kbs),
                            "knowledge_bases": [
                                {
                                    "id": str(kb.id),
                                    "name": kb.name,
                                    "process_slug": kb.process_slug,
                                    "sources_count": kb.sources_count,
                                }
                                for kb in kbs
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0

            results = []
            for kb in kbs:
                item = {
                    "knowledge_base_id": str(kb.id),
                    "name": kb.name,
                    "process_slug": kb.process_slug,
                    "sources_count": kb.sources_count,
                }
                if args.queue_index:
                    item["indexing"] = await _queue_index(kb.id, owner_id)
                results.append(item)

            print(
                json.dumps(
                    {
                        "status": "reindex",
                        "knowledge_bases_count": len(results),
                        "knowledge_bases": results,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0

        if args.from_db:
            bundles = await _load_bundles_from_service(session, folder_marker=args.folder_marker)
        else:
            if not args.report.exists():
                raise SystemExit(f"Отчёт не найден: {args.report}. Запустите scripts/build_department_normative_bundles.py")
            bundles = _load_bundles_from_report(args.report)

        if args.department_key:
            bundles = [b for b in bundles if b.get("department_key") == args.department_key]
            if not bundles:
                raise SystemExit(f"Комплект не найден: {args.department_key}")

        bundles = [b for b in bundles if b.get("documents_count", 0) > 0]

        if args.dry_run:
            preview = []
            for bundle in bundles:
                doc_ids = _bundle_document_ids(bundle)
                preview.append(
                    {
                        "department_key": bundle.get("department_key"),
                        "department_name": _bundle_department_name(bundle),
                        "process_slug": _bundle_process_slug(bundle),
                        "kb_name": _kb_name(bundle),
                        "documents_count": len(doc_ids),
                        "matched_1c": bundle.get("enterprise_department_id") is not None,
                        "warnings": bundle.get("warnings", []),
                    }
                )
            print(
                json.dumps(
                    {
                        "status": "dry_run",
                        "bundles_count": len(preview),
                        "total_documents": sum(item["documents_count"] for item in preview),
                        "bundles": preview,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        created_count = 0
        updated_count = 0
        kb_results: list[dict] = []

        for bundle in bundles:
            document_ids = _bundle_document_ids(bundle)
            if not document_ids:
                continue

            kb, created, sources_added = await _get_or_create_kb(
                session,
                owner=owner,
                bundle=bundle,
                document_ids=document_ids,
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

            item = {
                "status": "created" if created else "updated",
                "department_key": bundle.get("department_key"),
                "department_name": _bundle_department_name(bundle),
                "process_slug": kb.process_slug,
                "knowledge_base_id": str(kb.id),
                "knowledge_base_name": kb.name,
                "sources_count": kb.sources_count,
                "sources_added": sources_added,
                "documents_in_bundle": len(document_ids),
            }
            kb_results.append(item)

        await session.commit()

        if args.queue_index:
            for item in kb_results:
                item["indexing"] = await _queue_index(uuid.UUID(item["knowledge_base_id"]), owner_id)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "knowledge_bases_created": created_count,
                    "knowledge_bases_updated": updated_count,
                    "knowledge_bases_total": len(kb_results),
                    "documents_total": sum(item["documents_in_bundle"] for item in kb_results),
                    "knowledge_bases": kb_results,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
