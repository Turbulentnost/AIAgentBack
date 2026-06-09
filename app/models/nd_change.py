from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    NdChangeApprovalStatus,
    NdChangeDraftFileStatus,
    NdChangeLocationStatus,
    NdChangeLocationType,
    NdChangeOperationStatus,
    NdChangeOperationType,
    NdChangeRequestStatus,
    NdChangeResultStatus,
)


class NdChangeRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_change_requests"

    number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reason: Mapped[str] = mapped_column(Text)
    release_date: Mapped[datetime | None] = mapped_column(Date)
    effective_date: Mapped[datetime | None] = mapped_column(Date)
    change_text: Mapped[str] = mapped_column(Text)
    initiator_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"), index=True)
    status: Mapped[NdChangeRequestStatus] = mapped_column(default=NdChangeRequestStatus.DRAFT, index=True)
    selected_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    selected_document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        index=True,
    )
    detection_confidence: Mapped[float | None] = mapped_column()
    requires_manual_document_selection: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    requires_manual_location_selection: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)

    candidates: Mapped[list["NdChangeCandidateDocument"]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
    )
    target_locations: Mapped[list["NdChangeTargetLocation"]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
    )
    operations: Mapped[list["NdChangeOperation"]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
    )
    draft_files: Mapped[list["NdChangeDraftFile"]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
    )
    approval_routes: Mapped[list["NdChangeApprovalRoute"]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
    )
    results: Mapped[list["NdChangeResult"]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
    )


class NdChangeCandidateDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_change_candidate_documents"

    change_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nd_change_requests.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"), index=True)
    score: Mapped[float] = mapped_column(default=0)
    rank: Mapped[int] = mapped_column(Integer, default=0, index=True)
    match_reason: Mapped[str | None] = mapped_column(Text)
    matched_fragments: Mapped[list | None] = mapped_column(JSONB)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    change_request: Mapped[NdChangeRequest] = relationship(back_populates="candidates")


class NdChangeTargetLocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_change_target_locations"

    change_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nd_change_requests.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"), index=True)
    section_number: Mapped[str | None] = mapped_column(String(128), index=True)
    section_title: Mapped[str | None] = mapped_column(String(512))
    page_number: Mapped[int | None] = mapped_column(Integer)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_chunks.id", ondelete="SET NULL"), index=True)
    location_type: Mapped[NdChangeLocationType] = mapped_column(default=NdChangeLocationType.BLOCK_TEXT, index=True)
    current_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()
    status: Mapped[NdChangeLocationStatus] = mapped_column(default=NdChangeLocationStatus.CANDIDATE, index=True)

    change_request: Mapped[NdChangeRequest] = relationship(back_populates="target_locations")


class NdChangeOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "nd_change_operations"

    change_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nd_change_requests.id", ondelete="CASCADE"), index=True)
    target_location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nd_change_target_locations.id", ondelete="SET NULL"), index=True)
    operation_type: Mapped[NdChangeOperationType] = mapped_column(default=NdChangeOperationType.MANUAL_REVIEW, index=True)
    old_text: Mapped[str | None] = mapped_column(Text)
    new_text: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[NdChangeOperationStatus] = mapped_column(default=NdChangeOperationStatus.DRAFT, index=True)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    change_request: Mapped[NdChangeRequest] = relationship(back_populates="operations")


class NdChangeDraftFile(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "nd_change_draft_files"

    change_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nd_change_requests.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    source_document_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"), index=True)
    draft_bucket: Mapped[str] = mapped_column(String(255))
    draft_object_name: Mapped[str] = mapped_column(String(1024), index=True)
    original_filename: Mapped[str | None] = mapped_column(String(512))
    generated_filename: Mapped[str] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(64), default="draft")
    status: Mapped[NdChangeDraftFileStatus] = mapped_column(default=NdChangeDraftFileStatus.GENERATED, index=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    change_request: Mapped[NdChangeRequest] = relationship(back_populates="draft_files")


class NdChangeApprovalRoute(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "nd_change_approval_routes"

    change_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nd_change_requests.id", ondelete="CASCADE"), index=True)
    status: Mapped[NdChangeApprovalStatus] = mapped_column(default=NdChangeApprovalStatus.DRAFT, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    change_request: Mapped[NdChangeRequest] = relationship(back_populates="approval_routes")
    participants: Mapped[list["NdChangeApprovalParticipant"]] = relationship(
        back_populates="approval_route",
        cascade="all, delete-orphan",
    )


class NdChangeApprovalParticipant(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "nd_change_approval_participants"

    approval_route_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nd_change_approval_routes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    role_name: Mapped[str | None] = mapped_column(String(255))
    approval_order: Mapped[int] = mapped_column(Integer, default=1, index=True)
    status: Mapped[NdChangeApprovalStatus] = mapped_column(default=NdChangeApprovalStatus.DRAFT, index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    approval_route: Mapped[NdChangeApprovalRoute] = relationship(back_populates="participants")


class NdChangeResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "nd_change_results"

    change_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nd_change_requests.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[NdChangeResultStatus] = mapped_column(default=NdChangeResultStatus.DRAFT, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()
    selected_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), index=True)
    draft_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nd_change_draft_files.id", ondelete="SET NULL"))
    change_notice_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nd_change_draft_files.id", ondelete="SET NULL"))
    warnings: Mapped[list | None] = mapped_column(JSONB)
    actions: Mapped[list | None] = mapped_column(JSONB)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    change_request: Mapped[NdChangeRequest] = relationship(back_populates="results")
