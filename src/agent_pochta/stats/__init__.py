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
from agent_pochta.stats.export import build_statistics_report, export_statistics_files

__all__ = [
    "EVENT_TYPES",
    "build_statistics_report",
    "export_statistics_files",
    "log_department_resolution",
    "log_field_change",
    "log_restore_from_spam",
    "log_routing_correction",
    "log_spam_decision",
    "log_xml_field_changes",
]
