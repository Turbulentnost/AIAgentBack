"""onec production plan sync

Revision ID: 0057_onec_production_plan_sync
Revises: 0056_aveon_shipment_schedule_versions
Create Date: 2026-08-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0057_onec_production_plan_sync"
down_revision = "0056_aveon_shipment_schedule_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onec_production_plan_headers",
        sa.Column("ref_key", sa.String(length=64), nullable=False),
        sa.Column("number", sa.String(length=64), nullable=False),
        sa.Column("plan_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted", sa.Boolean(), nullable=False),
        sa.Column("deletion_mark", sa.Boolean(), nullable=False),
        sa.Column("source_entity", sa.String(length=255), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ref_key", name="uq_onec_production_plan_headers_ref_key"),
    )
    op.create_index("ix_onec_production_plan_headers_date", "onec_production_plan_headers", ["plan_date"])
    op.create_index("ix_onec_production_plan_headers_id", "onec_production_plan_headers", ["id"])

    op.create_table(
        "onec_production_plan_sync_runs",
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("plan_ref_key", sa.String(length=64), nullable=False),
        sa.Column("plan_number", sa.String(length=64), nullable=False),
        sa.Column("plan_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_count", sa.Integer(), nullable=False),
        sa.Column("saved_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_onec_production_plan_sync_runs_id", "onec_production_plan_sync_runs", ["id"])

    op.create_table(
        "onec_production_plan_items",
        sa.Column("plan_ref_key", sa.String(length=64), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("product_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("month_key", sa.String(length=7), nullable=False),
        sa.Column("nomenclature_key", sa.String(length=64), nullable=False),
        sa.Column("nomenclature_code", sa.String(length=64), nullable=False),
        sa.Column("nomenclature_name", sa.Text(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=128), nullable=False),
        sa.Column("department", sa.Text(), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_ref_key"], ["onec_production_plan_headers.ref_key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_ref_key", "line_number", "nomenclature_key", name="uq_onec_prod_plan_line"),
    )
    op.create_index("ix_onec_production_plan_items_id", "onec_production_plan_items", ["id"])
    op.create_index("ix_onec_production_plan_items_plan", "onec_production_plan_items", ["plan_ref_key"])
    op.create_index("ix_onec_production_plan_items_nom", "onec_production_plan_items", ["nomenclature_key"])
    op.create_index("ix_onec_production_plan_items_month", "onec_production_plan_items", ["month_key"])


def downgrade() -> None:
    op.drop_index("ix_onec_production_plan_items_month", table_name="onec_production_plan_items")
    op.drop_index("ix_onec_production_plan_items_nom", table_name="onec_production_plan_items")
    op.drop_index("ix_onec_production_plan_items_plan", table_name="onec_production_plan_items")
    op.drop_index("ix_onec_production_plan_items_id", table_name="onec_production_plan_items")
    op.drop_table("onec_production_plan_items")

    op.drop_index("ix_onec_production_plan_sync_runs_id", table_name="onec_production_plan_sync_runs")
    op.drop_table("onec_production_plan_sync_runs")

    op.drop_index("ix_onec_production_plan_headers_id", table_name="onec_production_plan_headers")
    op.drop_index("ix_onec_production_plan_headers_date", table_name="onec_production_plan_headers")
    op.drop_table("onec_production_plan_headers")
