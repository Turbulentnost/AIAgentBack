"""Add human verification fields to marking labels."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "006_marking_label_human_verified"
down_revision = "005_update_seed_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "eskd_marking_labels",
        sa.Column("human_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("eskd_marking_labels", sa.Column("verified_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("eskd_marking_labels", sa.Column("verified_by_login", sa.String(length=64), nullable=True))
    op.add_column("eskd_marking_labels", sa.Column("verified_by_name", sa.String(length=256), nullable=True))
    op.create_foreign_key(
        "fk_eskd_marking_labels_verified_by_user_id",
        "eskd_marking_labels",
        "eskd_users",
        ["verified_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_eskd_marking_labels_verified_by_user_id", "eskd_marking_labels", type_="foreignkey")
    op.drop_column("eskd_marking_labels", "verified_by_name")
    op.drop_column("eskd_marking_labels", "verified_by_login")
    op.drop_column("eskd_marking_labels", "verified_by_user_id")
    op.drop_column("eskd_marking_labels", "human_verified_at")
