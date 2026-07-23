"""§12 KPI formulas — common, role-special, system quality metrics.

Tone rules (СТО-10-095 / ТЗ §12):
- нет измеренной выборки → unknown (на UI — жёлтый «нет данных»);
- строгое целевое 100% / 0: любое отклонение → bad (красный);
- остальные: недобор до 5 п.п. → warn (жёлтый), сильнее → bad (красный).
"""

from __future__ import annotations

from typing import Any

from app.agents.quality_kpi_agent.schemas import KpiMetric

# Targets from ТЗ §12.1 / §12.2 / §12.3
TARGET_ACCURACY = 95.0
TARGET_COMPLETENESS = 98.0
TARGET_TIMELINESS = 95.0
TARGET_REWORK_PILOT = 10.0
TARGET_TRACEABILITY = 100.0
TARGET_CRITICAL = 0.0
TARGET_INCOMING_SLA = 95.0
TARGET_LOTS_WITHOUT_STATUS = 0.0
TARGET_CONTROL_TRACEABILITY = 100.0
TARGET_MANDATORY_DATA = 95.0
TARGET_PROCUREMENT_SLA = 95.0
TARGET_RECEIPT_SLA = 98.0
WARN_BAND_PP = 5.0


def _tone(
    value: float | None,
    target: float | None,
    *,
    higher_is_better: bool = True,
    sample_size: int = 0,
) -> str:
    """Map KPI value to ok / warn / bad / unknown."""
    if sample_size <= 0 or value is None or target is None:
        return "unknown"

    strict = target in {0.0, 100.0}
    if higher_is_better:
        if value >= target:
            return "ok"
        if not strict and value >= target - WARN_BAND_PP:
            return "warn"
        return "bad"

    if value <= target:
        return "ok"
    if not strict and value <= target + WARN_BAND_PP:
        return "warn"
    return "bad"


def _pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 2)


def _metric(
    *,
    id: str,
    title: str,
    formula: str,
    value: float | None,
    target: float,
    target_label: str,
    sample_size: int,
    higher_is_better: bool = True,
    unit: str = "%",
    details: dict[str, Any] | None = None,
) -> KpiMetric:
    return KpiMetric(
        id=id,
        title=title,
        formula=formula,
        value=value,
        target=target,
        target_label=target_label,
        unit=unit,
        tone=_tone(
            value,
            target,
            higher_is_better=higher_is_better,
            sample_size=sample_size,
        ),
        sample_size=sample_size,
        details=details or {},
    )


