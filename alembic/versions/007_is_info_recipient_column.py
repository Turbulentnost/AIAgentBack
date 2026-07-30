"""is_info_recipient denormalized column for list/stats filters

Revision ID: 007
Revises: 006
Create Date: 2026-07-21

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_messages",
        sa.Column("is_info_recipient", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, mailbox, raw_payload_json FROM email_messages")
    ).fetchall()

    from agent_pochta.db.message_filters import compute_is_info_recipient

    for row_id, mailbox, raw_payload_json in rows:
        flag = compute_is_info_recipient(mailbox=mailbox, raw_payload_json=raw_payload_json)
        connection.execute(
            sa.text("UPDATE email_messages SET is_info_recipient = :flag WHERE id = :id"),
            {"flag": flag, "id": row_id},
        )

    op.create_index(
        "ix_email_messages_is_info_recipient_status",
        "email_messages",
        ["is_info_recipient", "status"],
    )
    op.create_index(
        "ix_email_messages_is_info_recipient_received_at",
        "email_messages",
        ["is_info_recipient", "received_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_messages_is_info_recipient_received_at", table_name="email_messages")
    op.drop_index("ix_email_messages_is_info_recipient_status", table_name="email_messages")
    op.drop_column("email_messages", "is_info_recipient")
