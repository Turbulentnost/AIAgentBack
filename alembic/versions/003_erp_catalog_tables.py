"""erp catalog staging: contractors, departments, sync_runs

Revision ID: 003
Revises: 002
Create Date: 2025-06-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("started_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("contractors_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("departments_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "erp_contractors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="1c"),
        sa.Column("contractor_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("emails_json", sa.Text(), nullable=False),
        sa.Column("department_codes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("contractor_type", sa.Text(), nullable=False, server_default="клиент"),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("raw_payload_json", sa.Text(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "sync_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catalog_sync_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
        sa.UniqueConstraint("source", "contractor_id", name="uq_erp_contractors_source_id"),
    )

    op.create_table(
        "erp_departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="1c"),
        sa.Column("department_id", sa.Text(), nullable=False),
        sa.Column("department_name", sa.Text(), nullable=False),
        sa.Column("head_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("responsibility", sa.Text(), nullable=False, server_default=""),
        sa.Column("keywords_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("raw_payload_json", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "sync_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catalog_sync_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False),
        sa.UniqueConstraint("source", "department_id", name="uq_erp_departments_source_id"),
    )


def downgrade() -> None:
    op.drop_table("erp_departments")
    op.drop_table("erp_contractors")
    op.drop_table("catalog_sync_runs")
