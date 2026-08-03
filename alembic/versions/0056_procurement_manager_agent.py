"""procurement manager catalog permissions

Revision ID: 0056_procurement_manager_agent
Revises: 20260722_protocol_draft
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

revision = "0056_procurement_manager_agent"
down_revision = "20260722_protocol_draft"
branch_labels = None
depends_on = None

SLUG = "procurement_logistics_agent"


def upgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('a480cfde-01c8-48bb-a5ca-a00100000061',
           'agents.{SLUG}.run', 'Работа агента менеджера по закупкам',
           'Доступ к рабочему месту закупок и логистики'),
          ('a480cfde-01c8-48bb-a5ca-a00100000062',
           'agents.{SLUG}.view_results', 'Просмотр закупочных кейсов',
           'Просмотр результатов закупочного агента'),
          ('a480cfde-01c8-48bb-a5ca-a00100000063',
           'agents.{SLUG}.approve', 'Согласование закупочных действий',
           'Подтверждение контролируемых операций закупочного агента')
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
        SET name = 'ИИ-агент менеджера по закупкам / ОМТО',
            purpose = 'Поиск поставщиков, RFQ, сравнение предложений и сопровождение поставки.',
            input_schema = '{{"case_id":"uuid","requested_operation":"string"}}'::jsonb,
            output_schema = '{{"lifecycle_state":"string","recommendation":"object"}}'::jsonb
        WHERE slug = '{SLUG}';

        UPDATE agent_versions
        SET config = COALESCE(config, '{{}}'::jsonb) || jsonb_build_object(
              'role_module', 'app.agents.procurement_manager_agent.service',
              'read_first', true,
              'payment_execution_allowed', false
            )
        WHERE agent_id IN (SELECT id FROM agents WHERE slug = '{SLUG}')
          AND is_current = true;
        END
        $migration$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM role_permissions
        WHERE permission_id IN (
          SELECT id FROM permissions WHERE code = 'agents.{SLUG}.approve'
        );
        DELETE FROM permissions WHERE code = 'agents.{SLUG}.approve';
        """
    )
