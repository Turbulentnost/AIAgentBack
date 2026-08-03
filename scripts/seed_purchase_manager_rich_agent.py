"""Apply purchase_manager rich-agent catalog update when alembic history is skewed."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

SLUG = "purchase_manager_agent"
APPROVE_PERMISSION_ID = "d9e3f604-ab5c-5f13-b04e-2c3d4e5f6075"


async def main() -> None:
    statements = [
        f"""
        INSERT INTO permissions (id, code, name, description)
        VALUES
          ('{APPROVE_PERMISSION_ID}',
           'agents.{SLUG}.approve',
           'Согласование закупочных действий',
           'Подтверждение контролируемых операций менеджера по закупкам')
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name, description = EXCLUDED.description
        """,
        f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT roles.id, permissions.id
        FROM roles CROSS JOIN permissions
        WHERE roles.code IN ('admin', 'super_admin')
          AND permissions.code IN (
            'agents.{SLUG}.run',
            'agents.{SLUG}.view_results',
            'agents.{SLUG}.approve'
          )
        ON CONFLICT DO NOTHING
        """,
        f"""
        UPDATE agents
        SET name = 'ИИ-агент менеджера по закупкам',
            purpose = 'Поиск поставщиков, RFQ, сравнение предложений и сопровождение поставки по заказам материалов.',
            status = 'ACTIVE',
            input_schema = '{{"case_id":"uuid","requested_operation":"string"}}'::jsonb,
            output_schema = '{{"lifecycle_state":"string","recommendation":"object"}}'::jsonb,
            updated_at = now()
        WHERE slug = '{SLUG}'
        """,
        f"""
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
          AND is_current = true
        """,
    ]
    async with AsyncSessionLocal() as db:
        for statement in statements:
            await db.execute(text(statement))
        await db.commit()
        row = await db.execute(
            text(
                """
                SELECT a.slug, a.status, av.config->>'role_module' AS role_module
                FROM agents a
                JOIN agent_versions av ON av.agent_id = a.id AND av.is_current
                WHERE a.slug = :slug
                """
            ),
            {"slug": SLUG},
        )
        print("seeded", row.first())


if __name__ == "__main__":
    asyncio.run(main())
