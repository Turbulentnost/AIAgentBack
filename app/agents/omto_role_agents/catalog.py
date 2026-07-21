"""Единый каталог ролевых агентов ОМТО (контур №3 «Закупка и выбор поставщика»).

Здесь — единственный источник правды для платформы: паспорт каждого агента
(slug, имя, должность-владелец, автономность, реестр), перечень поддерживаемых
типов задач и дескрипторы KPI. Slug строго совпадает в трёх местах:
``BaseAgent.agent_id`` (код) == ``Agent.slug`` (БД/миграция) == суффикс permission
``agents.<slug>.*``.

Значения KPI НЕ хранятся здесь — они вычисляются из реальной истории запусков
(``app.services.omto_kpi``). Дескриптор описывает только цель и способ получения
значения:

* ``metric`` — метрика, вычисляемая из истории запусков платформы (реальные данные);
* ``provider="onec"`` — KPI, требующий сверки с 1С/ОПЭ; до подключения интеграции
  значение отдаётся как ``null`` со статусом ``pending_integration`` (без фейков).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KpiGoal:
    """Машиночитаемая цель KPI для сравнения с вычисленным значением."""

    op: str  # ">=" | "<=" | "=="
    value: float
    unit: str  # "percent" | "count" | "number"


@dataclass(frozen=True)
class KpiDescriptor:
    id: str
    name: str
    target: str  # человекочитаемая цель из kpi.yaml (напр. "≥ 90%")
    goal: KpiGoal
    source: str  # откуда берётся значение (для подписи в дашборде)
    blocking: bool = False
    guardrail: bool = False
    # Способ получения значения:
    metric: str | None = None  # ключ метрики истории запусков (реальные данные)
    provider: str = "runs"  # "runs" | "onec"


@dataclass(frozen=True)
class OmtoAgentSpec:
    slug: str
    name: str
    name_full: str
    doc_ref: str
    registry_no: int
    position_role: str  # должность-владелец (owner.role)
    position_markers: tuple[str, ...]  # строки для сопоставления с User.position
    purpose: str
    contour: str
    autonomy_default: str
    task_types: tuple[str, ...]
    default_task_type: str
    kpi: tuple[KpiDescriptor, ...] = field(default_factory=tuple)


# --- переиспользуемые метрики истории запусков --------------------------------
# Эти метрики считаются из реальных записей ``agent_kpi_runs`` (см. omto_kpi.py).
_SOURCE_REF = "source_reference_rate"  # доля findings со ссылкой на источник, %
_AVG_COVERAGE = "avg_coverage"  # средний coverage из output_data, %
_VERDICTS_BELOW_COVERAGE = "verdicts_below_full_coverage"  # заключений при coverage<100, шт


def _pct(op: str, value: float) -> KpiGoal:
    return KpiGoal(op=op, value=value, unit="percent")


def _count(value: float = 0) -> KpiGoal:
    return KpiGoal(op="==", value=value, unit="count")


PROCUREMENT_MANAGER_AGENT = OmtoAgentSpec(
    slug="procurement_manager_agent",
    name="Агент менеджера по закупкам",
    name_full="ИИ-агент ведущего менеджера / менеджера по закупкам",
    doc_ref="ТЗ-АГТ-ЗАКУП-001",
    registry_no=17,
    position_role="Ведущий менеджер / менеджер по закупкам",
    position_markers=("менеджер по закупкам", "ведущий менеджер по закупкам"),
    purpose="Ролевой агент контура №3: проверка спецификации, выбор поставщика, "
    "сравнение КП, проекты заказа/договора/претензии.",
    contour="№3 — Закупка и выбор поставщика",
    autonomy_default="U1",
    task_types=(
        "validate_spec",
        "select_supplier",
        "prepare_rfq",
        "compare_quotes",
        "prepare_order",
        "prepare_claim",
    ),
    default_task_type="validate_spec",
    kpi=(
        KpiDescriptor("K1", "Доля закупок с требуемым числом сопоставимых предложений",
                      "≥ 90%", _pct(">=", 90), "quotes + comparison", provider="onec"),
        KpiDescriptor("K2", "Полнота сравнения цены, срока, качества, договора и рисков",
                      "100%", _pct(">=", 100), "comparison", blocking=True, provider="onec"),
        KpiDescriptor("K3", "Доля рекомендаций поставщика, подтверждённых человеком",
                      "≥ 90%", _pct(">=", 90), "журнал HITL", provider="onec"),
        KpiDescriptor("K4", "Критические ошибки в проекте заказа или договора",
                      "0", _count(0), "журнал HITL + ОПЭ", blocking=True, provider="onec"),
        KpiDescriptor("K5", "Своевременность проекта претензии",
                      "≥ 95%", _pct(">=", 95), "draft_claim + audit_logs", provider="onec"),
        KpiDescriptor("K6", "Среднее количество циклов уточнения спецификации",
                      "≤ 1", KpiGoal("<=", 1, "number"), "request_clarification", provider="onec"),
        KpiDescriptor("K7", "Доля выводов со ссылкой на источник и пункт",
                      "100%", _pct(">=", 100), "source_references", metric=_SOURCE_REF),
        KpiDescriptor("K8", "Заказы, размещённые без согласованного отклонения от КД",
                      "0", _count(0), "сверка kd_deviation с 1С", blocking=True, provider="onec"),
    ),
)

OMTO_HEAD_AGENT = OmtoAgentSpec(
    slug="omto_head_agent",
    name="Агент начальника ОМТО",
    name_full="ИИ-агент начальника отдела материально-технического обеспечения",
    doc_ref="ТЗ-АГТ-ОМТО-НАЧ-001",
    registry_no=15,
    position_role="Начальник ОМТО",
    position_markers=("начальник омто", "начальник отдела материально-технического"),
    purpose="Ролевой агент-надзор контура №3: сквозной контроль КТ, проверка "
    "отклонений цены/поставки, эскалации и решения, ежедневный отчёт.",
    contour="№3 — Закупка и выбор поставщика",
    autonomy_default="U1",
    task_types=(
        "periodic_control",
        "case_review",
        "price_deviation_review",
        "daily_report",
    ),
    default_task_type="daily_report",
    kpi=(
        KpiDescriptor("K1", "Своевременность распределения заявок",
                      "≥ 98%", _pct(">=", 98), "erp.read_procurement_cases", provider="onec"),
        KpiDescriptor("K2", "Доля просроченных закупочных задач",
                      "≤ 5%", _pct("<=", 5), "erp.read_procurement_cases", provider="onec"),
        KpiDescriptor("K3", "Пропущенные критические отклонения цены или поставки",
                      "0", _count(0), "сверка с 1С + журнал ОПЭ", blocking=True, provider="onec"),
        KpiDescriptor("K4", "Корректирующие действия, закрытые в срок",
                      "≥ 95%", _pct(">=", 95), "erp.write_decision + audit_logs", provider="onec"),
        KpiDescriptor("K5", "Доля выводов со ссылкой на источник и правило",
                      "100%", _pct(">=", 100), "source_references", metric=_SOURCE_REF),
        KpiDescriptor("K6", "Доля проектов решений, принятых без переработки",
                      "≥ 90%", _pct(">=", 90), "журнал HITL", provider="onec"),
    ),
)

OMTO_DEPUTY_AGENT = OmtoAgentSpec(
    slug="omto_deputy_agent",
    name="Агент заместителя начальника ОМТО",
    name_full="ИИ-агент заместителя начальника отдела МТО",
    doc_ref="ТЗ-АГТ-ОМТО-ЗАМ-001",
    registry_no=16,
    position_role="Заместитель начальника ОМТО",
    position_markers=(
        "заместитель начальника омто",
        "зам начальника омто",
        "заместитель начальника отдела материально-технического",
    ),
    purpose="Ролевой агент контура №3: распределение заявок по менеджерам, "
    "контроль дублей, балансировка загрузки, проект назначения.",
    contour="№3 — Закупка и выбор поставщика",
    autonomy_default="U1",
    task_types=("assign_positions", "queue_review", "rebalance_workload"),
    default_task_type="assign_positions",
    kpi=(
        KpiDescriptor("K1", "Точность назначения по специализации",
                      "≥ 98%", _pct(">=", 98), "журнал HITL + audit_logs", provider="onec"),
        KpiDescriptor("K2", "Позиции без ответственного сверх допустимого срока",
                      "0", _count(0), "erp.read_unassigned_queue", blocking=True, provider="onec"),
        KpiDescriptor("K3", "Повторные переназначения из-за ошибочного распределения",
                      "≤ 5%", _pct("<=", 5), "audit_logs", provider="onec"),
        KpiDescriptor("K4", "Отклонение загрузки менеджеров от среднего",
                      "≤ 20%", _pct("<=", 20), "erp.read_managers_workload", provider="onec"),
        KpiDescriptor("K5", "Дубли потребности, переданные в закупку",
                      "0", _count(0), "check_duplicates + сверка с 1С", blocking=True, provider="onec"),
        KpiDescriptor("K6", "Доля назначений с объяснимым обоснованием",
                      "100%", _pct(">=", 100), "source_references", metric=_SOURCE_REF),
    ),
)

KB_ENGINEER_AGENT = OmtoAgentSpec(
    slug="kb_engineer_agent",
    name="Агент инженера КБ / ГСПП",
    name_full="ИИ-агент инженера конструкторского бюро / ГСПП",
    doc_ref="ТЗ-АГТ-КБ-001",
    registry_no=19,
    position_role="Инженер КБ / ГСПП",
    position_markers=("инженер кб", "инженер гспп", "инженер конструкторского бюро"),
    purpose="Ролевой агент контура №3: техническое заключение по аналогу/замене, "
    "проверка актуальности КД, расчёт покрытия требований, оценка риска.",
    contour="№3 — Закупка и выбор поставщика",
    autonomy_default="U1",
    task_types=(
        "analog_review",
        "substitution_review",
        "kd_deviation_review",
        "spec_clarification",
    ),
    default_task_type="analog_review",
    kpi=(
        KpiDescriptor("K1", "Полнота технического заключения",
                      "100%", _pct(">=", 100), "comparison + coverage", blocking=True,
                      metric=_AVG_COVERAGE),
        KpiDescriptor("K2", "Критические ошибочные разрешения аналога или замены",
                      "0", _count(0), "сверка с входным контролем и производством",
                      blocking=True, provider="onec"),
        KpiDescriptor("K3", "Своевременность подготовки заключения",
                      "≥ 95%", _pct(">=", 95), "audit_logs", provider="onec"),
        KpiDescriptor("K4", "Доля заключений, возвращённых из-за неполноты",
                      "≤ 5%", _pct("<=", 5), "журнал HITL", provider="onec"),
        KpiDescriptor("K5", "Доля утверждений заключения со ссылкой на документ, пункт и версию",
                      "100%", _pct(">=", 100), "source_references", metric=_SOURCE_REF),
        KpiDescriptor("K6", "Заказы, размещённые без согласованного отклонения",
                      "0", _count(0), "сверка erp.write_deviation_approval с ЗаказПоставщику",
                      blocking=True, provider="onec"),
    ),
)

SECURITY_OFFICER_AGENT = OmtoAgentSpec(
    slug="security_officer_agent",
    name="Агент сотрудника службы безопасности",
    name_full="ИИ-агент сотрудника службы безопасности (проверка контрагента)",
    doc_ref="ТЗ-АГТ-СБ-001",
    registry_no=25,
    position_role="Сотрудник службы безопасности",
    position_markers=(
        "сотрудник службы безопасности",
        "служба безопасности",
        "специалист сб",
        "офицер сб",
    ),
    purpose="Ролевой агент контура №3: проверка контрагента по обязательным "
    "критериям, оценка риска, вердикт допуска (согласование/условия/отказ).",
    contour="№3 — Закупка и выбор поставщика",
    autonomy_default="U1",
    task_types=(
        "new_supplier_check",
        "periodic_recheck",
        "contract_check",
        "exception_check",
    ),
    default_task_type="new_supplier_check",
    kpi=(
        KpiDescriptor("K1", "Полнота проверки контрагента по обязательным критериям",
                      "100%", _pct(">=", 100), "map_to_criteria + coverage", blocking=True,
                      metric=_AVG_COVERAGE),
        KpiDescriptor("K2", "Пропущенные критические факторы риска",
                      "0", _count(0), "сверка с инцидентами + журнал ОПЭ", blocking=True,
                      provider="onec"),
        KpiDescriptor("K3", "Доля необоснованных блокировок безопасных контрагентов",
                      "≤ 5%", _pct("<=", 5), "журнал HITL", guardrail=True, provider="onec"),
        KpiDescriptor("K4", "Своевременность заключения",
                      "≥ 95%", _pct(">=", 95), "audit_logs", provider="onec"),
        KpiDescriptor("K5", "Доля факторов риска со ссылкой, датой обращения и статусом источника",
                      "100%", _pct(">=", 100), "source_references", metric=_SOURCE_REF),
        KpiDescriptor("K6", "Заключения, выданные при покрытии критериев < 100%",
                      "0", _count(0), "coverage + audit_logs", blocking=True,
                      metric=_VERDICTS_BELOW_COVERAGE),
    ),
)


OMTO_AGENTS: dict[str, OmtoAgentSpec] = {
    spec.slug: spec
    for spec in (
        PROCUREMENT_MANAGER_AGENT,
        OMTO_HEAD_AGENT,
        OMTO_DEPUTY_AGENT,
        KB_ENGINEER_AGENT,
        SECURITY_OFFICER_AGENT,
    )
}

OMTO_AGENT_SLUGS: tuple[str, ...] = tuple(OMTO_AGENTS.keys())
AGENT_VERSION = "1.0.0"


def get_spec(slug: str) -> OmtoAgentSpec | None:
    return OMTO_AGENTS.get(slug)


def graph_module_path(slug: str) -> str:
    """Путь к модулю графа агента (LangGraph-совместимый ``build_graph``)."""
    return f"app.agents.{slug}.graph"


__all__ = [
    "AGENT_VERSION",
    "KpiDescriptor",
    "KpiGoal",
    "OMTO_AGENTS",
    "OMTO_AGENT_SLUGS",
    "OmtoAgentSpec",
    "get_spec",
    "graph_module_path",
]
