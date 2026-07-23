from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.enums import TextExtractStatus
from app.schemas.document_card import DocumentCardUpdate
from app.services.department_normative_path_utils import (
    match_enterprise_department,
    parse_normative_relative_path,
    related_departments_from_path,
)
from app.services.document_card_service import DocumentCardService
from app.services.document_card_utils import (
    DOCUMENT_KIND_LABELS,
    QMS_LEVEL_LABELS,
    extract_document_code,
    fallback_document_code,
    infer_document_kind,
    infer_qms_level,
)
from app.services.onec_departments_fetcher import EnterpriseDepartment, fetch_all_departments_from_1c

FOLDER_MARKER = "НОРМАТИВНЫЕ ДОКУМЕНТЫ ОРГАНИЗАЦИИ"


class DepartmentBundleDocument(BaseModel):
    document_id: uuid.UUID
    document_code: str
    document_name: str
    document_type: str
    qms_level: str
    owner_department: str | None
    scope: str | None
    relative_path: str | None
    related_documents: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DepartmentNormativeBundle(BaseModel):
    department_key: str
    folder_department: str | None
    enterprise_department_name: str | None
    enterprise_department_path: str | None
    enterprise_department_id: str | None
    documents_count: int
    summary: dict[str, dict[str, int]]
    warnings: list[str] = Field(default_factory=list)
    documents: list[DepartmentBundleDocument] = Field(default_factory=list)


class DepartmentNormativeBundleReport(BaseModel):
    departments_from_1c: int
    documents_scanned: int
    documents_assigned: int
    documents_excluded: int
    documents_outside_tree: int
    cards_created: int
    cards_updated: int
    cards_skipped: int
    cards_table_available: bool
    global_warnings: list[str] = Field(default_factory=list)
    bundles: list[DepartmentNormativeBundle] = Field(default_factory=list)


@dataclass
class _Assignment:
    document: Document
    folder_department: str
    enterprise_department: EnterpriseDepartment | None
    scope: str | None
    match_warning: str | None
    excluded: bool = False
    excluded_reason: str | None = None
    outside_tree: bool = False
    doc_warnings: list[str] = field(default_factory=list)


class DepartmentNormativeBundleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.card_service = DocumentCardService(db)

    async def build_report(
        self,
        *,
        persist_cards: bool = True,
        folder_marker: str = FOLDER_MARKER,
    ) -> DepartmentNormativeBundleReport:
        departments = fetch_all_departments_from_1c()
        cards_table_available = await self._cards_table_available()
        documents = await self._load_documents(folder_marker)

        assignments: list[_Assignment] = []
        excluded_count = 0
        outside_count = 0
        global_warnings: list[str] = []

        for document in documents:
            metadata = document.metadata_ or document.doc_metadata or {}
            relative_path = metadata.get("import_relative_path") or metadata.get("relative_path")
            parsed = parse_normative_relative_path(str(relative_path) if relative_path else None)

            if parsed.excluded_reason:
                excluded_count += 1
                continue

            if parsed.folder_department is None:
                outside_count += 1
                continue

            enterprise_department, match_warning = match_enterprise_department(
                parsed.folder_department,
                departments,
            )
            scope = " / ".join(parsed.scope_parts) if parsed.scope_parts else None
            doc_warnings: list[str] = []
            if match_warning:
                doc_warnings.append(match_warning)
            if document.text_extract_status == TextExtractStatus.FAILED:
                doc_warnings.append("Извлечение текста завершилось ошибкой")

            assignments.append(
                _Assignment(
                    document=document,
                    folder_department=parsed.folder_department,
                    enterprise_department=enterprise_department,
                    scope=scope,
                    match_warning=match_warning,
                    doc_warnings=doc_warnings,
                )
            )

        by_department: dict[str, list[_Assignment]] = defaultdict(list)
        for item in assignments:
            key = item.enterprise_department.external_id if item.enterprise_department else f"unmatched:{item.folder_department}"
            by_department[key].append(item)

        cards_created = 0
        cards_updated = 0
        cards_skipped = 0
        bundles: list[DepartmentNormativeBundle] = []

        for department_key, items in sorted(by_department.items(), key=lambda pair: pair[0]):
            bundle_warnings: list[str] = []
            first = items[0]
            enterprise = first.enterprise_department
            if enterprise is None:
                bundle_warnings.append(
                    f"Комплект без привязки к 1С: папка «{first.folder_department}» ({len(items)} док.)"
                )

            prepared: list[tuple[_Assignment, str, object, object, str, list[str]]] = []
            for item in items:
                metadata = item.document.metadata_ or item.document.doc_metadata or {}
                code = extract_document_code(
                    title=item.document.title,
                    original_filename=item.document.original_filename,
                    metadata=metadata,
                ) or fallback_document_code(str(item.document.id))
                if code.startswith("ND-"):
                    item.doc_warnings.append("Код документа не распознан из имени файла")

                document_kind = infer_document_kind(code)
                qms_level = infer_qms_level(document_kind)
                owner_department = enterprise.name if enterprise else item.folder_department
                related_departments = related_departments_from_path(enterprise) if enterprise else []
                prepared.append((item, code, document_kind, qms_level, owner_department, related_departments))

            codes = [entry[1] for entry in prepared]
            duplicate_codes = [code for code, count in Counter(codes).items() if count > 1]
            if duplicate_codes:
                bundle_warnings.append(f"Дублирующиеся коды в комплекте: {', '.join(duplicate_codes[:5])}")

            doc_payloads: list[DepartmentBundleDocument] = []
            for item, code, document_kind, qms_level, owner_department, related_departments in prepared:
                metadata = item.document.metadata_ or item.document.doc_metadata or {}
                related_documents = [
                    other_code
                    for other_item, other_code, *_ in prepared
                    if other_item.document.id != item.document.id and other_code != code
                ][:20]

                if persist_cards and cards_table_available:
                    action = await self._upsert_card(
                        item.document,
                        owner_department=owner_department,
                        scope=item.scope,
                        related_departments=related_departments,
                        related_documents=related_documents,
                        code=code,
                        document_kind=document_kind,
                        qms_level=qms_level,
                        metadata=metadata,
                    )
                    if action == "created":
                        cards_created += 1
                    elif action == "updated":
                        cards_updated += 1
                    else:
                        cards_skipped += 1
                elif persist_cards and not cards_table_available:
                    cards_skipped += 1

                doc_payloads.append(
                    DepartmentBundleDocument(
                        document_id=item.document.id,
                        document_code=code,
                        document_name=str(metadata.get("document_name") or item.document.title),
                        document_type=DOCUMENT_KIND_LABELS[document_kind],
                        qms_level=QMS_LEVEL_LABELS[qms_level],
                        owner_department=owner_department,
                        scope=item.scope,
                        relative_path=metadata.get("import_relative_path"),
                        related_documents=related_documents,
                        warnings=item.doc_warnings,
                    )
                )

            type_counter: Counter[str] = Counter()
            level_counter: Counter[str] = Counter()
            for doc in doc_payloads:
                type_counter[doc.document_type] += 1
                level_counter[doc.qms_level] += 1

            bundles.append(
                DepartmentNormativeBundle(
                    department_key=department_key,
                    folder_department=first.folder_department,
                    enterprise_department_name=enterprise.name if enterprise else None,
                    enterprise_department_path=enterprise.path if enterprise else None,
                    enterprise_department_id=enterprise.external_id if enterprise else None,
                    documents_count=len(doc_payloads),
                    summary={
                        "by_document_type": dict(type_counter),
                        "by_qms_level": dict(level_counter),
                    },
                    warnings=bundle_warnings,
                    documents=sorted(doc_payloads, key=lambda doc: doc.document_code),
                )
            )

        if persist_cards and not cards_table_available:
            global_warnings.append(
                "Таблица document_cards недоступна — карточки не сохранены, сформирован только отчёт по комплектам"
            )

        unmatched_bundles = sum(1 for bundle in bundles if bundle.enterprise_department_id is None)
        if unmatched_bundles:
            global_warnings.append(f"Комплектов без совпадения с 1С: {unmatched_bundles}")

        return DepartmentNormativeBundleReport(
            departments_from_1c=len(departments),
            documents_scanned=len(documents),
            documents_assigned=len(assignments),
            documents_excluded=excluded_count,
            documents_outside_tree=outside_count,
            cards_created=cards_created,
            cards_updated=cards_updated,
            cards_skipped=cards_skipped,
            cards_table_available=cards_table_available,
            global_warnings=global_warnings,
            bundles=bundles,
        )

    async def _load_documents(self, folder_marker: str) -> list[Document]:
        stmt = select(Document).where(
            or_(
                Document.metadata_["import_folder_root"].as_string().ilike(f"%{folder_marker}%"),
                Document.metadata_["original_storage_location"].as_string().ilike(f"%{folder_marker}%"),
                Document.metadata_["import_relative_path"].as_string().ilike("%Нормативные документы по подразделениям%"),
            )
        )
        stmt = stmt.order_by(Document.created_at.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _cards_table_available(self) -> bool:
        from sqlalchemy import text

        try:
            await self.db.execute(text("SELECT 1 FROM document_cards LIMIT 1"))
            return True
        except ProgrammingError:
            await self.db.rollback()
            return False

    async def _upsert_card(
        self,
        document: Document,
        *,
        owner_department: str,
        scope: str | None,
        related_departments: list[str],
        related_documents: list[str],
        code: str,
        document_kind,
        qms_level,
        metadata: dict,
    ) -> str:
        existing = await self.card_service.get_by_document_id(document.id)
        attachments: list[str] = []
        if document.original_filename:
            attachments.append(document.original_filename)

        created = False
        if existing is None:
            try:
                await self.card_service.create_from_document(document)
                created = True
            except Exception:
                return "skipped"
            existing = await self.card_service.get_by_document_id(document.id)
            if existing is None:
                return "skipped"

        await self.card_service.update(
            existing.id,
            DocumentCardUpdate(
                document_code=code,
                document_name=str(metadata.get("document_name") or document.title),
                document_type=document_kind,
                qms_level=qms_level,
                owner_department=owner_department,
                scope=scope,
                related_departments=related_departments,
                related_documents=related_documents,
                original_storage_location=metadata.get("original_storage_location")
                or metadata.get("import_source_path"),
                electronic_storage_location=metadata.get("electronic_storage_location") or "DMS/Knowledge Base",
                attachments=attachments or None,
            ),
        )
        return "created" if created else "updated"
