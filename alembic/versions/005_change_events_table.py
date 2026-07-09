"""change_events audit table for HITL statistics

Revision ID: 005
Revises: 004
Create Date: 2026-07-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "change_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("email_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False, server_default="operator"),
        sa.Column("source", sa.Text(), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["email_id"], ["email_messages.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_change_events_created_at", "change_events", ["created_at"])
    op.create_index("ix_change_events_message_id", "change_events", ["message_id"])
    op.create_index("ix_change_events_event_type", "change_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_change_events_event_type", table_name="change_events")
    op.drop_index("ix_change_events_message_id", table_name="change_events")
    op.drop_index("ix_change_events_created_at", table_name="change_events")
    op.drop_table("change_events")
