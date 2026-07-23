"""stub for already-applied legal specialist agent revision

Revision ID: 0060_legal_specialist_agent
Revises: 0054_activate_procurement_agents
Create Date: 2026-07-21

The database already contains this revision; the original migration file
was missing from the workspace. This stub restores a linear history so
later migrations can upgrade cleanly.
"""

from __future__ import annotations

revision = "0060_legal_specialist_agent"
down_revision = "0054_activate_procurement_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Already applied in the target database.
    return


def downgrade() -> None:
    return
