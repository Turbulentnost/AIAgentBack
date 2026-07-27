from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.check_run import EskdCheckRun
from app.models.check_run_change import EskdCheckRunChange
from app.models.marking import EskdMarkingDocument
from app.schemas.marking import MarkingLabelCreate
from app.services.marking_service import MarkingService
from app.services.check_version_service import CheckVersionService
from app.services.user_service import EskdActor


@dataclass
class _KbRow:
    key: str
    display_name: str
    designation: str | None = None
    checked: bool = False
    check_count: int = 0
    last_checked_at: datetime | None = None
    last_check_run_id: uuid.UUID | None = None
    total_errors: int | None = None
    total_warnings: int | None = None
    has_ai_check: bool = False
    has_marking: bool = False
    marking_document_id: uuid.UUID | None = None
    marked_pages_count: int = 0
    marking_updated_at: datetime | None = None
    human_verified_at: datetime | None = None
    pages_count: int | None = None
    verifiers: list[str] = field(default_factory=list)
    sort_ts: datetime = datetime.min


def _entry_key(*, sha256: str | None, filename: str | None) -> str:
    if sha256:
        return f"sha:{sha256.strip().lower()}"
    name = (filename or "unknown").strip().lower()
    return f"name:{name}"


def _filename_to_sha_map(check_runs: list[EskdCheckRun]) -> dict[str, str]:
    out: dict[str, str] = {}
    for run in check_runs:
        name = (run.original_filename or "").strip().lower()
        sha = (run.file_sha256 or "").strip().lower()
        if name and sha:
            out.setdefault(name, sha)
    return out


def _format_person_short(name: str | None) -> str | None:
    if not name or not name.strip():
        return None
    parts = [part for part in name.strip().split() if part]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    surname = parts[0]
    initials = "".join(f"{part[0]}." for part in parts[1:] if part[0].isalpha())
    return f"{surname} {initials}".strip() if initials else surname


def _add_verifier(row: _KbRow, name: str | None) -> None:
    short = _format_person_short(name)
    if short and short not in row.verifiers:
        row.verifiers.append(short)


def _merge_entry_key(
    *,
    sha256: str | None,
    filename: str | None,
    filename_to_sha: dict[str, str],
) -> str:
    norm = (filename or "").strip().lower()
    sha = (sha256 or "").strip().lower() if sha256 else ""
    if not sha and norm:
        sha = filename_to_sha.get(norm, "")
    return _entry_key(sha256=sha or None, filename=filename if not sha else None)


