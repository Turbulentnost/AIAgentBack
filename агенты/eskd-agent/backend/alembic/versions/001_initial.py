"""Initial eskd-agent tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eskd_check_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column("designation", sa.String(length=128), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("pages_count", sa.Integer(), nullable=True),
        sa.Column("check_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("adapter", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_errors", sa.Integer(), nullable=False),
        sa.Column("total_warnings", sa.Integer(), nullable=False),
        sa.Column("raw_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("gost_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eskd_check_runs_job_id", "eskd_check_runs", ["job_id"])
    op.create_index("ix_eskd_check_runs_designation", "eskd_check_runs", ["designation"])
    op.create_index("ix_eskd_check_runs_file_sha256", "eskd_check_runs", ["file_sha256"])
    op.create_index("ix_eskd_check_runs_status", "eskd_check_runs", ["status"])

    op.create_table(
        "eskd_marking_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("designation", sa.String(length=128), nullable=True),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("pages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eskd_marking_documents_designation", "eskd_marking_documents", ["designation"])

    op.create_table(
        "eskd_marking_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("check_run_id", sa.Uuid(), nullable=True),
        sa.Column("is_rework", sa.Boolean(), nullable=False),
        sa.Column("document_level", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("page_level", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("problem_report", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["check_run_id"], ["eskd_check_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["eskd_marking_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eskd_marking_labels_document_id", "eskd_marking_labels", ["document_id"])
    op.create_index("ix_eskd_marking_labels_check_run_id", "eskd_marking_labels", ["check_run_id"])
    op.create_index("ix_eskd_marking_labels_is_rework", "eskd_marking_labels", ["is_rework"])


def downgrade() -> None:
    op.drop_table("eskd_marking_labels")
    op.drop_table("eskd_marking_documents")
    op.drop_table("eskd_check_runs")
