"""quality role agents + KPI agent catalog and permissions

Revision ID: 0055_quality_agents
Revises: 0054_omto_agent
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op

revision = "0055_quality_agents"
down_revision = "0054_omto_agent"
branch_labels = None
depends_on = None

AGENTS = (
    {
        "id": "a480cfde-01c8-48bb-a5ca-a00100000051",
        "version_id": "a480cfde-01c8-48bb-a5ca-a00100000052",
        "run_perm": "a480cfde-01c8-48bb-a5ca-a00100000053",
        "view_perm": "a480cfde-01c8-48bb-a5ca-a00100000054",
        "slug": "otk_head_agent",
        "name": "ИИ-агент начальника ОТК",
        "purpose": "Распределение предъявлений, проверка актов и контроль сроков входного контроля.",
        "module": "app.agents.otk_head_agent.service",
    },
    {
        "id": "a480cfde-01c8-48bb-a5ca-a00100000055",
        "version_id": "a480cfde-01c8-48bb-a5ca-a00100000056",
        "run_perm": "a480cfde-01c8-48bb-a5ca-a00100000057",
        "view_perm": "a480cfde-01c8-48bb-a5ca-a00100000058",
        "slug": "quality_engineer_agent",
        "name": "ИИ-агент инженера по качеству",
        "purpose": "Документарный и физический входной контроль, протоколы и акты несоответствия.",
        "module": "app.agents.quality_engineer_agent.service",
    },
    {
        "id": "a480cfde-01c8-48bb-a5ca-a00100000059",
        "version_id": "a480cfde-01c8-48bb-a5ca-a0010000005a",
        "run_perm": "a480cfde-01c8-48bb-a5ca-a0010000005b",
        "view_perm": "a480cfde-01c8-48bb-a5ca-a0010000005c",
        "slug": "quality_deputy_director_agent",
        "name": "ИИ-агент заместителя директора по качеству",
        "purpose": "Проект резолюции по несоответствующей партии и контроль маршрута исполнения.",
        "module": "app.agents.quality_deputy_director_agent.service",
    },
    {
        "id": "a480cfde-01c8-48bb-a5ca-a0010000005d",
        "version_id": "a480cfde-01c8-48bb-a5ca-a0010000005e",
        "run_perm": "a480cfde-01c8-48bb-a5ca-a0010000005f",
        "view_perm": "a480cfde-01c8-48bb-a5ca-a00100000060",
        "slug": "quality_kpi_agent",
        "name": "ИИ-агент качества (KPI)",
        "purpose": "Оценивает работу других ИИ-агентов и считает KPI по §12 ТЗ.",
        "module": "app.agents.quality_kpi_agent.service",
    },
)


def upgrade() -> None:
    for agent in AGENTS:
        slug = agent["slug"]
        op.execute(
            f"""
            DO $migration$
            BEGIN
            INSERT INTO permissions (id, code, name, description)
            VALUES
              ('{agent["run_perm"]}', 'agents.{slug}.run',
               'Работа агента {agent["name"]}',
               'Доступ к рабочему месту {slug}'),
              ('{agent["view_perm"]}', 'agents.{slug}.view_results',
               'Просмотр результатов {agent["name"]}',
               'Просмотр результатов {slug}')
            ON CONFLICT (code) DO UPDATE
            SET name = EXCLUDED.name, description = EXCLUDED.description;

            INSERT INTO role_permissions (role_id, permission_id)
            SELECT roles.id, permissions.id
            FROM roles CROSS JOIN permissions
            WHERE roles.code IN ('admin', 'super_admin')
              AND permissions.code IN (
                'agents.{slug}.run',
                'agents.{slug}.view_results'
              )
            ON CONFLICT DO NOTHING;

            INSERT INTO agents (
              id, name, slug, purpose, status, input_schema, output_schema,
              department_id, owner_id
            )
            VALUES (
              '{agent["id"]}',
              '{agent["name"]}',
              '{slug}',
              '{agent["purpose"]}',
              'TESTING',
              '{{"case_id":"string","quality":{{}}}}'::jsonb,
              '{{"summary":"string","actions":"array","next_status":"string"}}'::jsonb,
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
              '{agent["version_id"]}',
              (SELECT id FROM agents WHERE slug = '{slug}'),
              1,
              jsonb_build_object(
                'module', '{agent["module"]}',
                'read_only', false,
                'max_parallel_cases', 10
              ),
              'MVP: входной контроль / KPI §12',
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
    for agent in reversed(AGENTS):
        slug = agent["slug"]
        op.execute(
            f"""
            DO $migration$
            BEGIN
            DELETE FROM agent_versions
            WHERE agent_id IN (SELECT id FROM agents WHERE slug = '{slug}');
            DELETE FROM agents WHERE slug = '{slug}';
            DELETE FROM role_permissions
            WHERE permission_id IN (
              SELECT id FROM permissions
              WHERE code IN ('agents.{slug}.run', 'agents.{slug}.view_results')
            );
            DELETE FROM permissions
            WHERE code IN ('agents.{slug}.run', 'agents.{slug}.view_results');
            END
            $migration$;
            """
        )