def compute_common_kpis(tasks: list[dict[str, Any]]) -> list[KpiMetric]:
    """§12.1 common KPIs for one agent from completed/checked tasks."""
    checked = [t for t in tasks if t.get("checked")]
    sample = len(checked)
    if sample <= 0:
        return [
            _metric(
                id="accuracy",
                title="Точность результата",
                formula="confirmed_without_material_error / checked × 100%",
                value=None,
                target=TARGET_ACCURACY,
                target_label="≥ 95%",
                sample_size=0,
            ),
            _metric(
                id="completeness",
                title="Полнота",
                formula="filled_mandatory / required × 100%",
                value=None,
                target=TARGET_COMPLETENESS,
                target_label="≥ 98%",
                sample_size=0,
            ),
            _metric(
                id="timeliness",
                title="Своевременность",
                formula="prepared_in_SLA / completed × 100%",
                value=None,
                target=TARGET_TIMELINESS,
                target_label="≥ 95%",
                sample_size=0,
            ),
            _metric(
                id="rework",
                title="Доля существенных доработок",
                formula="substantially_reworked / checked × 100%",
                value=None,
                target=TARGET_REWORK_PILOT,
                target_label="≤ 10% (пилот)",
                sample_size=0,
                higher_is_better=False,
            ),
            _metric(
                id="traceability",
                title="Прослеживаемость",
                formula="results_with_refs / all × 100%",
                value=None,
                target=TARGET_TRACEABILITY,
                target_label="100%",
                sample_size=0,
            ),
            _metric(
                id="critical",
                title="Критические / несанкционированные действия",
                formula="count",
                value=None,
                target=TARGET_CRITICAL,
                target_label="0",
                sample_size=0,
                higher_is_better=False,
                unit="",
            ),
        ]

    confirmed = sum(1 for t in checked if t.get("confirmed_without_material_error") is True)
    complete = sum(1 for t in checked if t.get("completeness_ok") is True)
    timely = sum(1 for t in checked if t.get("sla_met") is True)
    reworked = sum(1 for t in checked if t.get("substantially_reworked") is True)
    traced = sum(1 for t in checked if t.get("traceability_ok") is True)
    critical = sum(1 for t in checked if t.get("critical_unauthorized") is True)
    denom = float(sample)

    return [
        _metric(
            id="accuracy",
            title="Точность результата",
            formula="confirmed_without_material_error / checked × 100%",
            value=_pct(confirmed, denom),
            target=TARGET_ACCURACY,
            target_label="≥ 95%",
            sample_size=sample,
            details={"confirmed": confirmed, "checked": sample},
        ),
        _metric(
            id="completeness",
            title="Полнота",
            formula="filled_mandatory / required × 100%",
            value=_pct(complete, denom),
            target=TARGET_COMPLETENESS,
            target_label="≥ 98%",
            sample_size=sample,
            details={"complete": complete, "checked": sample},
        ),
        _metric(
            id="timeliness",
            title="Своевременность",
            formula="prepared_in_SLA / completed × 100%",
            value=_pct(timely, denom),
            target=TARGET_TIMELINESS,
            target_label="≥ 95%",
            sample_size=sample,
            details={"timely": timely, "checked": sample},
        ),
        _metric(
            id="rework",
            title="Доля существенных доработок",
            formula="substantially_reworked / checked × 100%",
            value=_pct(reworked, denom),
            target=TARGET_REWORK_PILOT,
            target_label="≤ 10% (пилот)",
            sample_size=sample,
            higher_is_better=False,
            details={"reworked": reworked, "checked": sample},
        ),
        _metric(
            id="traceability",
            title="Прослеживаемость",
            formula="results_with_refs / all × 100%",
            value=_pct(traced, denom),
            target=TARGET_TRACEABILITY,
            target_label="100%",
            sample_size=sample,
            details={"traced": traced, "checked": sample},
        ),
        _metric(
            id="critical",
            title="Критические / несанкционированные действия",
            formula="count",
            value=float(critical),
            target=TARGET_CRITICAL,
            target_label="0",
            sample_size=sample,
            higher_is_better=False,
            unit="",
            details={"critical": critical},
        ),
    ]


