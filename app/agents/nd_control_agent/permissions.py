from __future__ import annotations

from app.services.nd_control_permission import (
    can_access_nd_control_agent,
    can_manage_nd_control_departments,
    can_manage_nd_control_templates,
    can_reanalyze_nd_control_departments,
    can_upload_template_documents,
    can_view_nd_change_journal,
    is_process_management_specialist_position,
    is_quality_deputy_position,
)

__all__ = [
    "can_access_nd_control_agent",
    "can_manage_nd_control_departments",
    "can_manage_nd_control_templates",
    "can_reanalyze_nd_control_departments",
    "can_upload_template_documents",
    "can_view_nd_change_journal",
    "is_process_management_specialist_position",
    "is_quality_deputy_position",
]
