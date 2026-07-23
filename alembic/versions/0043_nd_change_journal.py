"""restore nd change journal revision marker

Revision ID: 0043_nd_change_journal
Revises: 004
Create Date: 2026-07-09

This revision is intentionally a no-op in this tree. The database already
contains this revision from a previous backend state; keeping the marker lets
Alembic resolve the current production revision without changing existing data.
"""

from __future__ import annotations

revision = "0043_nd_change_journal"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
