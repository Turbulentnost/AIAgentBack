"""CRUD справочника отделов (таблица departments)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from agent_pochta.db.models import DepartmentRow
from agent_pochta.schemas import DepartmentRecord


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row_to_record(row: DepartmentRow) -> DepartmentRecord:
    return DepartmentRecord(
        code=row.code,
        name=row.name,
        direction=row.direction,
        email=row.email,
        is_active=row.is_active,
        metadata=json.loads(row.metadata_json or "{}"),
    )


class DepartmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_many(self, records: list[DepartmentRecord]) -> int:
        now = _utc_now_naive()
        count = 0
        for record in records:
            row = self._session.query(DepartmentRow).filter_by(code=record.code).one_or_none()
            if row is None:
                row = DepartmentRow(
                    id=uuid.uuid4(),
                    code=record.code,
                    created_at=now,
                )
                self._session.add(row)

            row.name = record.name
            row.direction = record.direction
            row.email = record.email
            row.is_active = record.is_active
            row.metadata_json = json.dumps(record.metadata, ensure_ascii=False)
            row.updated_at = now
            count += 1
        self._session.flush()
        return count

    def get_by_code(self, code: str) -> DepartmentRecord | None:
        row = self._session.query(DepartmentRow).filter_by(code=code).one_or_none()
        return _row_to_record(row) if row else None

    def list_active(self, *, limit: int | None = None) -> list[DepartmentRecord]:
        query = (
            self._session.query(DepartmentRow)
            .filter_by(is_active=True)
            .order_by(DepartmentRow.code)
        )
        if limit is not None:
            query = query.limit(limit)
        return [_row_to_record(row) for row in query.all()]

    def list_for_ui(self) -> list[dict[str, str]]:
        return [{"id": record.code, "name": record.name} for record in self.list_active()]

    def count_active(self) -> int:
        return self._session.query(DepartmentRow).filter_by(is_active=True).count()

    def deactivate(self, code: str) -> bool:
        row = self._session.query(DepartmentRow).filter_by(code=code).one_or_none()
        if row is None:
            return False
        row.is_active = False
        row.updated_at = _utc_now_naive()
        self._session.flush()
        return True


def seed_departments_to_db(records: list[DepartmentRecord]) -> int:
    from agent_pochta.db.session import get_session_factory

    factory = get_session_factory()
    with factory() as session:
        repo = DepartmentRepository(session)
        count = repo.upsert_many(records)
        session.commit()
        return count
