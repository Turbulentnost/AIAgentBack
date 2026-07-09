"""initial email_messages and email_attachments (ТЗ раздел 7)

Revision ID: 001
Revises:
Create Date: 2025-06-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", sa.Text(), nullable=False, unique=True),
        sa.Column("received_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("processed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("mailbox", sa.Text(), nullable=False),
        sa.Column("sender_email", sa.Text(), nullable=False),
        sa.Column("sender_name", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("is_spam", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("spam_confidence", sa.Float(), nullable=True),
        sa.Column("spam_reason", sa.Text(), nullable=True),
        sa.Column("contractor_id", sa.Text(), nullable=True),
        sa.Column("is_new_contractor", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("department_id", sa.Text(), nullable=True),
        sa.Column("department_name", sa.Text(), nullable=True),
        sa.Column("dept_confidence", sa.Float(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("summary_ru", sa.Text(), nullable=True),
        sa.Column("erp_document_number", sa.Text(), nullable=True),
        sa.Column("erp_task_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="processing"),
        sa.Column("human_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attachments_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("agent_version", sa.Text(), nullable=True),
    )
    op.create_table(
        "email_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("ocr_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("email_attachments")
    op.drop_table("email_messages")
