"""Сбор событий изменений и фоновый экспорт статистики."""

from agent_pochta.stats.change_log import (
    EVENT_TYPES,
    log_department_resolution,
    log_field_change,
    log_restore_from_spam,
    log_routing_correction,
    log_spam_decision,
    log_xml_field_changes,
)
from agent_pochta.stats.classification_log import (
    collect_classification_summary_for_period,
    log_agent_classification_from_row,
    log_classification_event,
    log_operator_department_event,
    log_operator_spam_event,
)
from agent_pochta.stats.export import build_statistics_report, export_statistics_files

__all__ = [
    "EVENT_TYPES",
    "build_statistics_report",
    "collect_classification_summary_for_period",
    "export_statistics_files",
    "log_agent_classification_from_row",
    "log_classification_event",
    "log_department_resolution",
    "log_field_change",
    "log_operator_department_event",
    "log_operator_spam_event",
    "log_restore_from_spam",
    "log_routing_correction",
    "log_spam_decision",
    "log_xml_field_changes",
]
