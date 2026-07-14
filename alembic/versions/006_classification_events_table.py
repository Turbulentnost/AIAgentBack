"""classification_events — журнал смены отдела и спам-статуса для графиков и точности

Revision ID: 006
Revises: 005
Create Date: 2026-07-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "classification_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("email_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("old_department_id", sa.Text(), nullable=True),
        sa.Column("old_department_name", sa.Text(), nullable=True),
        sa.Column("new_department_id", sa.Text(), nullable=True),
        sa.Column("new_department_name", sa.Text(), nullable=True),
        sa.Column("old_is_spam", sa.Boolean(), nullable=True),
        sa.Column("new_is_spam", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False, server_default="agent"),
        sa.Column("source", sa.Text(), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["email_id"], ["email_messages.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_classification_events_created_at", "classification_events", ["created_at"])
    op.create_index("ix_classification_events_message_id", "classification_events", ["message_id"])
    op.create_index("ix_classification_events_category", "classification_events", ["category"])
    op.create_index("ix_classification_events_event_type", "classification_events", ["event_type"])
    op.create_index("ix_classification_events_actor", "classification_events", ["actor"])


def downgrade() -> None:
    op.drop_index("ix_classification_events_actor", table_name="classification_events")
    op.drop_index("ix_classification_events_event_type", table_name="classification_events")
    op.drop_index("ix_classification_events_category", table_name="classification_events")
    op.drop_index("ix_classification_events_message_id", table_name="classification_events")
    op.drop_index("ix_classification_events_created_at", table_name="classification_events")
    op.drop_table("classification_events")
