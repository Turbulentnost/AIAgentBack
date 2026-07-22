"""OMTO role agents: KPI runs table, catalog, permissions

Revision ID: 0061_omto_role_agents
Revises: 0054_activate_procurement_agents
Create Date: 2026-07-21

ВНИМАНИЕ (при вливании в main): down_revision должен указывать на ТЕКУЩИЙ head
alembic. На момент подготовки head в main = "0054_activate_procurement_agents".
Если к моменту вливания в main уже влиты миграции 0055–0060 (из других веток),
поставьте down_revision на актуальный head (см. `alembic heads`) либо выполните
`alembic merge heads`. Номер 0061 выбран, чтобы не пересекаться с 0054–0060.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0061_omto_role_agents"
down_revision = "0054_activate_procurement_agents"
branch_labels = None
depends_on = None

# Детерминированное пространство имён для стабильных UUID сидов.
_NS = uuid.UUID("a480cfde-01c8-48bb-a5ca-a00200000000")

# (slug, name паспорта, purpose)
OMTO_AGENTS = [
    (
        "procurement_manager_agent",
        "Агент менеджера по закупкам",
        "Ролевой агент контура №3: проверка спецификации, выбор поставщика, "
        "сравнение КП, проекты заказа/договора/претензии.",
    ),
    (
        "omto_head_agent",
        "Агент начальника ОМТО",
        "Ролевой агент-надзор контура №3: сквозной контроль КТ, проверка "
        "отклонений цены/поставки, эскалации и решения, ежедневный отчёт.",
    ),
    (
        "omto_deputy_agent",
        "Агент заместителя начальника ОМТО",
        "Ролевой агент контура №3: распределение заявок, контроль дублей, "
        "балансировка загрузки менеджеров, проект назначения.",
    ),
    (
        "kb_engineer_agent",
        "Агент инженера КБ / ГСПП",
        "Ролевой агент контура №3: техническое заключение по аналогу/замене, "
        "проверка актуальности КД, расчёт покрытия требований, оценка риска.",
    ),
    (
        "security_officer_agent",
        "Агент сотрудника службы безопасности",
        "Ролевой агент контура №3: проверка контрагента по критериям, оценка "
        "риска, вердикт допуска (согласование/условия/отказ).",
    ),
]


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, ":".join(parts)))


def upgrade() -> None:
    op.create_table(
        "agent_kpi_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_slug", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False, server_default="default"),
        sa.Column("task_type", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("role_status", sa.String(length=64), nullable=False),
        sa.Column("data_confidence", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("total_findings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("critical_findings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_with_source", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_references", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_percent", sa.Float(), nullable=True),
        sa.Column("verdict_emitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("thread_id", sa.String(length=128), nullable=True),
        sa.Column("hitl_pending", postgresql.JSONB(), nullable=True),
        sa.Column("triggered_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("output_data", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_kpi_runs_agent_slug", "agent_kpi_runs", ["agent_slug"])
    op.create_index("ix_agent_kpi_runs_correlation_id", "agent_kpi_runs", ["correlation_id"])
    op.create_index("ix_agent_kpi_runs_tenant_id", "agent_kpi_runs", ["tenant_id"])
    op.create_index("ix_agent_kpi_runs_status", "agent_kpi_runs", ["status"])
    op.create_index("ix_agent_kpi_runs_role_status", "agent_kpi_runs", ["role_status"])
    op.create_index("ix_agent_kpi_runs_thread_id", "agent_kpi_runs", ["thread_id"])

    for slug, name, purpose in OMTO_AGENTS:
        run_perm = _uid("perm", slug, "run")
        view_perm = _uid("perm", slug, "view")
        agent_id = _uid("agent", slug)
        version_id = _uid("version", slug)
        op.execute(
            sa.text(
                """
                INSERT INTO permissions (id, code, name, description)
                VALUES
                  (:run_id, :run_code, :run_name, :run_desc),
                  (:view_id, :view_code, :view_name, :view_desc)
                ON CONFLICT (code) DO UPDATE
                SET name = EXCLUDED.name, description = EXCLUDED.description;
                """
            ).bindparams(
                run_id=run_perm,
                run_code=f"agents.{slug}.run",
                run_name=f"Работа агента: {name}",
                run_desc=f"Запуск и рабочее место агента «{name}»",
                view_id=view_perm,
                view_code=f"agents.{slug}.view_results",
                view_name=f"Просмотр KPI: {name}",
                view_desc=f"Доступ к KPI-дашборду агента «{name}»",
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT roles.id, permissions.id
                FROM roles CROSS JOIN permissions
                WHERE roles.code IN ('admin', 'super_admin')
                  AND permissions.code IN (:run_code, :view_code)
                ON CONFLICT DO NOTHING;
                """
            ).bindparams(
                run_code=f"agents.{slug}.run",
                view_code=f"agents.{slug}.view_results",
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO agents (id, name, slug, purpose, status, input_schema, output_schema,
                                    department_id, owner_id)
                VALUES (:id, :name, :slug, :purpose, 'TESTING',
                        :input_schema, :output_schema, NULL, NULL)
                ON CONFLICT (slug) DO UPDATE
                SET name = EXCLUDED.name, purpose = EXCLUDED.purpose,
                    status = EXCLUDED.status,
                    input_schema = EXCLUDED.input_schema,
                    output_schema = EXCLUDED.output_schema;
                """
            ).bindparams(
                id=agent_id,
                name=name,
                slug=slug,
                purpose=purpose,
                input_schema='{"task_type":"string","task_payload":"object"}',
                output_schema='{"status":"string","role_status":"string","output_data":"object"}',
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO agent_versions (id, agent_id, version, config, changelog, is_current)
                VALUES (:version_id, (SELECT id FROM agents WHERE slug = :slug), 1,
                        :config, :changelog, true)
                ON CONFLICT (id) DO UPDATE
                SET config = EXCLUDED.config, changelog = EXCLUDED.changelog,
                    is_current = EXCLUDED.is_current;
                """
            ).bindparams(
                version_id=version_id,
                slug=slug,
                config=(
                    '{"module": "app.agents.omto_role_agents.service", '
                    '"engine": "langgraph", "read_only": true}'
                ),
                changelog="Адаптация LangGraph-агента ОМТО под платформу; KPI из истории запусков",
            )
        )


