"""Replace test OTK seed users."""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa

revision = "005_update_seed_users"
down_revision = "004_check_versioning_users"
branch_labels = None
depends_on = None

REMOVED_LOGINS = ("otk.ivanov", "otk.petrova")
NEW_USERS = [
    {
        "login": "otk.arsunov",
        "display_name": "Арсуноев Михаил",
        "role": "ESKD_OTK",
        "department": "ОТК",
    },
    {
        "login": "dev.razrabotchik",
        "display_name": "Разработчик",
        "role": "ESKD_OTK",
        "department": "Разработка",
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    for login in REMOVED_LOGINS:
        bind.execute(
            sa.text("UPDATE eskd_users SET is_active = false WHERE login = :login"),
            {"login": login},
        )

    users = sa.table(
        "eskd_users",
        sa.column("id", sa.Uuid()),
        sa.column("login", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("role", sa.String()),
        sa.column("department", sa.String()),
        sa.column("is_active", sa.Boolean()),
    )
    existing = {
        row[0]
        for row in bind.execute(sa.text("SELECT login FROM eskd_users")).fetchall()
    }
    to_insert = [
        {
            "id": uuid.uuid4(),
            **row,
            "is_active": True,
        }
        for row in NEW_USERS
        if row["login"] not in existing
    ]
    if to_insert:
        op.bulk_insert(users, to_insert)


def downgrade() -> None:
    bind = op.get_bind()
    for login in ("otk.arsunov", "dev.razrabotchik"):
        bind.execute(
            sa.text("UPDATE eskd_users SET is_active = false WHERE login = :login"),
            {"login": login},
        )
    for login in REMOVED_LOGINS:
        bind.execute(
            sa.text("UPDATE eskd_users SET is_active = true WHERE login = :login"),
            {"login": login},
        )