# Специальные KPI §12.2 — для всех ролевых агентов контура закупки/качества.
SPECIAL_KPI_SPECS: dict[str, list[dict[str, Any]]] = {
    "otk_head_agent": [
        {
            "id": "otk_assign_2wh",
            "title": "Назначение ≤ 2 раб. ч. (СТО-10-095)",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "assigned_within_2wh",
        },
        {
            "id": "otk_confirm_1wh",
            "title": "Подтверждение акта ≤ 1 раб. ч. (СТО-10-095)",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "act_confirmed_within_1wh",
        },
        {
            "id": "otk_zdk_1600",
            "title": "Акты к ЗДК до 16:00 (СТО-10-095)",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "handed_to_zdk_by_1600",
        },
        {
            "id": "otk_missed_critical",
            "title": "Пропущенные критические нарушения",
            "target": 0.0,
            "target_label": "0",
            "flag": "missed_critical",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "otk_quarterly_report",
            "title": "Полнота квартального отчёта / плана КД",
            "target": 100.0,
            "target_label": "100%",
            "flag": "quarterly_report_complete",
        },
    ],
    "quality_engineer_agent": [
        {
            "id": "qi_docs",
            "title": "Полнота обязательных docs",
            "target": 100.0,
            "target_label": "100%",
            "flag": "docs_complete",
        },
        {
            "id": "qi_program",
            "title": "Точность программы / выборки",
            "target": 100.0,
            "target_label": "100%",
            "flag": "program_ok",
        },
        {
            "id": "qi_results",
            "title": "Полнота записи результатов",
            "target": 100.0,
            "target_label": "100%",
            "flag": "results_complete",
        },
        {
            "id": "qi_false_release",
            "title": "Ложные разрешающие статусы",
            "target": 0.0,
            "target_label": "0",
            "flag": "false_releasing_status",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "qi_timely_act",
            "title": "Своевременность акта / ярлыка",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "act_label_timely",
        },
        {
            "id": "qi_recontrol_link",
            "title": "Повторные контроли со связью с актом",
            "target": 100.0,
            "target_label": "100%",
            "flag": "recontrol_linked",
        },
    ],
    "quality_deputy_director_agent": [
        {
            "id": "zdk_8wh",
            "title": "Резолюция ≤ 8 раб. ч. (СТО-10-095)",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "resolution_within_8wh",
        },
        {
            "id": "zdk_allowlist",
            "title": "Резолюция в допустимом перечне",
            "target": 100.0,
            "target_label": "100%",
            "flag": "disposition_allowed",
        },
        {
            "id": "zdk_conditions",
            "title": "Полнота условий исполнения",
            "target": 100.0,
            "target_label": "100%",
            "flag": "conditions_complete",
        },
        {
            "id": "zdk_contradictory",
            "title": "Противоречивые / неисполнимые",
            "target": 0.0,
            "target_label": "0",
            "flag": "contradictory_resolution",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
    "omto_support_manager_agent": [
        {
            "id": "omto_status_accuracy",
            "title": "Точность статусов счёта / отгрузки",
            "target": 98.0,
            "target_label": "≥ 98%",
            "flag": "status_accuracy_ok",
        },
        {
            "id": "omto_missed_dates",
            "title": "Пропущенные контрольные даты",
            "target": 0.0,
            "target_label": "0",
            "flag": "missed_control_date",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "omto_docs_pack",
            "title": "Полнота комплекта для транспорта / ОТК",
            "target": 98.0,
            "target_label": "≥ 98%",
            "flag": "docs_pack_complete",
        },
        {
            "id": "omto_delivery_forecast",
            "title": "Точность прогноза даты поставки",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "delivery_forecast_ok",
        },
    ],
    "production_preparation_engineer_agent": [
        {
            "id": "ppe_bom_coverage",
            "title": "Охват позиций ресурсной спецификации",
            "target": 99.0,
            "target_label": "≥ 99%",
            "flag": "bom_coverage_ok",
        },
        {
            "id": "ppe_need_accuracy",
            "title": "Точность валовой / чистой потребности",
            "target": 98.0,
            "target_label": "≥ 98%",
            "flag": "need_calc_ok",
        },
        {
            "id": "ppe_material_order",
            "title": "Полнота заказа материалов",
            "target": 98.0,
            "target_label": "≥ 98%",
            "flag": "material_order_complete",
        },
        {
            "id": "ppe_missed_critical",
            "title": "Пропущенные критические позиции",
            "target": 0.0,
            "target_label": "0",
            "flag": "missed_critical_position",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
    "production_dispatcher_agent": [
        {
            "id": "pd_minmax_accuracy",
            "title": "Точность МИН / МАКС / точки заказа",
            "target": 98.0,
            "target_label": "≥ 98%",
            "flag": "minmax_ok",
        },
        {
            "id": "pd_replenish_timely",
            "title": "Своевременность сигнала пополнения",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "replenish_signal_timely",
        },
        {
            "id": "pd_duplicate_signal",
            "title": "Дубли сигнала при действующем заказе",
            "target": 0.0,
            "target_label": "0",
            "flag": "duplicate_replenish_signal",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "pd_missed_rop",
            "title": "Критические дефициты из-за пропущенной ТЗК",
            "target": 0.0,
            "target_label": "0",
            "flag": "missed_rop_deficit",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
    "department_initiator_agent": [
        {
            "id": "di_requisites",
            "title": "Заявки с полным набором реквизитов",
            "target": 98.0,
            "target_label": "≥ 98%",
            "flag": "requisites_complete",
        },
        {
            "id": "di_route_accuracy",
            "title": "Точность классификации основания / маршрута",
            "target": 98.0,
            "target_label": "≥ 98%",
            "flag": "route_classified_ok",
        },
        {
            "id": "di_clarify_cycles",
            "title": "Среднее число циклов уточнения ≤ 1",
            "target": 100.0,
            "target_label": "≤ 1 цикл",
            "flag": "clarify_cycles_ok",
        },
        {
            "id": "di_no_basis",
            "title": "Заявки без подтверждённого основания",
            "target": 0.0,
            "target_label": "0",
            "flag": "request_without_basis",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
    "warehouse_manager_agent": [
        {
            "id": "wh_task_accuracy",
            "title": "Точность распределения складских задач",
            "target": 98.0,
            "target_label": "≥ 98%",
            "flag": "warehouse_task_ok",
        },
        {
            "id": "wh_defect_zone",
            "title": "Контроль зоны брака / недоступного остатка",
            "target": 100.0,
            "target_label": "100%",
            "flag": "defect_zone_ok",
        },
        {
            "id": "wh_receipt_without_docs",
            "title": "Оприходование без полного комплекта docs",
            "target": 0.0,
            "target_label": "0",
            "flag": "receipt_without_docs",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "wh_overdue_tasks",
            "title": "Доля просроченных складских заданий",
            "target": 5.0,
            "target_label": "≤ 5%",
            "flag": "warehouse_task_overdue",
            "higher_is_better": False,
        },
    ],
    "procurement_logistics_agent": [
        {
            "id": "proc_quotes",
            "title": "Закупки с сопоставимыми предложениями",
            "target": 90.0,
            "target_label": "≥ 90%",
            "flag": "comparable_quotes_ok",
        },
        {
            "id": "proc_compare_complete",
            "title": "Полнота сравнения цены / срока / рисков",
            "target": 100.0,
            "target_label": "100%",
            "flag": "comparison_complete",
        },
        {
            "id": "proc_supplier_confirmed",
            "title": "Рекомендации поставщика, подтверждённые человеком",
            "target": 90.0,
            "target_label": "≥ 90%",
            "flag": "supplier_confirmed",
        },
        {
            "id": "proc_critical_order_errors",
            "title": "Критические ошибки в проекте заказа",
            "target": 0.0,
            "target_label": "0",
            "flag": "critical_order_error",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
    "finance_director_agent": [
        {
            "id": "fd_budget_check",
            "title": "Точность проверки бюджета / лимита",
            "target": 100.0,
            "target_label": "100%",
            "flag": "budget_check_ok",
        },
        {
            "id": "fd_exception_justified",
            "title": "Исключения с полным обоснованием",
            "target": 100.0,
            "target_label": "100%",
            "flag": "exception_justified",
        },
        {
            "id": "fd_timely",
            "title": "Своевременность финансового решения",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "finance_decision_timely",
        },
        {
            "id": "fd_over_limit",
            "title": "Согласования за пределами полномочий",
            "target": 0.0,
            "target_label": "0",
            "flag": "over_limit_approval",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
    "executive_director_agent": [
        {
            "id": "ed_payment_basis",
            "title": "Полнота проверки основания платежа",
            "target": 100.0,
            "target_label": "100%",
            "flag": "payment_basis_ok",
        },
        {
            "id": "ed_timely",
            "title": "Своевременность проекта резолюции",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "resolution_timely",
        },
        {
            "id": "ed_returned",
            "title": "Резолюции, возвращённые без основания",
            "target": 5.0,
            "target_label": "≤ 5%",
            "flag": "resolution_returned",
            "higher_is_better": False,
        },
        {
            "id": "ed_illegal_exception",
            "title": "Неразрешённые исключения",
            "target": 0.0,
            "target_label": "0",
            "flag": "illegal_exception",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
    "chief_accountant_agent": [
        {
            "id": "ca_missed_errors",
            "title": "Пропущенные критические бух. ошибки",
            "target": 0.0,
            "target_label": "0",
            "flag": "missed_accounting_error",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "ca_docs_check",
            "title": "Полнота проверки реквизитов / первички",
            "target": 99.0,
            "target_label": "≥ 99%",
            "flag": "accounting_docs_ok",
        },
        {
            "id": "ca_timely",
            "title": "Своевременность бухгалтерского заключения",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "accounting_opinion_timely",
        },
        {
            "id": "ca_corrections",
            "title": "Заключения, исправленные после контроля",
            "target": 2.0,
            "target_label": "≤ 2%",
            "flag": "accounting_corrected",
            "higher_is_better": False,
        },
    ],
    "accountant_agent": [
        {
            "id": "acc_payment_status",
            "title": "Точность статуса платежа / взаиморасчётов",
            "target": 100.0,
            "target_label": "100%",
            "flag": "payment_status_ok",
        },
        {
            "id": "acc_without_approval",
            "title": "Платежи без полного согласования",
            "target": 0.0,
            "target_label": "0",
            "flag": "payment_without_approval",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "acc_primary_docs",
            "title": "Полнота регистрации первичных документов",
            "target": 99.0,
            "target_label": "≥ 99%",
            "flag": "primary_docs_ok",
        },
        {
            "id": "acc_discrepancy_timely",
            "title": "Расхождения, обработанные в срок",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "discrepancy_handled_timely",
        },
    ],
    "legal_specialist_agent": [
        {
            "id": "legal_terms",
            "title": "Полнота проверки существенных условий",
            "target": 100.0,
            "target_label": "100%",
            "flag": "contract_terms_ok",
        },
        {
            "id": "legal_missed_risk",
            "title": "Пропущенные критические юр. риски",
            "target": 0.0,
            "target_label": "0",
            "flag": "missed_legal_risk",
            "higher_is_better": False,
            "as_count": True,
        },
        {
            "id": "legal_accepted",
            "title": "Редакции, принятые без существенной переработки",
            "target": 90.0,
            "target_label": "≥ 90%",
            "flag": "legal_edit_accepted",
        },
        {
            "id": "legal_timely",
            "title": "Своевременность юридического заключения",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "legal_opinion_timely",
        },
    ],
    "cfo_head_agent": [
        {
            "id": "cfo_cfo_article",
            "title": "Точность ЦФО и статьи расходов",
            "target": 100.0,
            "target_label": "100%",
            "flag": "cfo_article_ok",
        },
        {
            "id": "cfo_priority",
            "title": "Решения с обоснованием приоритета",
            "target": 100.0,
            "target_label": "100%",
            "flag": "priority_justified",
        },
        {
            "id": "cfo_timely",
            "title": "Своевременность проекта согласования",
            "target": 95.0,
            "target_label": "≥ 95%",
            "flag": "approval_timely",
        },
        {
            "id": "cfo_budget_conflict",
            "title": "Решения против утверждённого бюджета",
            "target": 0.0,
            "target_label": "0",
            "flag": "budget_conflict",
            "higher_is_better": False,
            "as_count": True,
        },
    ],
}


def compute_special_kpis(agent_id: str, tasks: list[dict[str, Any]]) -> list[KpiMetric]:
    specs = SPECIAL_KPI_SPECS.get(agent_id) or []
    checked = [t for t in tasks if t.get("checked")]
    sample = len(checked)
    metrics: list[KpiMetric] = []
    for spec in specs:
        flag = spec["flag"]
        as_count = bool(spec.get("as_count"))
        higher = bool(spec.get("higher_is_better", True))
        if sample <= 0:
            value = None
        elif as_count:
            value = float(sum(1 for t in checked if t.get(flag) is True))
        else:
            known = [t for t in checked if t.get(flag) is not None]
            if not known:
                value = None
            else:
                hits = sum(1 for t in known if t.get(flag) is True)
                value = _pct(hits, float(len(known)))
        metrics.append(
            _metric(
                id=spec["id"],
                title=spec["title"],
                formula=flag,
                value=value,
                target=float(spec["target"]),
                target_label=str(spec["target_label"]),
                sample_size=sample if not as_count else sample,
                higher_is_better=higher,
                unit="" if as_count else "%",
            )
        )
    return metrics


def _case_flag_ratio(cases: list[dict[str, Any]], flag: str) -> tuple[float | None, int]:
    known = [c for c in cases if c.get(flag) is not None]
    if not known:
        return None, 0
    hits = sum(1 for c in known if c.get(flag) is True)
    return _pct(hits, float(len(known))), len(known)


def compute_system_quality_kpis(cases: list[dict[str, Any]]) -> list[KpiMetric]:
    """§12.3 quality-relevant + сквозные KPI системы закупки."""
    sla_value, sla_n = _case_flag_ratio(cases, "incoming_control_sla_met")
    traced_value, traced_n = _case_flag_ratio(cases, "control_traceability_ok")
    mandatory_value, mandatory_n = _case_flag_ratio(cases, "mandatory_data_ok")
    proc_sla_value, proc_sla_n = _case_flag_ratio(cases, "procurement_sla_met")
    receipt_value, receipt_n = _case_flag_ratio(cases, "receipt_sla_met")

    without_known = [c for c in cases if c.get("available_without_releasing_status") is not None]
    without_status = (
        float(sum(1 for c in without_known if c.get("available_without_releasing_status") is True))
        if without_known
        else None
    )
    without_basis_known = [c for c in cases if c.get("purchase_without_basis") is not None]
    without_basis = (
        float(sum(1 for c in without_basis_known if c.get("purchase_without_basis") is True))
        if without_basis_known
        else None
    )
    hx_known = [c for c in cases if c.get("hx_action_by_ai") is not None]
    hx_actions = (
        float(sum(1 for c in hx_known if c.get("hx_action_by_ai") is True)) if hx_known else None
    )

    return [
        _metric(
            id="mandatory_data",
            title="Полнота обязательных данных",
            formula="filled_mandatory / required × 100%",
            value=mandatory_value,
            target=TARGET_MANDATORY_DATA,
            target_label="≥ 95%",
            sample_size=mandatory_n,
        ),
        _metric(
            id="purchases_without_basis",
            title="Закупки без основания",
            formula="count",
            value=without_basis,
            target=0.0,
            target_label="0",
            sample_size=len(without_basis_known),
            higher_is_better=False,
            unit="",
        ),
        _metric(
            id="procurement_sla",
            title="Соблюдение SLA обработки заявки",
            formula="in_SLA / completed × 100%",
            value=proc_sla_value,
            target=TARGET_PROCUREMENT_SLA,
            target_label="≥ 95%",
            sample_size=proc_sla_n,
        ),
        _metric(
            id="receipt_sla",
            title="Своевременное оприходование",
            formula="on_time / lots × 100%",
            value=receipt_value,
            target=TARGET_RECEIPT_SLA,
            target_label="≥ 98%",
            sample_size=receipt_n,
        ),
        _metric(
            id="hx_ai_actions",
            title="Действия ИИ классов H/X",
            formula="count",
            value=hx_actions,
            target=0.0,
            target_label="0",
            sample_size=len(hx_known),
            higher_is_better=False,
            unit="",
        ),
        _metric(
            id="incoming_control_sla",
            title="Соблюдение SLA входного контроля (СТО-10-095)",
            formula="in_SLA / completed × 100%",
            value=sla_value,
            target=TARGET_INCOMING_SLA,
            target_label="≥ 95%",
            sample_size=sla_n,
        ),
        _metric(
            id="lots_without_releasing_status",
            title="Партии без разрешающего статуса в доступном запасе",
            formula="count",
            value=without_status,
            target=TARGET_LOTS_WITHOUT_STATUS,
            target_label="0",
            sample_size=len(without_known),
            higher_is_better=False,
            unit="",
        ),
        _metric(
            id="control_traceability",
            title="Прослеживаемость результата контроля",
            formula="lots_with_full_refs / all × 100%",
            value=traced_value,
            target=TARGET_CONTROL_TRACEABILITY,
            target_label="100%",
            sample_size=traced_n,
        ),
    ]


__all__ = [
    "SPECIAL_KPI_SPECS",
    "compute_common_kpis",
    "compute_special_kpis",
    "compute_system_quality_kpis",
]
