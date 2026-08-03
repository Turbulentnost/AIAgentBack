"""production dispatcher agent catalog and activation

Revision ID: 0055_dispatcher_agent
Revises: 0060_legal_specialist_agent
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op

revision = "0055_dispatcher_agent"
down_revision = "0060_legal_specialist_agent"
branch_labels = None
depends_on = None

AGENT_ID = "b7c1d4e2-8f3a-4d91-9e2c-0a1b2c3d4e51"
VERSION_ID = "b7c1d4e2-8f3a-4d91-9e2c-0a1b2c3d4e52"
RUN_PERMISSION_ID = "b7c1d4e2-8f3a-4d91-9e2c-0a1b2c3d4e53"
VIEW_PERMISSION_ID = "b7c1d4e2-8f3a-4d91-9e2c-0a1b2c3d4e54"
SLUG = "production_dispatcher_agent"


def upgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{RUN_PERMISSION_ID}', 'agents.{SLUG}.run',
           'Работа агента диспетчера производства',
           'Доступ к read-only рабочему месту расчёта точек заказа и обеспечения'),
          ('{VIEW_PERMISSION_ID}', 'agents.{SLUG}.view_results',
           'Просмотр результатов агента диспетчера производства',
           'Просмотр расчётов по точкам заказа и кейсам после инженера')
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
          'ИИ-агент диспетчера производства',
          '{SLUG}',
          'Проверяет остатки по точкам заказа и кейсам после инженера, рассчитывает срочность и рекомендует способ обеспечения.',
          'ACTIVE',
          '{{"case_id":"string","source_type":"reorder_point|production_material_order"}}'::jsonb,
          '{{"positions":"array","decision_kind":"string","summary":"string"}}'::jsonb,
          NULL, NULL
        )
        ON CONFLICT (slug) DO UPDATE
        SET name = EXCLUDED.name,
            purpose = EXCLUDED.purpose,
            status = 'ACTIVE',
            input_schema = EXCLUDED.input_schema,
            output_schema = EXCLUDED.output_schema;

        INSERT INTO agent_versions (id, agent_id, version, config, changelog, is_current)
        VALUES (
          '{VERSION_ID}',
          (SELECT id FROM agents WHERE slug = '{SLUG}'),
          1,
          jsonb_build_object(
            'module', 'app.agents.production_dispatcher_agent.service',
            'read_only', true
          ),
          'Расчёт свободного остатка, min/max, К-т запаса и рекомендаций обеспечения',
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
