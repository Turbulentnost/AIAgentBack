"""Integration layer tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003_integration_layer"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_document_id", sa.String(length=256), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("designation", sa.String(length=128), nullable=True),
        sa.Column("document_type", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=512), nullable=True),
        sa.Column("revision", sa.String(length=64), nullable=True),
        sa.Column("sheet_count", sa.Integer(), nullable=True),
        sa.Column("author", sa.String(length=256), nullable=True),
        sa.Column("department", sa.String(length=256), nullable=True),
        sa.Column("product_id", sa.String(length=128), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("related_documents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("route_status", sa.String(length=64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_system",
            "external_document_id",
            "revision",
            name="uq_integration_documents_source_ext_rev",
        ),
    )
    op.create_index("ix_integration_documents_external_document_id", "integration_documents", ["external_document_id"])
    op.create_index("ix_integration_documents_source_system", "integration_documents", ["source_system"])
    op.create_index("ix_integration_documents_checksum", "integration_documents", ["checksum"])

    op.create_table(
        "integration_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("check_run_id", sa.Uuid(), nullable=True),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ruleset_version", sa.String(length=64), nullable=True),
        sa.Column("critical_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("major_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minor_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocks_workflow", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("result_status", sa.String(length=32), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("submitted_by", sa.String(length=256), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["check_run_id"], ["eskd_check_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["integration_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_integration_jobs_request_id"),
    )
    op.create_index("ix_integration_jobs_status", "integration_jobs", ["status"])

    op.create_table(
        "integration_exchange_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sender", sa.String(length=64), nullable=False),
        sa.Column("receiver", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("external_document_id", sa.String(length=256), nullable=True),
        sa.Column("designation", sa.String(length=128), nullable=True),
        sa.Column("revision", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actor", sa.String(length=256), nullable=True),
        sa.Column("payload_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["integration_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "integration_webhooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret", sa.String(length=256), nullable=True),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "integration_webhook_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("webhook_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["integration_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["webhook_id"], ["integration_webhooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "integration_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_integration_api_keys_key_hash"),
    )


def downgrade() -> None:
    op.drop_table("integration_api_keys")
    op.drop_table("integration_webhook_deliveries")
    op.drop_table("integration_webhooks")
    op.drop_table("integration_exchange_log")
    op.drop_table("integration_jobs")
    op.drop_table("integration_documents")
