"""warehouse picker procurement agent catalog

Revision ID: 0056_warehouse_picker_agent
Revises: 0055_dispatcher_agent
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

revision = "0056_warehouse_picker_agent"
down_revision = "0055_dispatcher_agent"
branch_labels = None
depends_on = None

AGENT_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f61"
VERSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f62"
RUN_PERMISSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f63"
VIEW_PERMISSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f64"
SLUG = "warehouse_picker_agent"

OMTO_AGENT_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f71"
OMTO_VERSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f72"
OMTO_RUN_PERMISSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f73"
OMTO_VIEW_PERMISSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f74"
OMTO_SLUG = "omto_chief_agent"


def upgrade() -> None:
    op.execute(
        f"""
        DO $migration$
        BEGIN
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{RUN_PERMISSION_ID}', 'agents.{SLUG}.run',
           'Работа ИИ-агента по закупке',
           'Доступ к рабочему месту кладовщика-комплектовщика по заказам МУ №2'),
          ('{VIEW_PERMISSION_ID}', 'agents.{SLUG}.view_results',
           'Просмотр результатов ИИ-агента по закупке',
           'Просмотр заключений по складскому наличию'),
          ('{OMTO_RUN_PERMISSION_ID}', 'agents.{OMTO_SLUG}.run',
           'Работа агента начальника ОМТО',
           'Доступ к обработке заключений кладовщика-комплектовщика'),
          ('{OMTO_VIEW_PERMISSION_ID}', 'agents.{OMTO_SLUG}.view_results',
           'Просмотр результатов агента начальника ОМТО',
           'Просмотр кейсов после кладовщика-комплектовщика')
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name, description = EXCLUDED.description;

        INSERT INTO role_permissions (role_id, permission_id)
        SELECT roles.id, permissions.id
        FROM roles CROSS JOIN permissions
        WHERE roles.code IN ('admin', 'super_admin')
          AND permissions.code IN (
            'agents.{SLUG}.run',
            'agents.{SLUG}.view_results',
            'agents.{OMTO_SLUG}.run',
            'agents.{OMTO_SLUG}.view_results'
          )
        ON CONFLICT DO NOTHING;

        INSERT INTO agents (
          id, name, slug, purpose, status, input_schema, output_schema,
          department_id, owner_id
        )
        VALUES
        (
          '{AGENT_ID}',
          'ИИ-агент по закупке',
          '{SLUG}',
          'Проверяет наличие ТМЦ в кладовой монтажного участка №2 и формирует заключение для ОМТО.',
          'ACTIVE',
          '{{"case_id":"string","source_type":"production_material_order"}}'::jsonb,
          '{{"positions":"array","conclusion":"object","decision_kind":"string"}}'::jsonb,
          NULL, NULL
        ),
        (
          '{OMTO_AGENT_ID}',
          'ИИ-агент начальника ОМТО',
          '{OMTO_SLUG}',
          'Принимает заключение кладовщика-комплектовщика для дальнейшей закупки.',
          'TESTING',
          '{{"case_id":"string"}}'::jsonb,
          '{{"status":"string"}}'::jsonb,
          NULL, NULL
        )
        ON CONFLICT (slug) DO UPDATE
        SET name = EXCLUDED.name,
            purpose = EXCLUDED.purpose,
            status = EXCLUDED.status,
            input_schema = EXCLUDED.input_schema,
            output_schema = EXCLUDED.output_schema;

        INSERT INTO agent_versions (id, agent_id, version, config, changelog, is_current)
        VALUES
        (
          '{VERSION_ID}',
          (SELECT id FROM agents WHERE slug = '{SLUG}'),
          1,
          jsonb_build_object(
            'module', 'app.agents.warehouse_picker_agent.service',
            'read_only', true,
            'department_filter', 'Монтажный участок №2'
          ),
          'Проверка кладовой и заключение по наличию для МУ №2',
          true
        ),
        (
          '{OMTO_VERSION_ID}',
          (SELECT id FROM agents WHERE slug = '{OMTO_SLUG}'),
          1,
          jsonb_build_object(
            'module', 'app.agents.procurement_role_agents.service',
            'stub', true
          ),
          'Заготовка агента начальника ОМТО',
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
        WHERE agent_id IN (
          SELECT id FROM agents WHERE slug IN ('{SLUG}', '{OMTO_SLUG}')
        );
        DELETE FROM agents WHERE slug IN ('{SLUG}', '{OMTO_SLUG}');
        DELETE FROM role_permissions
        WHERE permission_id IN (
          SELECT id FROM permissions
          WHERE code IN (
            'agents.{SLUG}.run',
            'agents.{SLUG}.view_results',
            'agents.{OMTO_SLUG}.run',
            'agents.{OMTO_SLUG}.view_results'
          )
        );
        DELETE FROM permissions
        WHERE code IN (
          'agents.{SLUG}.run',
          'agents.{SLUG}.view_results',
          'agents.{OMTO_SLUG}.run',
          'agents.{OMTO_SLUG}.view_results'
        );
        END
        $migration$;
        """
    )
