"""ESKD agent catalog entry and permissions

Revision ID: 0065_eskd_agent
Revises: 0064_supplier_order_reconciliation
Create Date: 2026-07-27
"""

from __future__ import annotations

from alembic import op

revision = "0065_eskd_agent"
down_revision = "0064_supplier_order_reconciliation"
branch_labels = None
depends_on = None

AGENT_ID = "b1e2f604-ab5c-5f13-b04e-2c3d4e5f6081"
VERSION_ID = "b1e2f604-ab5c-5f13-b04e-2c3d4e5f6082"
RUN_PERM = "b1e2f604-ab5c-5f13-b04e-2c3d4e5f6083"
VIEW_PERM = "b1e2f604-ab5c-5f13-b04e-2c3d4e5f6084"
SLUG = "eskd_agent"
NAME = "ESKD Agent"
PURPOSE = (
    "Проверка конструкторской документации по ЕСКД: анализ чертежей, разметка, "
    "база знаний и журнал интеграций."
)


def upgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{RUN_PERM}', 'agents.{SLUG}.run',
           'Работа агента {NAME}',
           'Доступ к рабочему месту {SLUG}'),
          ('{VIEW_PERM}', 'agents.{SLUG}.view_results',
           'Просмотр результатов {NAME}',
           'Просмотр результатов {SLUG}')
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name, description = EXCLUDED.description;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT roles.id, permissions.id
        FROM roles CROSS JOIN permissions
        WHERE roles.code IN ('admin', 'super_admin', 'employee')
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
          '{NAME}',
          '{SLUG}',
          '{PURPOSE}',
          'active',
          '{{"files":"array","designation":"string"}}'::jsonb,
          '{{"items":"array","summary":"string"}}'::jsonb,
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
            'module', 'features.eskd',
            'read_only', false,
            'max_parallel_cases', 5
          ),
          'MVP: проверка КД по ЕСКД',
          true
        )
        ON CONFLICT (id) DO NOTHING;
        END
        $migration$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM agent_versions WHERE id = '{VERSION_ID}';
        DELETE FROM agents WHERE slug = '{SLUG}';
        DELETE FROM permissions WHERE code IN ('agents.{SLUG}.run', 'agents.{SLUG}.view_results');
        """
    )
