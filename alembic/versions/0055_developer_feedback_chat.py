"""developer feedback chat

Revision ID: 0055_developer_feedback_chat
Revises: 0054_shift_completion_reports
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0055_developer_feedback_chat"
down_revision = "0054_shift_completion_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "developer_feedback_threads",
        sa.Column("agent_slug", sa.String(length=128), nullable=False),
        sa.Column("participant_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_name", sa.String(length=255), nullable=False),
        sa.Column("participant_email", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("participant_last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("developer_last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["participant_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_slug",
            "participant_user_id",
            name="uq_developer_feedback_threads_agent_participant",
        ),
    )
    op.create_index("ix_developer_feedback_threads_id", "developer_feedback_threads", ["id"])
    op.create_index("ix_developer_feedback_threads_agent_slug", "developer_feedback_threads", ["agent_slug"])
    op.create_index(
        "ix_developer_feedback_threads_participant_user_id",
        "developer_feedback_threads",
        ["participant_user_id"],
    )
    op.create_index(
        "ix_developer_feedback_threads_participant_name",
        "developer_feedback_threads",
        ["participant_name"],
    )
    op.create_index(
        "ix_developer_feedback_threads_participant_email",
        "developer_feedback_threads",
        ["participant_email"],
    )
    op.create_index("ix_developer_feedback_threads_status", "developer_feedback_threads", ["status"])
    op.create_index(
        "ix_developer_feedback_threads_last_message_at",
        "developer_feedback_threads",
        ["last_message_at"],
    )

    op.create_table(
        "developer_feedback_messages",
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author_role", sa.String(length=32), nullable=False),
        sa.Column("author_name", sa.String(length=255), nullable=False),
        sa.Column("author_email", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["developer_feedback_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_developer_feedback_messages_id", "developer_feedback_messages", ["id"])
    op.create_index("ix_developer_feedback_messages_thread_id", "developer_feedback_messages", ["thread_id"])
    op.create_index(
        "ix_developer_feedback_messages_author_user_id",
        "developer_feedback_messages",
        ["author_user_id"],
    )
    op.create_index("ix_developer_feedback_messages_author_role", "developer_feedback_messages", ["author_role"])
    op.create_index("ix_developer_feedback_messages_author_email", "developer_feedback_messages", ["author_email"])

    op.create_table(
        "developer_feedback_attachments",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["developer_feedback_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_path"),
    )
    op.create_index("ix_developer_feedback_attachments_id", "developer_feedback_attachments", ["id"])
    op.create_index(
        "ix_developer_feedback_attachments_message_id",
        "developer_feedback_attachments",
        ["message_id"],
    )
    op.create_index("ix_developer_feedback_attachments_checksum", "developer_feedback_attachments", ["checksum"])
    op.create_index("ix_developer_feedback_attachments_storage_path", "developer_feedback_attachments", ["storage_path"])


def downgrade() -> None:
    op.drop_index("ix_developer_feedback_attachments_storage_path", table_name="developer_feedback_attachments")
    op.drop_index("ix_developer_feedback_attachments_checksum", table_name="developer_feedback_attachments")
    op.drop_index("ix_developer_feedback_attachments_message_id", table_name="developer_feedback_attachments")
    op.drop_index("ix_developer_feedback_attachments_id", table_name="developer_feedback_attachments")
    op.drop_table("developer_feedback_attachments")

    op.drop_index("ix_developer_feedback_messages_author_email", table_name="developer_feedback_messages")
    op.drop_index("ix_developer_feedback_messages_author_role", table_name="developer_feedback_messages")
    op.drop_index("ix_developer_feedback_messages_author_user_id", table_name="developer_feedback_messages")
    op.drop_index("ix_developer_feedback_messages_thread_id", table_name="developer_feedback_messages")
    op.drop_index("ix_developer_feedback_messages_id", table_name="developer_feedback_messages")
    op.drop_table("developer_feedback_messages")

    op.drop_index("ix_developer_feedback_threads_last_message_at", table_name="developer_feedback_threads")
    op.drop_index("ix_developer_feedback_threads_status", table_name="developer_feedback_threads")
    op.drop_index("ix_developer_feedback_threads_participant_email", table_name="developer_feedback_threads")
    op.drop_index("ix_developer_feedback_threads_participant_name", table_name="developer_feedback_threads")
    op.drop_index("ix_developer_feedback_threads_participant_user_id", table_name="developer_feedback_threads")
    op.drop_index("ix_developer_feedback_threads_agent_slug", table_name="developer_feedback_threads")
    op.drop_index("ix_developer_feedback_threads_id", table_name="developer_feedback_threads")
    op.drop_table("developer_feedback_threads")
