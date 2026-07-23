"""restore protocol draft revision marker

Revision ID: 20260722_protocol_draft
Revises: 0055_quality_agents
Create Date: 2026-07-22

This revision is intentionally a no-op. The shared/dev database already has
``alembic_version = '20260722_protocol_draft'`` and the ``protocol_draft_*``
columns on ``meeting_registry_entries``, but the migration file was never
committed (``alembic/versions/*.py`` is gitignored). Keeping this marker lets
Alembic resolve the DB revision. It is placed after ``0055_quality_agents``
because that schema (quality agents, procurement sync columns, etc.) is already
present on the same DB — so treating this id as head is safe for upgrade.
"""

from __future__ import annotations

revision = "20260722_protocol_draft"
down_revision = "0055_quality_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