class KnowledgeBaseService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_entries(
        self,
        *,
        q: str | None = None,
        checked: bool | None = None,
        page: int = 1,
        size: int = 24,
    ) -> tuple[list[dict], int, int, int]:
        rows = await self._build_index()
        if q:
            needle = q.strip().lower()
            rows = [
                r
                for r in rows
                if needle in r.display_name.lower()
                or (r.designation and needle in r.designation.lower())
            ]
        checked_count = sum(1 for r in rows if r.checked)
        unchecked_count = len(rows) - checked_count

        if checked is True:
            rows = [r for r in rows if r.checked]
        elif checked is False:
            rows = [r for r in rows if not r.checked]

        rows.sort(key=lambda r: r.sort_ts, reverse=True)
        total = len(rows)
        offset = (page - 1) * size
        page_rows = rows[offset : offset + size]
        return [self._to_dict(r) for r in page_rows], total, checked_count, unchecked_count

    async def verify_entry(
        self,
        *,
        check_run_id: uuid.UUID | None = None,
        marking_document_id: uuid.UUID | None = None,
        actor: EskdActor | None = None,
    ) -> dict:
        if not check_run_id and not marking_document_id:
            raise ValueError("Укажите check_run_id или marking_document_id")

        now = datetime.now(timezone.utc)

        if check_run_id:
            run = await self._db.get(EskdCheckRun, check_run_id)
            if not run:
                raise ValueError("Проверка ИИ не найдена")
            run.human_verified_at = now
            await CheckVersionService(self._db).record_verification(run, actor=actor)
            await self._db.commit()

        if marking_document_id:
            marking_svc = MarkingService(self._db)
            doc = await marking_svc.get_document(marking_document_id)
            if not doc:
                raise ValueError("Документ разметки не найден")
            latest = await marking_svc.get_latest_label_for_document(marking_document_id)
            if latest is None:
                latest = await marking_svc.create_label(
                    MarkingLabelCreate(
                        document_id=marking_document_id,
                        problem_report="Подтверждено человеком",
                    )
                )
            latest.human_verified_at = now
            if actor:
                latest.verified_by_user_id = actor.user_id
                latest.verified_by_login = actor.login
                latest.verified_by_name = actor.display_name
            await self._db.commit()

        rows = await self._build_index()
        if check_run_id:
            row = next((r for r in rows if r.last_check_run_id == check_run_id), None)
            if row:
                return self._to_dict(row)
        if marking_document_id:
            row = next((r for r in rows if r.marking_document_id == marking_document_id), None)
            if row:
                return self._to_dict(row)
        raise ValueError("Запись базы знаний не найдена")

    async def delete_entry(self, key: str) -> dict:
        key = key.strip()
        if not key:
            raise ValueError("Не указан ключ записи")

        rows = await self._build_index()
        row = next((r for r in rows if r.key == key), None)
        if row is None:
            raise ValueError("Запись базы знаний не найдена")

        marking_docs = (
            await self._db.scalars(select(EskdMarkingDocument))
        ).all()
        check_runs = (await self._db.scalars(select(EskdCheckRun))).all()

        docs_to_delete, runs_to_delete = self._resolve_delete_targets(
            key,
            marking_docs=list(marking_docs),
            check_runs=list(check_runs),
        )

        if not docs_to_delete and not runs_to_delete:
            raise ValueError("Нечего удалять")

        marking_svc = MarkingService(self._db)
        deleted_docs = 0
        for doc in docs_to_delete:
            if await marking_svc.delete_document(doc.id):
                deleted_docs += 1

        deleted_runs = 0
        for run in runs_to_delete:
            await self._db.delete(run)
            deleted_runs += 1

        await self._db.commit()
        return {
            "key": key,
            "display_name": row.display_name,
            "deleted_marking_documents": deleted_docs,
            "deleted_check_runs": deleted_runs,
        }

    @staticmethod
    def _resolve_delete_targets(
        key: str,
        *,
        marking_docs: list[EskdMarkingDocument],
        check_runs: list[EskdCheckRun],
    ) -> tuple[list[EskdMarkingDocument], list[EskdCheckRun]]:
        docs: list[EskdMarkingDocument] = []
        runs: list[EskdCheckRun] = []

        if key.startswith("sha:"):
            sha = key[4:]
            runs = [r for r in check_runs if (r.file_sha256 or "").strip().lower() == sha]
            names = {(r.original_filename or "").strip().lower() for r in runs if r.original_filename}
            if names:
                docs = [d for d in marking_docs if d.source_filename.strip().lower() in names]
        elif key.startswith("name:"):
            name = key[5:]
            docs = [d for d in marking_docs if d.source_filename.strip().lower() == name]
            runs = [r for r in check_runs if (r.original_filename or "").strip().lower() == name]
        else:
            raise ValueError("Некорректный ключ записи")

        return docs, runs

    async def _build_index(self) -> list[_KbRow]:
        by_key: dict[str, _KbRow] = {}

        check_runs = (
            await self._db.scalars(select(EskdCheckRun).order_by(EskdCheckRun.created_at.desc()))
        ).all()
        verified_changes = (
            await self._db.scalars(
                select(EskdCheckRunChange).where(EskdCheckRunChange.change_type == "verified")
            )
        ).all()
        changes_by_run: dict[uuid.UUID, list[EskdCheckRunChange]] = defaultdict(list)
        for change in verified_changes:
            changes_by_run[change.run_id].append(change)
        filename_to_sha = _filename_to_sha_map(check_runs)

        marking_docs = (
            await self._db.scalars(
                select(EskdMarkingDocument).order_by(EskdMarkingDocument.updated_at.desc())
            )
        ).all()
        marking_svc = MarkingService(self._db)

        for doc in marking_docs:
            key = _merge_entry_key(
                sha256=None,
                filename=doc.source_filename,
                filename_to_sha=filename_to_sha,
            )
            latest_doc = await marking_svc.find_latest_document_by_filename(doc.source_filename)
            doc_for_row = latest_doc or doc
            latest = await marking_svc.get_latest_label_for_document(doc_for_row.id)
            marked_pages = len(latest.page_level or []) if latest else 0
            row = by_key.get(key) or _KbRow(key=key, display_name=doc.source_filename)
            row.key = key
            row.display_name = doc_for_row.source_filename
            row.designation = doc_for_row.designation or row.designation
            row.has_marking = True
            row.marking_document_id = doc_for_row.id
            row.marked_pages_count = max(row.marked_pages_count, marked_pages)
            row.pages_count = len(doc.pages or []) or row.pages_count
            marking_ts = latest.updated_at if latest else doc.updated_at
            if row.marking_updated_at is None or marking_ts > row.marking_updated_at:
                row.marking_updated_at = marking_ts
            if latest and latest.human_verified_at:
                row.checked = True
                if row.human_verified_at is None or latest.human_verified_at > row.human_verified_at:
                    row.human_verified_at = latest.human_verified_at
                _add_verifier(row, latest.verified_by_name)
            row.sort_ts = max(row.sort_ts, marking_ts, doc.updated_at)
            by_key[key] = row

        for run in check_runs:
            key = _merge_entry_key(
                sha256=run.file_sha256,
                filename=run.original_filename,
                filename_to_sha=filename_to_sha,
            )
            row = by_key.get(key) or _KbRow(
                key=key,
                display_name=run.original_filename or "Без имени",
            )
            row.key = key
            row.display_name = run.original_filename or row.display_name
            row.designation = run.designation or row.designation
            row.has_ai_check = True
            row.check_count += 1
            if row.last_checked_at is None or (run.created_at and run.created_at > row.last_checked_at):
                row.last_checked_at = run.created_at
                row.last_check_run_id = run.id
                row.total_errors = run.total_errors
                row.total_warnings = run.total_warnings
                row.pages_count = run.pages_count or row.pages_count
            if run.human_verified_at:
                row.checked = True
                if row.human_verified_at is None or run.human_verified_at > row.human_verified_at:
                    row.human_verified_at = run.human_verified_at
                _add_verifier(row, run.verified_by_name)
            for change in changes_by_run.get(run.id, []):
                _add_verifier(row, change.changed_by_name)
            row.sort_ts = max(row.sort_ts, run.created_at)
            by_key[key] = row

        return list(by_key.values())

    @staticmethod
    def _to_dict(row: _KbRow) -> dict:
        return {
            "key": row.key,
            "display_name": row.display_name,
            "designation": row.designation,
            "checked": row.checked,
            "check_count": row.check_count,
            "last_checked_at": row.last_checked_at,
            "last_check_run_id": row.last_check_run_id,
            "total_errors": row.total_errors,
            "total_warnings": row.total_warnings,
            "has_ai_check": row.has_ai_check,
            "has_marking": row.has_marking,
            "marking_document_id": row.marking_document_id,
            "marked_pages_count": row.marked_pages_count,
            "marking_updated_at": row.marking_updated_at,
            "human_verified_at": row.human_verified_at,
            "pages_count": row.pages_count,
            "verifiers": row.verifiers,
            "verifiers_count": len(row.verifiers),
        }
