"""aveon shipment schedule versions

Revision ID: 0056_aveon_shipment_schedule_versions
Revises: 0055_developer_feedback_chat
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0056_aveon_shipment_schedule_versions"
down_revision = "0055_developer_feedback_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aveon_shipment_schedule_versions",
        sa.Column("country_scope", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_base64", sa.Text(), nullable=False),
        sa.Column("preview_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("stats_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("changed_cells_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_reason", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "country_scope",
            "file_sha256",
            name="uq_aveon_shipment_schedule_versions_country_hash",
        ),
    )
    op.create_index("ix_aveon_shipment_schedule_versions_id", "aveon_shipment_schedule_versions", ["id"])
    op.create_index(
        "ix_aveon_shipment_schedule_versions_country_scope",
        "aveon_shipment_schedule_versions",
        ["country_scope"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_versions_source_type",
        "aveon_shipment_schedule_versions",
        ["source_type"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_versions_file_sha256",
        "aveon_shipment_schedule_versions",
        ["file_sha256"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_versions_is_active",
        "aveon_shipment_schedule_versions",
        ["is_active"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_versions_created_by_user_id",
        "aveon_shipment_schedule_versions",
        ["created_by_user_id"],
    )

    op.create_table(
        "aveon_shipment_schedule_change_events",
        sa.Column("schedule_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("next_schedule_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("manager_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("manager_name", sa.String(length=255), nullable=True),
        sa.Column("task_key", sa.String(length=600), nullable=True),
        sa.Column("task_type", sa.String(length=255), nullable=True),
        sa.Column("nomenclature", sa.String(length=1024), nullable=False),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("supplier", sa.String(length=512), nullable=True),
        sa.Column("original_dates_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("add_batches_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("manager_result", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["next_schedule_version_id"],
            ["aveon_shipment_schedule_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_version_id"],
            ["aveon_shipment_schedule_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["manager_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_aveon_shipment_schedule_change_events_idempotency_key",
        ),
    )
    op.create_index("ix_aveon_shipment_schedule_change_events_id", "aveon_shipment_schedule_change_events", ["id"])
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_schedule_version_id",
        "aveon_shipment_schedule_change_events",
        ["schedule_version_id"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_next_schedule_version_id",
        "aveon_shipment_schedule_change_events",
        ["next_schedule_version_id"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_manager_user_id",
        "aveon_shipment_schedule_change_events",
        ["manager_user_id"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_manager_name",
        "aveon_shipment_schedule_change_events",
        ["manager_name"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_task_key",
        "aveon_shipment_schedule_change_events",
        ["task_key"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_nomenclature",
        "aveon_shipment_schedule_change_events",
        ["nomenclature"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_country",
        "aveon_shipment_schedule_change_events",
        ["country"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_status",
        "aveon_shipment_schedule_change_events",
        ["status"],
    )
    op.create_index(
        "ix_aveon_shipment_schedule_change_events_idempotency_key",
        "aveon_shipment_schedule_change_events",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_aveon_shipment_schedule_change_events_idempotency_key", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_status", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_country", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_nomenclature", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_task_key", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_manager_name", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_manager_user_id", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_next_schedule_version_id", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_schedule_version_id", table_name="aveon_shipment_schedule_change_events")
    op.drop_index("ix_aveon_shipment_schedule_change_events_id", table_name="aveon_shipment_schedule_change_events")
    op.drop_table("aveon_shipment_schedule_change_events")

    op.drop_index("ix_aveon_shipment_schedule_versions_created_by_user_id", table_name="aveon_shipment_schedule_versions")
    op.drop_index("ix_aveon_shipment_schedule_versions_is_active", table_name="aveon_shipment_schedule_versions")
    op.drop_index("ix_aveon_shipment_schedule_versions_file_sha256", table_name="aveon_shipment_schedule_versions")
    op.drop_index("ix_aveon_shipment_schedule_versions_source_type", table_name="aveon_shipment_schedule_versions")
    op.drop_index("ix_aveon_shipment_schedule_versions_country_scope", table_name="aveon_shipment_schedule_versions")
    op.drop_index("ix_aveon_shipment_schedule_versions_id", table_name="aveon_shipment_schedule_versions")
    op.drop_table("aveon_shipment_schedule_versions")
