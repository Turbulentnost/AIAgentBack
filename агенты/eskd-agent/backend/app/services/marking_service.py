from __future__ import annotations

import io
import shutil
import uuid
from collections import defaultdict
from pathlib import Path

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.format_processing.pipeline import process_bytes
from app.format_processing.types import ProcessedArtifact
from app.gost.catalog import GOST_LINE_ORDER
from app.models.check_run import EskdCheckRun
from app.models.marking import EskdMarkingDocument, EskdMarkingLabel
from app.schemas.marking import MarkingLabelCreate


class MarkingService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._storage = Path(settings.storage_path)

    async def create_document(
        self,
        *,
        filename: str,
        data: bytes,
        designation: str | None = None,
    ) -> EskdMarkingDocument:
        self._storage.mkdir(parents=True, exist_ok=True)
        doc_id = uuid.uuid4()
        doc_dir = self._storage / str(doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)

        prep = process_bytes(filename, data)
        image_arts = [a for a in prep.artifacts if a.kind == "image"]
        if not image_arts:
            ext = Path(filename).suffix.lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                image_arts = [
                    ProcessedArtifact(
                        source=filename,
                        name=Path(filename).name,
                        kind="image",
                        data=data,
                        mime="image/png",
                        format="image",
                        meta={"page": 1},
                    )
                ]

        pages_meta: list[dict] = []
        for art in sorted(image_arts, key=lambda a: int((a.meta or {}).get("page") or 0)):
            page_no = int((art.meta or {}).get("page") or len(pages_meta) + 1)
            rel_name = f"p{page_no:02d}.jpg"
            rel_path = f"{doc_id}/{rel_name}"
            abs_path = self._storage / rel_path
            width, height = _save_as_jpg(art.data, abs_path)
            pages_meta.append(
                {
                    "page": page_no,
                    "preview_path": rel_path,
                    "width": width,
                    "height": height,
                }
            )

        if not pages_meta:
            raise ValueError("Не удалось сформировать превью страниц для разметки")

        doc = EskdMarkingDocument(
            id=doc_id,
            designation=designation,
            source_filename=filename,
            pages=pages_meta,
        )
        self._db.add(doc)
        await self._db.commit()
        await self._db.refresh(doc)
        return doc

    async def find_latest_document_by_filename(self, filename: str) -> EskdMarkingDocument | None:
        needle = filename.strip().lower()
        if not needle:
            return None
        docs = (
            await self._db.scalars(
                select(EskdMarkingDocument)
                .where(func.lower(EskdMarkingDocument.source_filename) == needle)
                .order_by(EskdMarkingDocument.updated_at.desc())
            )
        ).all()
        if not docs:
            return None
        best = docs[0]
        best_ts = best.updated_at
        for doc in docs:
            label = await self.get_latest_label_for_document(doc.id)
            ts = label.updated_at if label else doc.updated_at
            if ts >= best_ts:
                best = doc
                best_ts = ts
        return best

    async def upload_document(
        self,
        *,
        filename: str,
        data: bytes,
        designation: str | None = None,
        reuse_existing: bool = True,
    ) -> tuple[EskdMarkingDocument, bool]:
        if reuse_existing:
            existing = await self.find_latest_document_by_filename(filename)
            if existing:
                return existing, True
        doc = await self.create_document(filename=filename, data=data, designation=designation)
        return doc, False

    async def get_document(self, doc_id: uuid.UUID) -> EskdMarkingDocument | None:
        return await self._db.get(EskdMarkingDocument, doc_id)

    async def get_label(self, label_id: uuid.UUID) -> EskdMarkingLabel | None:
        return await self._db.get(EskdMarkingLabel, label_id)

    async def get_latest_label_for_document(self, doc_id: uuid.UUID) -> EskdMarkingLabel | None:
        return (
            await self._db.scalars(
                select(EskdMarkingLabel)
                .where(EskdMarkingLabel.document_id == doc_id)
                .order_by(EskdMarkingLabel.updated_at.desc())
                .limit(1)
            )
        ).first()

    async def list_documents(self, *, limit: int = 50) -> list[dict]:
        docs = (
            await self._db.scalars(
                select(EskdMarkingDocument).order_by(EskdMarkingDocument.updated_at.desc()).limit(limit)
            )
        ).all()
        items: list[dict] = []
        for doc in docs:
            latest = await self.get_latest_label_for_document(doc.id)
            marked_pages = len(latest.page_level or []) if latest else 0
            items.append(
                {
                    "id": doc.id,
                    "designation": doc.designation,
                    "source_filename": doc.source_filename,
                    "pages_count": len(doc.pages or []),
                    "created_at": doc.created_at,
                    "latest_label_id": latest.id if latest else None,
                    "marked_pages_count": marked_pages,
                    "label_updated_at": latest.updated_at if latest else None,
                }
            )
        return items

    async def update_label(self, label_id: uuid.UUID, payload: MarkingLabelCreate) -> EskdMarkingLabel:
        label = await self.get_label(label_id)
        if not label:
            raise ValueError("Разметка не найдена")
        if label.document_id != payload.document_id:
            raise ValueError("document_id не совпадает")

        label.document_level = [f.model_dump() for f in payload.document_level]
        label.page_level = [p.model_dump() for p in payload.page_level]
        label.problem_report = payload.problem_report or None
        await self._db.commit()
        await self._db.refresh(label)
        return label

    def preview_path(self, doc_id: uuid.UUID, page: int) -> Path | None:
        return None

    def resolve_preview_file(self, doc: EskdMarkingDocument, page: int) -> Path | None:
        for item in doc.pages or []:
            if int(item.get("page") or 0) == page:
                rel = item.get("preview_path")
                if rel:
                    path = self._storage / str(rel)
                    if path.is_file():
                        return path
        return None

    async def create_label(self, payload: MarkingLabelCreate) -> EskdMarkingLabel:
        doc = await self.get_document(payload.document_id)
        if not doc:
            raise ValueError("Документ не найден")

        label = EskdMarkingLabel(
            document_id=payload.document_id,
            check_run_id=payload.check_run_id,
            is_rework=payload.is_rework,
            document_level=[f.model_dump() for f in payload.document_level],
            page_level=[p.model_dump() for p in payload.page_level],
            problem_report=payload.problem_report or None,
        )
        self._db.add(label)
        await self._db.commit()
        await self._db.refresh(label)
        return label

    async def list_labels(self, *, limit: int = 100) -> list[EskdMarkingLabel]:
        rows = (
            await self._db.scalars(
                select(EskdMarkingLabel).order_by(EskdMarkingLabel.created_at.desc()).limit(limit)
            )
        ).all()
        return list(rows)

    async def compute_stats(self) -> list[dict]:
        labels = await self.list_labels(limit=5000)
        title_map = {key: title for key, title in GOST_LINE_ORDER}

        # Только последняя разметка на документ — без накопления старых сохранений
        latest_by_doc: dict[uuid.UUID, EskdMarkingLabel] = {}
        for label in labels:
            existing = latest_by_doc.get(label.document_id)
            if existing is None or label.updated_at > existing.updated_at:
                latest_by_doc[label.document_id] = label
        labels = list(latest_by_doc.values())

        doc_ids = {label.document_id for label in labels}
        docs_by_id: dict[uuid.UUID, EskdMarkingDocument] = {}
        if doc_ids:
            docs = (
                await self._db.scalars(
                    select(EskdMarkingDocument).where(EskdMarkingDocument.id.in_(doc_ids))
                )
            ).all()
            docs_by_id = {doc.id: doc for doc in docs}

        check_runs = (await self._db.scalars(select(EskdCheckRun))).all()
        runs_by_filename: dict[str, list[EskdCheckRun]] = defaultdict(list)
        for run in check_runs:
            if run.original_filename:
                runs_by_filename[run.original_filename.strip().lower()].append(run)

        error_counts: dict[str, int] = defaultdict(int)
        warning_counts: dict[str, int] = defaultdict(int)
        after_ai_error_counts: dict[str, int] = defaultdict(int)
        after_ai_warning_counts: dict[str, int] = defaultdict(int)

        for label in labels:
            doc = docs_by_id.get(label.document_id)
            after_ai = self._label_is_after_ai_check(
                label,
                doc,
                runs_by_filename=runs_by_filename,
            )
            # Считаем только page_level — document_level дублирует те же отметки
            for page_entry in label.page_level or []:
                if not isinstance(page_entry, dict):
                    continue
                for finding in page_entry.get("gost_findings") or []:
                    if not isinstance(finding, dict):
                        continue
                    key = str(finding.get("gost_key") or "")
                    sev = str(finding.get("severity") or "ok")
                    if sev == "error":
                        error_counts[key] += 1
                        if after_ai:
                            after_ai_error_counts[key] += 1
                    elif sev == "warning":
                        warning_counts[key] += 1
                        if after_ai:
                            after_ai_warning_counts[key] += 1

        items: list[dict] = []
        all_keys = set(title_map) | set(error_counts) | set(warning_counts) | set(after_ai_error_counts) | set(after_ai_warning_counts)
        for key in sorted(all_keys):
            err = error_counts.get(key, 0)
            warn = warning_counts.get(key, 0)
            after_ai_err = after_ai_error_counts.get(key, 0)
            after_ai_warn = after_ai_warning_counts.get(key, 0)
            items.append(
                {
                    "gost_key": key,
                    "title": title_map.get(key, key),
                    "error_count": err,
                    "warning_count": warn,
                    "total": err + warn,
                    "after_ai_error_count": after_ai_err,
                    "after_ai_warning_count": after_ai_warn,
                    "after_ai_total": after_ai_err + after_ai_warn,
                }
            )
        items.sort(key=lambda x: x["total"], reverse=True)
        return items

    @staticmethod
    def _label_is_after_ai_check(
        label: EskdMarkingLabel,
        doc: EskdMarkingDocument | None,
        *,
        runs_by_filename: dict[str, list[EskdCheckRun]],
    ) -> bool:
        if label.check_run_id:
            return True
        if not doc or not doc.source_filename:
            return False
        fname = doc.source_filename.strip().lower()
        for run in runs_by_filename.get(fname, []):
            if run.created_at and label.updated_at and run.created_at <= label.updated_at:
                return True
        return False

    async def _is_rework_label(self, label: EskdMarkingLabel) -> bool:
        if label.is_rework:
            return True
        if not label.check_run_id:
            return False

        run = await self._db.get(EskdCheckRun, label.check_run_id)
        if not run or not run.file_sha256:
            return False

        prior = (
            await self._db.scalars(
                select(EskdCheckRun)
                .where(
                    EskdCheckRun.file_sha256 == run.file_sha256,
                    EskdCheckRun.created_at < run.created_at,
                    EskdCheckRun.total_errors > 0,
                )
                .limit(1)
            )
        ).first()
        return prior is not None

    async def delete_document(self, doc_id: uuid.UUID) -> bool:
        doc = await self.get_document(doc_id)
        if not doc:
            return False
        doc_dir = self._storage / str(doc_id)
        if doc_dir.is_dir():
            shutil.rmtree(doc_dir, ignore_errors=True)
        await self._db.delete(doc)
        return True


def _save_as_jpg(data: bytes, path: Path) -> tuple[int | None, int | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data)) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size
        rgb.save(path, format="JPEG", quality=88, optimize=True)
        return width, height
