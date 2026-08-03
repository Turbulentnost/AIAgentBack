"""Check run versioning and OTK users."""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_check_versioning_users"
down_revision = "003_integration_layer"
branch_labels = None
depends_on = None

TEST_USERS = [
    {
        "login": "otk.ivanov",
        "display_name": "Иванов Иван Иванович",
        "role": "ESKD_OTK",
        "department": "ОТК",
    },
    {
        "login": "otk.petrova",
        "display_name": "Петрова Анна Сергеевна",
        "role": "ESKD_OTK",
        "department": "ОТК",
    },
]


def upgrade() -> None:
    op.create_table(
        "eskd_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="ESKD_OTK"),
        sa.Column("department", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("login"),
    )
    op.create_index("ix_eskd_users_login", "eskd_users", ["login"])
    op.create_index("ix_eskd_users_role", "eskd_users", ["role"])

    op.add_column("eskd_check_runs", sa.Column("document_key", sa.String(length=128), nullable=True))
    op.add_column("eskd_check_runs", sa.Column("version_no", sa.Integer(), server_default="1", nullable=False))
    op.add_column("eskd_check_runs", sa.Column("parent_run_id", sa.Uuid(), nullable=True))
    op.add_column("eskd_check_runs", sa.Column("created_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("eskd_check_runs", sa.Column("created_by_login", sa.String(length=64), nullable=True))
    op.add_column("eskd_check_runs", sa.Column("created_by_name", sa.String(length=256), nullable=True))
    op.add_column("eskd_check_runs", sa.Column("verified_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("eskd_check_runs", sa.Column("verified_by_login", sa.String(length=64), nullable=True))
    op.add_column("eskd_check_runs", sa.Column("verified_by_name", sa.String(length=256), nullable=True))
    op.create_index("ix_eskd_check_runs_document_key", "eskd_check_runs", ["document_key"])
    op.create_index("ix_eskd_check_runs_parent_run_id", "eskd_check_runs", ["parent_run_id"])
    op.create_foreign_key(
        "fk_eskd_check_runs_parent_run_id",
        "eskd_check_runs",
        "eskd_check_runs",
        ["parent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_eskd_check_runs_created_by_user_id",
        "eskd_check_runs",
        "eskd_users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_eskd_check_runs_verified_by_user_id",
        "eskd_check_runs",
        "eskd_users",
        ["verified_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "eskd_check_run_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("changed_by_login", sa.String(length=64), nullable=True),
        sa.Column("changed_by_name", sa.String(length=256), nullable=True),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["eskd_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["eskd_check_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["eskd_check_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eskd_check_run_changes_run_id", "eskd_check_run_changes", ["run_id"])
    op.create_index("ix_eskd_check_run_changes_change_type", "eskd_check_run_changes", ["change_type"])

    users = sa.table(
        "eskd_users",
        sa.column("id", sa.Uuid()),
        sa.column("login", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("role", sa.String()),
        sa.column("department", sa.String()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        users,
        [
            {
                "id": uuid.uuid4(),
                **row,
                "is_active": True,
            }
            for row in TEST_USERS
        ],
    )


def downgrade() -> None:
    op.drop_table("eskd_check_run_changes")
    op.drop_constraint("fk_eskd_check_runs_verified_by_user_id", "eskd_check_runs", type_="foreignkey")
    op.drop_constraint("fk_eskd_check_runs_created_by_user_id", "eskd_check_runs", type_="foreignkey")
    op.drop_constraint("fk_eskd_check_runs_parent_run_id", "eskd_check_runs", type_="foreignkey")
    op.drop_index("ix_eskd_check_runs_parent_run_id", table_name="eskd_check_runs")
    op.drop_index("ix_eskd_check_runs_document_key", table_name="eskd_check_runs")
    op.drop_column("eskd_check_runs", "verified_by_name")
    op.drop_column("eskd_check_runs", "verified_by_login")
    op.drop_column("eskd_check_runs", "verified_by_user_id")
    op.drop_column("eskd_check_runs", "created_by_name")
    op.drop_column("eskd_check_runs", "created_by_login")
    op.drop_column("eskd_check_runs", "created_by_user_id")
    op.drop_column("eskd_check_runs", "parent_run_id")
    op.drop_column("eskd_check_runs", "version_no")
    op.drop_column("eskd_check_runs", "document_key")
    op.drop_table("eskd_users")