def downgrade() -> None:
    for slug, _name, _purpose in OMTO_AGENTS:
        run_code = f"agents.{slug}.run"
        view_code = f"agents.{slug}.view_results"
        op.execute(
            sa.text(
                "DELETE FROM agent_versions WHERE agent_id IN "
                "(SELECT id FROM agents WHERE slug = :slug);"
            ).bindparams(slug=slug)
        )
        op.execute(
            sa.text("DELETE FROM agents WHERE slug = :slug;").bindparams(slug=slug)
        )
        op.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE permission_id IN "
                "(SELECT id FROM permissions WHERE code IN (:run_code, :view_code));"
            ).bindparams(run_code=run_code, view_code=view_code)
        )
        op.execute(
            sa.text(
                "DELETE FROM permissions WHERE code IN (:run_code, :view_code);"
            ).bindparams(run_code=run_code, view_code=view_code)
        )
    op.drop_index("ix_agent_kpi_runs_thread_id", table_name="agent_kpi_runs")
    op.drop_index("ix_agent_kpi_runs_role_status", table_name="agent_kpi_runs")
    op.drop_index("ix_agent_kpi_runs_status", table_name="agent_kpi_runs")
    op.drop_index("ix_agent_kpi_runs_tenant_id", table_name="agent_kpi_runs")
    op.drop_index("ix_agent_kpi_runs_correlation_id", table_name="agent_kpi_runs")
    op.drop_index("ix_agent_kpi_runs_agent_slug", table_name="agent_kpi_runs")
    op.drop_table("agent_kpi_runs")
