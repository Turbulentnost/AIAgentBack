from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.check_run import EskdCheckRun
from app.models.check_run_change import EskdCheckRunChange
from app.services.user_service import EskdActor


def document_key_for(*, file_sha256: str | None, filename: str | None) -> str | None:
    if file_sha256:
        return f"sha:{file_sha256.strip().lower()}"
    name = (filename or "").strip().lower()
    return f"name:{name}" if name else None


def compute_check_diff(previous: EskdCheckRun | None, current: EskdCheckRun) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    if previous is None:
        diff["initial"] = True
        return diff

    fields = (
        ("total_errors", "ошибок"),
        ("total_warnings", "замечаний"),
        ("pages_count", "листов"),
    )
    for field, label in fields:
        old = getattr(previous, field)
        new = getattr(current, field)
        if old != new:
            diff[field] = {"before": old, "after": new, "label": label}

    if (previous.designation or "") != (current.designation or ""):
        diff["designation"] = {
            "before": previous.designation,
            "after": current.designation,
        }

    if previous.status != current.status:
        diff["status"] = {"before": previous.status, "after": current.status}

    prev_raw = previous.raw_result or {}
    curr_raw = current.raw_result or {}
    if prev_raw.get("pipeline_mode") != curr_raw.get("pipeline_mode"):
        diff["pipeline_mode"] = {
            "before": prev_raw.get("pipeline_mode"),
            "after": curr_raw.get("pipeline_mode"),
        }

    prev_pkg = len(prev_raw.get("package_errors") or [])
    curr_pkg = len(curr_raw.get("package_errors") or [])
    if prev_pkg != curr_pkg:
        diff["package_errors_count"] = {"before": prev_pkg, "after": curr_pkg}

    prev_gost_errors = set((previous.gost_summary or {}).get("errors") or {})
    curr_gost_errors = set((current.gost_summary or {}).get("errors") or {})
    added = sorted(curr_gost_errors - prev_gost_errors)
    removed = sorted(prev_gost_errors - curr_gost_errors)
    if added or removed:
        diff["gost_errors"] = {"added": added, "removed": removed}

    return diff


def summarize_diff(change_type: str, diff: dict[str, Any], *, version_no: int) -> str:
    if change_type == "verified":
        return f"Версия {version_no}: проверка подтверждена сотрудником ОТК"
    if change_type == "created":
        return f"Версия {version_no}: первая проверка документа"

    parts: list[str] = [f"Версия {version_no}: повторная проверка"]
    if "total_errors" in diff:
        before = diff["total_errors"]["before"]
        after = diff["total_errors"]["after"]
        parts.append(f"ошибок {before} → {after}")
    if "total_warnings" in diff:
        before = diff["total_warnings"]["before"]
        after = diff["total_warnings"]["after"]
        parts.append(f"замечаний {before} → {after}")
    if "designation" in diff:
        parts.append("изменено обозначение")
    if "package_errors_count" in diff:
        before = diff["package_errors_count"]["before"]
        after = diff["package_errors_count"]["after"]
        parts.append(f"cross-page замечаний {before} → {after}")
    if "pipeline_mode" in diff:
        parts.append(
            f"pipeline {diff['pipeline_mode'].get('before') or 'legacy'} → "
            f"{diff['pipeline_mode'].get('after') or 'legacy'}"
        )
    if "gost_errors" in diff:
        added = diff["gost_errors"].get("added") or []
        removed = diff["gost_errors"].get("removed") or []
        if added:
            parts.append(f"новые нарушения ГОСТ: {', '.join(added)}")
        if removed:
            parts.append(f"сняты нарушения ГОСТ: {', '.join(removed)}")
    return "; ".join(parts)


class CheckVersionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def find_latest_for_document(
        self,
        document_key: str | None,
        *,
        exclude_run_id: uuid.UUID | None = None,
    ) -> EskdCheckRun | None:
        if not document_key:
            return None
        query = (
            select(EskdCheckRun)
            .where(EskdCheckRun.document_key == document_key)
            .order_by(EskdCheckRun.version_no.desc(), EskdCheckRun.created_at.desc())
            .limit(1)
        )
        if exclude_run_id:
            query = query.where(EskdCheckRun.id != exclude_run_id)
        return await self._db.scalar(query)

    async def next_version_no(self, document_key: str | None) -> int:
        if not document_key:
            return 1
        current = await self._db.scalar(
            select(func.max(EskdCheckRun.version_no)).where(EskdCheckRun.document_key == document_key)
        )
        return int(current or 0) + 1

    async def apply_version_metadata(
        self,
        run: EskdCheckRun,
        *,
        actor: EskdActor | None,
    ) -> EskdCheckRunChange:
        document_key = document_key_for(
            file_sha256=run.file_sha256,
            filename=run.original_filename,
        )
        run.document_key = document_key
        parent = await self.find_latest_for_document(document_key, exclude_run_id=run.id)
        if parent and parent.id != run.id:
            run.parent_run_id = parent.id
            run.version_no = parent.version_no + 1
            change_type = "rerun"
        else:
            run.version_no = 1
            change_type = "created"
            parent = None

        if actor:
            run.created_by_user_id = actor.user_id
            run.created_by_login = actor.login
            run.created_by_name = actor.display_name

        diff = compute_check_diff(parent, run)
        summary = summarize_diff(change_type, diff, version_no=run.version_no)
        change = EskdCheckRunChange(
            run_id=run.id,
            parent_run_id=parent.id if parent else None,
            version_no=run.version_no,
            changed_by_user_id=actor.user_id if actor else None,
            changed_by_login=actor.login if actor else None,
            changed_by_name=actor.display_name if actor else None,
            change_type=change_type,
            summary=summary,
            diff=diff,
        )
        self._db.add(change)
        return change

    async def record_verification(
        self,
        run: EskdCheckRun,
        *,
        actor: EskdActor | None,
    ) -> EskdCheckRunChange:
        if actor:
            run.verified_by_user_id = actor.user_id
            run.verified_by_login = actor.login
            run.verified_by_name = actor.display_name

        change = EskdCheckRunChange(
            run_id=run.id,
            parent_run_id=run.parent_run_id,
            version_no=run.version_no,
            changed_by_user_id=actor.user_id if actor else None,
            changed_by_login=actor.login if actor else None,
            changed_by_name=actor.display_name if actor else None,
            change_type="verified",
            summary=summarize_diff("verified", {}, version_no=run.version_no),
            diff={"verified": True},
        )
        self._db.add(change)
        return change

    async def list_versions(self, run_id: uuid.UUID) -> list[EskdCheckRun]:
        run = await self._db.get(EskdCheckRun, run_id)
        if not run or not run.document_key:
            if run:
                return [run]
            return []
        rows = (
            await self._db.scalars(
                select(EskdCheckRun)
                .where(EskdCheckRun.document_key == run.document_key)
                .order_by(EskdCheckRun.version_no.asc())
            )
        ).all()
        return list(rows)

    async def list_changes(self, run_id: uuid.UUID) -> list[EskdCheckRunChange]:
        run = await self._db.get(EskdCheckRun, run_id)
        if not run or not run.document_key:
            return list(
                (
                    await self._db.scalars(
                        select(EskdCheckRunChange)
                        .where(EskdCheckRunChange.run_id == run_id)
                        .order_by(EskdCheckRunChange.created_at.asc())
                    )
                ).all()
            )
        run_ids = [
            row.id
            for row in (
                await self._db.scalars(
                    select(EskdCheckRun.id).where(EskdCheckRun.document_key == run.document_key)
                )
            ).all()
        ]
        return list(
            (
                await self._db.scalars(
                    select(EskdCheckRunChange)
                    .where(EskdCheckRunChange.run_id.in_(run_ids))
                    .order_by(EskdCheckRunChange.created_at.asc())
                )
            ).all()
        )
