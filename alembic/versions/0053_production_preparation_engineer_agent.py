"""production preparation engineer agent catalog and permissions

Revision ID: 0053_engineer_agent
Revises: 0052_procurement_source_synced_at
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op

revision = "0053_engineer_agent"
down_revision = "0052_procurement_source_synced_at"
branch_labels = None
depends_on = None

AGENT_ID = "a480cfde-01c8-48bb-a5ca-a00100000011"
VERSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000012"
RUN_PERMISSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000013"
VIEW_PERMISSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000014"
SLUG = "production_preparation_engineer_agent"


def upgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{RUN_PERMISSION_ID}', 'agents.{SLUG}.run',
           'Работа агента инженера СПП',
           'Доступ к read-only рабочему месту расчёта потребности производства'),
          ('{VIEW_PERMISSION_ID}', 'agents.{SLUG}.view_results',
           'Просмотр результатов агента инженера СПП',
           'Просмотр расчётов по заказам материалов в производство')
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
          'ИИ-агент инженера по подготовке производства',
          '{SLUG}',
          'Проверяет ресурсные спецификации, рассчитывает потребность и подтверждённое обеспечение ТМЦ.',
          'TESTING',
          '{{"case_id":"string","source_type":"production_material_order"}}'::jsonb,
          '{{"positions":"array","validation_issues":"array","summary":"string"}}'::jsonb,
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
            'module', 'app.agents.production_preparation_engineer_agent.service',
            'read_only', true,
            'max_parallel_cases', 5
          ),
          'Детерминированный расчёт потребности, обеспеченности и дефицита по данным 1С',
          true
        )
        ON CONFLICT (id) DO UPDATE
        SET config = EXCLUDED.config,
            changelog = EXCLUDED.changelog,
            is_current = EXCLUDED.is_current;
        END
        $migration$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        DELETE FROM agent_versions
        WHERE agent_id IN (SELECT id FROM agents WHERE slug = '{SLUG}');
        DELETE FROM agents WHERE slug = '{SLUG}';
        DELETE FROM role_permissions
        WHERE permission_id IN (
          SELECT id FROM permissions
          WHERE code IN ('agents.{SLUG}.run', 'agents.{SLUG}.view_results')
        );
        DELETE FROM permissions
        WHERE code IN ('agents.{SLUG}.run', 'agents.{SLUG}.view_results');
        END
        $migration$;
        """
    )
