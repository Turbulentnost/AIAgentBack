"""supplier order reconciliation and purchase manager agent

Revision ID: 0064_supplier_order_reconciliation
Revises: 0056_warehouse_picker_agent
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0064_supplier_order_reconciliation"
down_revision = "0056_warehouse_picker_agent"
branch_labels = None
depends_on = None

AGENT_ID = "d9e3f604-ab5c-5f13-b04e-2c3d4e5f6071"
VERSION_ID = "d9e3f604-ab5c-5f13-b04e-2c3d4e5f6072"
RUN_PERMISSION_ID = "d9e3f604-ab5c-5f13-b04e-2c3d4e5f6073"
VIEW_PERMISSION_ID = "d9e3f604-ab5c-5f13-b04e-2c3d4e5f6074"
SLUG = "purchase_manager_agent"


def upgrade() -> None:
    op.create_table(
        "procurement_supplier_order_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_order_1c_ref", sa.String(64), nullable=False),
        sa.Column("supplier_order_number", sa.String(128), nullable=True),
        sa.Column("order_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_status", sa.String(128), nullable=True),
        sa.Column("basis_1c_ref", sa.String(64), nullable=True),
        sa.Column("basis_type", sa.String(255), nullable=True),
        sa.Column("root_source_1c_ref", sa.String(64), nullable=True),
        sa.Column("root_source_type", sa.String(64), nullable=True),
        sa.Column("chain", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["case_id"], ["procurement_cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "supplier_order_1c_ref",
            name="uq_procurement_supplier_order_links_case_order",
        ),
    )
    for name, column in (
        ("ix_supplier_links_case_id", "case_id"),
        ("ix_supplier_links_order_ref", "supplier_order_1c_ref"),
        ("ix_supplier_links_order_number", "supplier_order_number"),
        ("ix_supplier_links_order_date", "order_date"),
        ("ix_supplier_links_order_status", "order_status"),
        ("ix_supplier_links_basis_ref", "basis_1c_ref"),
        ("ix_supplier_links_root_ref", "root_source_1c_ref"),
        ("ix_supplier_links_root_type", "root_source_type"),
        ("ix_supplier_links_last_seen", "last_seen_at"),
        ("ix_supplier_links_active", "active"),
    ):
        op.create_index(name, "procurement_supplier_order_links", [column])

    op.create_table(
        "procurement_supplier_order_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.String(128), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("nomenclature_id", sa.String(64), nullable=False),
        sa.Column("characteristic_id", sa.String(64), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["link_id"], ["procurement_supplier_order_links.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "link_id",
            "line_id",
            name="uq_procurement_supplier_order_lines_link_line",
        ),
    )
    op.create_index(
        "ix_supplier_order_lines_link_id",
        "procurement_supplier_order_lines",
        ["link_id"],
    )
    op.create_index(
        "ix_supplier_order_lines_nomenclature_id",
        "procurement_supplier_order_lines",
        ["nomenclature_id"],
    )

    op.execute(
        f"""
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{RUN_PERMISSION_ID}', 'agents.{SLUG}.run',
           'Работа ИИ-агента менеджера по закупкам',
           'Доступ к связанным заказам поставщику'),
          ('{VIEW_PERMISSION_ID}', 'agents.{SLUG}.view_results',
           'Просмотр ИИ-агента менеджера по закупкам',
           'Просмотр покрытия заказов материалов заказами поставщику')
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name, description = EXCLUDED.description;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT roles.id, permissions.id
        FROM roles CROSS JOIN permissions
        WHERE roles.code IN ('admin', 'super_admin')
          AND permissions.code IN (
            'agents.{SLUG}.run',
            'agents.{SLUG}.view_results'
          )
        ON CONFLICT DO NOTHING;

        INSERT INTO agents (
          id, name, slug, purpose, status, input_schema, output_schema,
          department_id, owner_id
        )
        VALUES (
          '{AGENT_ID}',
          'ИИ-агент менеджера по закупкам',
          '{SLUG}',
          'Контролирует связанные заказы поставщику по заказам материалов в производство.',
          'ACTIVE',
          '{{"case_id":"string","source_type":"production_material_order"}}'::jsonb,
          '{{"supplier_orders":"array","positions":"array","coverage_status":"string"}}'::jsonb,
          NULL, NULL
        )
        ON CONFLICT (slug) DO UPDATE
        SET name = EXCLUDED.name,
            purpose = EXCLUDED.purpose,
            status = EXCLUDED.status,
            input_schema = EXCLUDED.input_schema,
            output_schema = EXCLUDED.output_schema;

        INSERT INTO agent_versions (id, agent_id, version, config, changelog, is_current)
        VALUES (
          '{VERSION_ID}',
          (SELECT id FROM agents WHERE slug = '{SLUG}'),
          1,
          jsonb_build_object(
            'module', 'app.agents.procurement_role_agents.service',
            'read_only', true,
            'reconciliation_interval_seconds', 1800
          ),
          'Сверка заказов поставщику с заказами материалов',
          true
        )
        ON CONFLICT (id) DO UPDATE
        SET config = EXCLUDED.config,
            changelog = EXCLUDED.changelog,
            is_current = EXCLUDED.is_current;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM agent_versions
        WHERE agent_id = (SELECT id FROM agents WHERE slug = '{SLUG}');
        DELETE FROM agents WHERE slug = '{SLUG}';
        DELETE FROM role_permissions
        WHERE permission_id IN (
          SELECT id FROM permissions
          WHERE code IN ('agents.{SLUG}.run', 'agents.{SLUG}.view_results')
        );
        DELETE FROM permissions
        WHERE code IN ('agents.{SLUG}.run', 'agents.{SLUG}.view_results');
        """
    )
    op.drop_table("procurement_supplier_order_lines")
    op.drop_table("procurement_supplier_order_links")
