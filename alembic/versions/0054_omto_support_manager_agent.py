"""omto support manager agent catalog and permissions

Revision ID: 0054_omto_agent
Revises: 0053_engineer_agent
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op

revision = "0054_omto_agent"
down_revision = "0053_engineer_agent"
branch_labels = None
depends_on = None

AGENT_ID = "a480cfde-01c8-48bb-a5ca-a00100000021"
VERSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000022"
RUN_PERMISSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000023"
VIEW_PERMISSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000024"
SLUG = "omto_support_manager_agent"


def upgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{RUN_PERMISSION_ID}', 'agents.{SLUG}.run',
           'Работа агента менеджера ОМТО',
           'Доступ к рабочему месту сопровождения поставок и DATA_CHECK'),
          ('{VIEW_PERMISSION_ID}', 'agents.{SLUG}.view_results',
           'Просмотр результатов агента менеджера ОМТО',
           'Просмотр проверок качества данных по закупкам')
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
          'ИИ-агент менеджера по сопровождению ОМТО',
          '{SLUG}',
          'Проверяет обязательные поля поставки, формирует DATA_CHECK и запрос уточнения.',
          'TESTING',
          '{{"case_id":"string","fields":{{"cfo":"string","article":"string","project":"string","date":"string","nomenclature":"string","quantity":"number"}}}}'::jsonb,
          '{{"quality_status":"string","findings":"array","actions":"array","summary":"string"}}'::jsonb,
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
            'module', 'app.agents.omto_support_manager_agent.service',
            'read_only', false,
            'max_parallel_cases', 10
          ),
          'MVP: контроль обязательных полей ЦФО/статья/проект/дата/номенклатура/количество',
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
