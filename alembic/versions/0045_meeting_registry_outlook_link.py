"""restore meeting registry outlook link revision marker

Revision ID: 0045_meeting_registry_outlook_link
Revises: 0043_nd_change_journal
Create Date: 2026-07-09

This revision is intentionally a no-op in this tree. The database already
contains this revision from a previous backend state; keeping the marker lets
Alembic resolve the current production revision without changing existing data.
"""

from __future__ import annotations


revision = "0045_meeting_registry_outlook_link"
down_revision = "0043_nd_change_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
