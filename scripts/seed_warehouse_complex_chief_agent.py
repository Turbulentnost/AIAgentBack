"""Idempotent seed for warehouse_complex_chief_agent (when alembic head is mismatched)."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

AGENT_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f81"
VERSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f82"
RUN_PERMISSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f83"
VIEW_PERMISSION_ID = "c8d2e5f3-9a4b-5e02-af3d-1b2c3d4e5f84"
SLUG = "warehouse_complex_chief_agent"

SQL = f"""
DO $migration$
BEGIN
INSERT INTO permissions (id, code, name, description)
VALUES
  ('{RUN_PERMISSION_ID}', 'agents.{SLUG}.run',
   'Работа ИИ-агента по закупкам',
   'Доступ к рабочему месту начальника складского комплекса'),
  ('{VIEW_PERMISSION_ID}', 'agents.{SLUG}.view_results',
   'Просмотр результатов ИИ-агента по закупкам',
   'Просмотр заключений начальника складского комплекса')
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
VALUES
(
  '{AGENT_ID}',
  'ИИ-агент по закупкам (складской комплекс)',
  '{SLUG}',
  'Начальник складского комплекса: обрабатывает заказы материалов в производство (кроме МУ №2), проверяет наличие ТМЦ и формирует заключение для ОМТО.',
  'ACTIVE',
  '{{"case_id":"string","source_type":"production_material_order"}}'::jsonb,
  '{{"positions":"array","conclusion":"object","decision_kind":"string"}}'::jsonb,
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
    'module', 'app.agents.procurement_role_agents.service',
    'read_only', true,
    'role', 'Начальник складского комплекса',
    'department_scope', 'non_montage_section_2'
  ),
  'Рабочее место начальника складского комплекса на базе логики комплектовщика',
  true
)
ON CONFLICT (id) DO UPDATE
SET config = EXCLUDED.config,
    changelog = EXCLUDED.changelog,
    is_current = EXCLUDED.is_current;
END
$migration$;
"""


async def main() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text(SQL))
        await db.commit()
        row = await db.execute(
            text("SELECT slug, name, status FROM agents WHERE slug = :s"),
            {"s": SLUG},
        )
        print(row.fetchone())


if __name__ == "__main__":
    asyncio.run(main())
