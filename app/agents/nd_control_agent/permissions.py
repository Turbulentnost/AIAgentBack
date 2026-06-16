from __future__ import annotations

from app.services.nd_control_permission import (
    can_access_nd_control_agent,
    can_manage_nd_control_departments,
    is_quality_deputy_position,
)

__all__ = [
    "can_access_nd_control_agent",
    "can_manage_nd_control_departments",
    "is_quality_deputy_position",
]
