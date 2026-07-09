"""raw_payload_json + erp_retry_count

Revision ID: 002
Revises: 001
Create Date: 2025-06-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("raw_payload_json", sa.Text(), nullable=True))
    op.add_column(
        "email_messages",
        sa.Column("erp_retry_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("email_messages", "erp_retry_count")
    op.drop_column("email_messages", "raw_payload_json")
