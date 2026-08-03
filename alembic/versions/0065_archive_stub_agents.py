"""archive non-functional catalog agents

Revision ID: 0065_archive_stub_agents
Revises: 0042_nd_template_document_metadata, 0049_procurement_orchestrator,
         20260722_protocol_draft
Create Date: 2026-07-27
"""

from __future__ import annotations

from alembic import op

revision = "0065_archive_stub_agents"
down_revision = (
    "0042_nd_template_document_metadata",
    "0049_procurement_orchestrator",
    "20260722_protocol_draft",
)
branch_labels = None
depends_on = None

STUB_AGENT_SLUGS = (
    "logistics_dispatcher_agent",
    "deputy_head_omto_agent",
    "quality_deputy_director_agent",
    "production_preparation_engineer_agent",
    "executive_director_agent",
    "quality_kpi_agent",
    "omto_chief_agent",
    "otk_head_agent",
    "finance_director_agent",
    "legal_specialist_agent",
)


def _quoted_slugs() -> str:
    return ", ".join(f"'{slug}'" for slug in STUB_AGENT_SLUGS)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE agents
        SET status = 'ARCHIVED', updated_at = now()
        WHERE slug IN ({_quoted_slugs()})
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE agents
        SET status = 'TESTING', updated_at = now()
        WHERE slug IN ({_quoted_slugs()})
        """
    )
