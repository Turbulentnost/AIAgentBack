from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import NdDevelopmentRequestKind, NdReportKind, NdValidationStandard
from app.models.user import Role, User
from app.services.nd_development_request_service import NdDevelopmentRequestService
from app.services.nd_document_validation_service import NdDocumentValidationService
from app.services.nd_reports_service import NdReportsService
from app.services.nd_visio_service import NdVisioService
from app.schemas.nd_development_request import NdDevelopmentRequestCreate


def _user() -> User:
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        username="test-user",
        hashed_password="hash",
        full_name="Test User",
        position="Специалист по процессному управлению",
        is_active=True,
        is_superuser=False,
    )
    user.role = Role(code="employee", name="employee", is_system=True)
    return user


def _db() -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=0)
    db.get = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_development_request_suggest_sto_kind() -> None:
    db = _db()
    service = NdDevelopmentRequestService(db)
    payload = NdDevelopmentRequestCreate(
        kind=NdDevelopmentRequestKind.NEW_DOCUMENT,
        title="СТО входного контроля",
        justification="Требуется стандарт организации для входного контроля продукции",
        process_description="Контроль качества поступающих материалов",
    )
    item = await service.create(payload, current_user=_user())
    assert item.document_kind.value == "sto"
    assert item.number.startswith("NDN-")


def test_visio_import_rejects_unknown_extension() -> None:
    result = NdVisioService().import_vsdx(filename="diagram.txt", content=b"test")
    assert result.imported is False


@pytest.mark.asyncio
async def test_reports_list_contains_management_review() -> None:
    reports = await NdReportsService(_db()).list_available()
    kinds = {item["kind"] for item in reports}
    assert NdReportKind.MANAGEMENT_REVIEW.value in kinds


@pytest.mark.asyncio
async def test_validation_detects_missing_version_marker() -> None:
    db = _db()
    document_id = uuid.uuid4()

    class _Document:
        title = "Положение о входном контроле"
        metadata_ = {"qms_document_kind": "regulation"}

    db.get = AsyncMock(return_value=_Document())
    report = await NdDocumentValidationService(db).validate_document(
        document_id,
        standards=[NdValidationStandard.STO_34_003],
    )
    assert report.document_id == document_id
    assert any(item.code == "missing_version_marker" for item in report.findings)
