"""Turbo SMK foundation tables.

Revision ID: 0069_turbo_smk_modules
Revises: 0068_eskd_agent
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0069_turbo_smk_modules"
down_revision = "0068_eskd_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nd_development_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("document_kind", sa.String(length=32)),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("process_description", sa.Text()),
        sa.Column("process_owner", sa.String(length=255)),
        sa.Column("developer_department", sa.String(length=255)),
        sa.Column("interested_departments", postgresql.JSONB()),
        sa.Column("similar_documents", postgresql.JSONB()),
        sa.Column("scope", sa.Text()),
        sa.Column("target_effective_date", sa.Date()),
        sa.Column("needs_process_diagram", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("needs_introduction_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("needs_implementation_plan", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledgement_targets", postgresql.JSONB()),
        sa.Column("base_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("base_document_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_versions.id", ondelete="SET NULL")),
        sa.Column("version_reason", sa.Text()),
        sa.Column("duplicate_check_result", postgresql.JSONB()),
        sa.Column("package_completeness", postgresql.JSONB()),
        sa.Column("initiator_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("number", name="uq_nd_development_requests_number"),
    )
    op.create_table(
        "nd_acknowledgement_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_versions.id", ondelete="SET NULL")),
        sa.Column("change_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nd_change_requests.id", ondelete="SET NULL")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("document_code", sa.String(length=128)),
        sa.Column("document_name", sa.String(length=512)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("nd_acknowledgement_assignments")
    op.drop_table("nd_development_requests")
