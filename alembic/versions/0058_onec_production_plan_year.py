"""onec production plan year resolver

Revision ID: 0058_onec_production_plan_year
Revises: 0057_onec_production_plan_sync
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0058_onec_production_plan_year"
down_revision = "0057_onec_production_plan_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "onec_production_plan_headers",
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "onec_production_plan_headers",
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("uq_onec_prod_plan_line", "onec_production_plan_items", type_="unique")
    op.create_unique_constraint(
        "uq_onec_prod_plan_line",
        "onec_production_plan_items",
        ["plan_ref_key", "line_number", "nomenclature_key", "month_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_onec_prod_plan_line", "onec_production_plan_items", type_="unique")
    op.create_unique_constraint(
        "uq_onec_prod_plan_line",
        "onec_production_plan_items",
        ["plan_ref_key", "line_number", "nomenclature_key"],
    )
    op.drop_column("onec_production_plan_headers", "period_end")
    op.drop_column("onec_production_plan_headers", "period_start")
