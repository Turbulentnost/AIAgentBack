"""executive director agent catalog and permissions

Revision ID: 0057_executive_director_agent
Revises: 0056_finance_director_agent
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op

revision = "0057_executive_director_agent"
down_revision = "0056_finance_director_agent"
branch_labels = None
depends_on = None

AGENT_ID = "a480cfde-01c8-48bb-a5ca-a00100000041"
VERSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000042"
RUN_PERMISSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000043"
VIEW_PERMISSION_ID = "a480cfde-01c8-48bb-a5ca-a00100000044"
SLUG = "executive_director_agent"


def upgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{RUN_PERMISSION_ID}', 'agents.{SLUG}.run',
           'Работа агента исполнительного директора',
           'Доступ к рабочему месту утверждения реестра оплат'),
          ('{VIEW_PERMISSION_ID}', 'agents.{SLUG}.view_results',
           'Просмотр результатов агента исполнительного директора',
           'Просмотр решений approve/return по реестру оплат')
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
          'ИИ-агент исполнительного директора',
          '{SLUG}',
          'Утверждение реестра оплат при согласованиях ЦФО: рекомендация approve/return.',
          'TESTING',
          '{{"case_id":"string","case_context":{{"registry_id":"string","registry_lines":"array"}}}}'::jsonb,
          '{{"role_status":"string","suggested_action":"string","output_data":"object"}}'::jsonb,
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
            'module', 'app.agents.executive_director_agent.service',
            'contour', 'contour4',
            'read_only', false
          ),
          'Каркас роли исполнительного директора контура 4 на платформе',
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
