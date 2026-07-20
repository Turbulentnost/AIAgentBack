"""Upsert справочников 1С в PostgreSQL (staging перед Qdrant / merge)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from agent_pochta.db.models import CatalogSyncRunRow, ErpContractorRow, ErpDepartmentRow
from agent_pochta.schemas import Contractor, Department


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def begin_sync_run(self, source: str, *, notes: str | None = None) -> uuid.UUID:
        row = CatalogSyncRunRow(
            id=uuid.uuid4(),
            source=source,
            status="running",
            started_at=_utc_now_naive(),
            notes=notes,
        )
        self._session.add(row)
        self._session.flush()
        return row.id

    def finish_sync_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        contractors_count: int,
        departments_count: int,
        error_message: str | None = None,
    ) -> None:
        row = self._session.get(CatalogSyncRunRow, run_id)
        if row is None:
            return
        row.status = status
        row.finished_at = _utc_now_naive()
        row.contractors_count = contractors_count
        row.departments_count = departments_count
        row.error_message = error_message
        self._session.flush()

    def upsert_contractors(
        self,
        contractors: list[Contractor],
        *,
        source: str = "1c",
        sync_run_id: uuid.UUID | None = None,
        raw_rows: list[dict] | None = None,
    ) -> int:
        now = _utc_now_naive()
        raw_by_id = {
            str(r.get("contractor_id") or r.get("Ref_Key") or r.get("Code") or ""): r
            for r in (raw_rows or [])
            if isinstance(r, dict)
        }
        count = 0
        for contractor in contractors:
            row = (
                self._session.query(ErpContractorRow)
                .filter_by(source=source, contractor_id=contractor.contractor_id)
                .one_or_none()
            )
            payload = json.dumps(raw_by_id.get(contractor.contractor_id) or {}, ensure_ascii=False)
            external_ref = None
            raw = raw_by_id.get(contractor.contractor_id)
            if raw:
                external_ref = str(raw.get("Ref_Key") or raw.get("external_ref") or "") or None

            if row is None:
                row = ErpContractorRow(
                    id=uuid.uuid4(),
                    source=source,
                    contractor_id=contractor.contractor_id,
                    created_at=now,
                )
                self._session.add(row)

            row.name = contractor.name
            row.emails_json = json.dumps(contractor.emails, ensure_ascii=False)
            row.department_codes_json = json.dumps(contractor.department_codes, ensure_ascii=False)
            row.contractor_type = contractor.contractor_type
            row.external_ref = external_ref
            row.raw_payload_json = payload if raw else row.raw_payload_json
            row.is_active = True
            row.sync_run_id = sync_run_id
            row.updated_at = now
            count += 1
        self._session.flush()
        return count

    def upsert_departments(
        self,
        departments: list[Department],
        *,
        source: str = "1c",
        sync_run_id: uuid.UUID | None = None,
        raw_rows: list[dict] | None = None,
    ) -> int:
        now = _utc_now_naive()
        raw_by_id = {
            str(r.get("department_id") or r.get("Ref_Key") or r.get("Code") or ""): r
            for r in (raw_rows or [])
            if isinstance(r, dict)
        }
        count = 0
        for department in departments:
            row = (
                self._session.query(ErpDepartmentRow)
                .filter_by(source=source, department_id=department.department_id)
                .one_or_none()
            )
            payload = json.dumps(raw_by_id.get(department.department_id) or {}, ensure_ascii=False)
            external_ref = None
            raw = raw_by_id.get(department.department_id)
            if raw:
                external_ref = str(raw.get("Ref_Key") or raw.get("external_ref") or "") or None

            if row is None:
                row = ErpDepartmentRow(
                    id=uuid.uuid4(),
                    source=source,
                    department_id=department.department_id,
                    created_at=now,
                )
                self._session.add(row)

            row.department_name = department.department_name
            row.head_name = department.head_name
            row.responsibility = department.responsibility
            row.keywords_json = json.dumps(department.keywords, ensure_ascii=False)
            row.external_ref = external_ref
            row.raw_payload_json = payload if raw else row.raw_payload_json
            row.is_active = True
            row.sync_run_id = sync_run_id
            row.updated_at = now
            count += 1
        self._session.flush()
        return count

    def upsert_manual_contractor(
        self,
        *,
        contractor_id: str,
        name: str,
        email: str,
        department_code: str | None = None,
    ) -> ErpContractorRow:
        """Черновик контрагента от оператора (needs_review) для RAG и поля «Партнёр»."""
        now = _utc_now_naive()
        existing_other = (
            self._session.query(ErpContractorRow)
            .filter(
                ErpContractorRow.contractor_id == contractor_id,
                ErpContractorRow.is_active.is_(True),
                ErpContractorRow.source != "hitl",
            )
            .first()
        )
        if existing_other is not None:
            return existing_other

        row = (
            self._session.query(ErpContractorRow)
            .filter_by(source="hitl", contractor_id=contractor_id)
            .one_or_none()
        )
        if row is None:
            row = ErpContractorRow(
                id=uuid.uuid4(),
                source="hitl",
                contractor_id=contractor_id,
                created_at=now,
            )
            self._session.add(row)

        dept_codes = [department_code] if department_code else []
        row.name = name
        row.emails_json = json.dumps([email], ensure_ascii=False)
        row.department_codes_json = json.dumps(dept_codes, ensure_ascii=False)
        row.contractor_type = row.contractor_type or "клиент"
        row.needs_review = True
        row.is_active = True
        row.updated_at = now
        self._session.flush()
        return row

    def load_active_contractors(self, *, source: str | None = None) -> list[Contractor]:
        query = self._session.query(ErpContractorRow).filter_by(is_active=True)
        if source:
            query = query.filter_by(source=source)
        result: list[Contractor] = []
        for row in query.all():
            emails = json.loads(row.emails_json or "[]")
            if not emails:
                continue
            result.append(
                Contractor(
                    contractor_id=row.contractor_id,
                    name=row.name,
                    emails=emails,
                    department_codes=json.loads(row.department_codes_json or "[]"),
                    contractor_type=row.contractor_type,
                )
            )
        return result

    def load_active_departments(self, *, source: str | None = None) -> list[Department]:
        query = self._session.query(ErpDepartmentRow).filter_by(is_active=True)
        if source:
            query = query.filter_by(source=source)
        return [
            Department(
                department_id=row.department_id,
                department_name=row.department_name,
                head_name=row.head_name,
                responsibility=row.responsibility,
                keywords=json.loads(row.keywords_json or "[]"),
            )
            for row in query.all()
        ]


def persist_catalog_to_db(
    contractors: list[Contractor],
    departments: list[Department],
    *,
    source: str = "1c",
    notes: str | None = None,
    contractor_raw: list[dict] | None = None,
    department_raw: list[dict] | None = None,
) -> uuid.UUID:
    from agent_pochta.db.session import get_session_factory

    factory = get_session_factory()
    with factory() as session:
        repo = CatalogRepository(session)
        run_id = repo.begin_sync_run(source, notes=notes)
        try:
            c_count = repo.upsert_contractors(
                contractors, source=source, sync_run_id=run_id, raw_rows=contractor_raw
            )
            d_count = repo.upsert_departments(
                departments, source=source, sync_run_id=run_id, raw_rows=department_raw
            )
            repo.finish_sync_run(
                run_id,
                status="done",
                contractors_count=c_count,
                departments_count=d_count,
            )
            session.commit()
            return run_id
        except Exception as exc:
            repo.finish_sync_run(
                run_id,
                status="error",
                contractors_count=0,
                departments_count=0,
                error_message=str(exc),
            )
            session.commit()
            raise
