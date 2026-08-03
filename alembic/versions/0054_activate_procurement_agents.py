"""activate procurement agents

Revision ID: 0054_activate_procurement_agents
Revises: 0053_engineer_agent
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op

revision = "0054_activate_procurement_agents"
down_revision = "0053_engineer_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE agents
        SET status = 'ACTIVE'
        WHERE slug IN (
            'procurement_logistics_agent',
            'production_preparation_engineer_agent'
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE agents
        SET status = 'TESTING'
        WHERE slug IN (
            'procurement_logistics_agent',
            'production_preparation_engineer_agent'
        )
        """
    )
