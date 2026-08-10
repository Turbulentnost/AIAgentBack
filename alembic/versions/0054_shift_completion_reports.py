"""shift completion reports

Revision ID: 0054_shift_completion_reports
Revises: 0053_engineer_agent
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0054_shift_completion_reports"
down_revision = "0053_engineer_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shift_completion_reports",
        sa.Column("manager_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("manager_name", sa.String(length=255), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("stats_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tasks_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("incomplete_reasons_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("email_sent_to", sa.String(length=512), nullable=False),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["manager_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manager_user_id",
            "report_date",
            name="uq_shift_completion_reports_manager_date",
        ),
        sa.UniqueConstraint(
            "manager_name",
            "report_date",
            name="uq_shift_completion_reports_manager_name_date",
        ),
    )
    op.create_index("ix_shift_completion_reports_id", "shift_completion_reports", ["id"])
    op.create_index(
        "ix_shift_completion_reports_manager_user_id",
        "shift_completion_reports",
        ["manager_user_id"],
    )
    op.create_index(
        "ix_shift_completion_reports_manager_name",
        "shift_completion_reports",
        ["manager_name"],
    )
    op.create_index(
        "ix_shift_completion_reports_report_date",
        "shift_completion_reports",
        ["report_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_shift_completion_reports_report_date", table_name="shift_completion_reports")
    op.drop_index("ix_shift_completion_reports_manager_name", table_name="shift_completion_reports")
    op.drop_index("ix_shift_completion_reports_manager_user_id", table_name="shift_completion_reports")
    op.drop_index("ix_shift_completion_reports_id", table_name="shift_completion_reports")
    op.drop_table("shift_completion_reports")
