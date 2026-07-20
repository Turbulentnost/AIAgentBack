"""add external waiting status for procurement role agents

Revision ID: 0051_procurement_role_waiting_status
Revises: 0050_procurement_case_lifecycle
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op

revision = "0051_procurement_role_waiting_status"
down_revision = "0050_procurement_case_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'WAITING_EXTERNAL'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely while rows may use them.
    pass
