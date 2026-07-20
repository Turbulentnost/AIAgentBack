from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.v1.endpoints import nd_control
from app.models.enums import NdTemplateType
from app.models.user import Role, User
from app.schemas.nd_control_analysis import DepartmentAnalysisStartRequest
from app.schemas.nd_control_registry import NdControlDepartmentCreate
from app.schemas.nd_control_templates import (
    NdControlTemplateDocumentCreate,
    NdControlTemplateUpdate,
)


def _user(*, position: str | None = None, role_code: str = "employee", superuser: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        username=str(uuid.uuid4()),
        hashed_password="hash",
        full_name="Test User",
        position=position,
        is_active=True,
        is_superuser=superuser,
    )
    user.role = Role(code=role_code, name=role_code, is_system=True)
    return user


def _db() -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=None)
    return db


@pytest.mark.asyncio
async def test_regular_user_cannot_patch_template() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.update_nd_template(
            uuid.uuid4(),
            NdControlTemplateUpdate(name="Новое имя"),
            _db(),
            _user(position="Инженер"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_regular_user_cannot_upload_template_document() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.add_nd_template_document(
            uuid.uuid4(),
            NdControlTemplateDocumentCreate(knowledge_base_source_id=uuid.uuid4()),
            _db(),
            _user(position="Инженер"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_specialist_cannot_create_department() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.create_nd_control_department(
            NdControlDepartmentCreate(
                name="Производство",
                knowledge_base_ids=[uuid.uuid4()],
                auto_start_analysis=False,
            ),
            _db(),
            _user(position="Специалист по процессному управлению"),
            BackgroundTasks(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_specialist_cannot_analyze_department() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.analyze_nd_control_department(
            uuid.uuid4(),
            DepartmentAnalysisStartRequest(force_reextract=False),
            _db(),
            _user(position="Специалист по процессному управлению"),
            BackgroundTasks(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_specialist_cannot_cancel_department_analysis() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.cancel_nd_control_department_analysis(
            uuid.uuid4(),
            _db(),
            _user(position="Специалист по процессному управлению"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_specialist_cannot_approve_review_relation() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.approve_relation(
            uuid.uuid4(),
            _db(),
            _user(position="Специалист по процессному управлению"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_specialist_cannot_bulk_approve_review_relations() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.bulk_approve_relations(
            nd_control.BulkApproveRelationsRequest(relation_ids=[uuid.uuid4()]),
            _db(),
            _user(position="Специалист по процессному управлению"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_specialist_cannot_confirm_process_owner() -> None:
    with pytest.raises(HTTPException) as exc:
        await nd_control.confirm_process_owner(
            uuid.uuid4(),
            nd_control.ConfirmProcessOwnerRequest(),
            _db(),
            _user(position="Специалист по процессному управлению"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_specialist_can_upload_template_document(monkeypatch) -> None:
    template_id = uuid.uuid4()
    link_id = uuid.uuid4()
    source_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    class FakeService:
        def __init__(self, db):
            self.db = db

        async def add_template_document(self, template_id_arg, *, current_user, knowledge_base_source_id, document_id):
            assert template_id_arg == template_id
            assert knowledge_base_source_id == source_id
            assert document_id is None
            return MagicMock(id=link_id)

        async def get_template_document_detail_or_raise(self, template_id_arg, link_id_arg):
            assert template_id_arg == template_id
            assert link_id_arg == link_id
            return {
                "id": link_id,
                "template_id": template_id,
                "knowledge_base_id": uuid.uuid4(),
                "knowledge_base_source_id": source_id,
                "document_id": uuid.uuid4(),
                "document_version_id": uuid.uuid4(),
                "detected_template_type": None,
                "detected_template_type_label": None,
                "classification_confidence": None,
                "classification_status": "pending",
                "classified_at": None,
                "classified_by": None,
                "knowledge_base_name": "БЗ",
                "document_title": "Документ",
                "original_filename": "template.docx",
                "created_at": now,
                "updated_at": now,
            }

    enqueue = MagicMock()
    enqueue.delay = MagicMock()
    monkeypatch.setattr(nd_control, "NdControlTemplateService", FakeService)
    monkeypatch.setattr(nd_control, "classify_template_document", enqueue)

    result = await nd_control.add_nd_template_document(
        template_id,
        NdControlTemplateDocumentCreate(knowledge_base_source_id=source_id),
        _db(),
        _user(position="Специалист по процессному управлению"),
    )

    assert result.id == link_id
    enqueue.delay.assert_called_once_with(str(link_id))


@pytest.mark.asyncio
async def test_admin_can_patch_template(monkeypatch) -> None:
    template_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    class FakeService:
        def __init__(self, db):
            self.db = db

        async def update_template(self, template_id_arg, *, values, current_user):
            assert template_id_arg == template_id
            assert values == {"name": "Положение"}
            return MagicMock(id=template_id)

        async def get_template_detail_or_raise(self, template_id_arg):
            assert template_id_arg == template_id
            return {
                "id": template_id,
                "name": "Положение",
                "title": "Положение",
                "template_type": NdTemplateType.REGULATION,
                "template_type_label": "Положение",
                "description": None,
                "sort_order": 20,
                "is_active": True,
                "created_by_user_id": None,
                "documents_count": 0,
                "knowledge_bases_count": 0,
                "classification_stats": {},
                "created_at": now,
                "updated_at": now,
            }

    monkeypatch.setattr(nd_control, "NdControlTemplateService", FakeService)

    result = await nd_control.update_nd_template(
        template_id,
        NdControlTemplateUpdate(name="Положение"),
        _db(),
        _user(role_code="admin"),
    )

    assert result.id == template_id
