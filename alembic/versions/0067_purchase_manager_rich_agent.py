"""upgrade purchase_manager_agent to rich procurement manager module

Revision ID: 0067_purchase_manager_rich_agent
Revises: 0066_warehouse_complex_chief_agent
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0067_purchase_manager_rich_agent"
down_revision = "0066_warehouse_complex_chief_agent"
branch_labels = None
depends_on = None

SLUG = "purchase_manager_agent"
APPROVE_PERMISSION_ID = "d9e3f604-ab5c-5f13-b04e-2c3d4e5f6075"


def upgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{APPROVE_PERMISSION_ID}',
           'agents.{SLUG}.approve',
           'Согласование закупочных действий',
           'Подтверждение контролируемых операций менеджера по закупкам')
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name, description = EXCLUDED.description;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT roles.id, permissions.id
        FROM roles CROSS JOIN permissions
        WHERE roles.code IN ('admin', 'super_admin')
          AND permissions.code IN (
            'agents.{SLUG}.run',
            'agents.{SLUG}.view_results',
            'agents.{SLUG}.approve'
          )
        ON CONFLICT DO NOTHING;

        UPDATE agents
        SET name = 'ИИ-агент менеджера по закупкам',
            purpose = 'Поиск поставщиков, RFQ, сравнение предложений и сопровождение поставки по заказам материалов.',
            status = 'ACTIVE',
            input_schema = '{{"case_id":"uuid","requested_operation":"string"}}'::jsonb,
            output_schema = '{{"lifecycle_state":"string","recommendation":"object"}}'::jsonb,
            updated_at = now()
        WHERE slug = '{SLUG}';

        UPDATE agent_versions
        SET config = COALESCE(config, '{{}}'::jsonb) || jsonb_build_object(
              'module', 'app.agents.procurement_role_agents.service',
              'role_module', 'app.agents.procurement_manager_agent.service',
              'read_first', true,
              'read_only', false,
              'payment_execution_allowed', false
            ),
            changelog = 'Полноценный менеджер по закупкам: поиск, оценка, PO/HITL',
            is_current = true
        WHERE agent_id IN (SELECT id FROM agents WHERE slug = '{SLUG}')
          AND is_current = true;
        END
        $migration$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE agent_versions
        SET config = COALESCE(config, '{{}}'::jsonb) || jsonb_build_object(
              'module', 'app.agents.procurement_role_agents.service',
              'read_only', true,
              'reconciliation_interval_seconds', 1800
            ) - 'role_module' - 'read_first' - 'payment_execution_allowed',
            changelog = 'Сверка заказов поставщику с заказами материалов'
        WHERE agent_id IN (SELECT id FROM agents WHERE slug = '{SLUG}')
          AND is_current = true;

        UPDATE agents
        SET purpose = 'Контролирует связанные заказы поставщику по заказам материалов в производство.',
            input_schema = '{{"case_id":"string","source_type":"production_material_order"}}'::jsonb,
            output_schema = '{{"supplier_orders":"array","positions":"array","coverage_status":"string"}}'::jsonb,
            updated_at = now()
        WHERE slug = '{SLUG}';

        DELETE FROM role_permissions
        WHERE permission_id IN (
          SELECT id FROM permissions WHERE code = 'agents.{SLUG}.approve'
        );
        DELETE FROM permissions WHERE code = 'agents.{SLUG}.approve';
        """
    )
