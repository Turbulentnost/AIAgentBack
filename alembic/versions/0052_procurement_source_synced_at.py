"""add procurement case source synchronization timestamp

Revision ID: 0052_procurement_source_synced_at
Revises: 0051_procurement_role_waiting_status
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_procurement_source_synced_at"
down_revision = "0051_procurement_role_waiting_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "procurement_cases",
        sa.Column("source_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE procurement_cases
        SET source_synced_at = COALESCE(updated_at, created_at)
        WHERE source_synced_at IS NULL
        """
    )
    op.create_index(
        "ix_procurement_cases_source_synced_at",
        "procurement_cases",
        ["source_synced_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_procurement_cases_source_synced_at",
        table_name="procurement_cases",
    )
    op.drop_column("procurement_cases", "source_synced_at")
