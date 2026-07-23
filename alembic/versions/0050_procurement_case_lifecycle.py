"""restore procurement case lifecycle revision marker

Revision ID: 0050_procurement_case_lifecycle
Revises: 0046_merge_platform_and_pochta_heads
Create Date: 2026-07-17

This revision is intentionally a no-op in this tree. The database already
contains this revision from a previous backend state; keeping the marker lets
Alembic resolve the current production revision without changing existing data.
"""

from __future__ import annotations

revision = "0050_procurement_case_lifecycle"
down_revision = "0046_merge_platform_and_pochta_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
