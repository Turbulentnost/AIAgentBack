from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.user import Department, User
from app.services.permission_service import PermissionService

PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG = "production_preparation_engineer_agent"
PRODUCTION_DISPATCHER_AGENT_SLUG = "production_dispatcher_agent"
WAREHOUSE_PICKER_AGENT_SLUG = "warehouse_picker_agent"
WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG = "warehouse_complex_chief_agent"
PURCHASE_MANAGER_AGENT_SLUG = "purchase_manager_agent"
OMTO_SUPPORT_MANAGER_AGENT_SLUG = "omto_support_manager_agent"
OTK_HEAD_AGENT_SLUG = "otk_head_agent"
QUALITY_ENGINEER_AGENT_SLUG = "quality_engineer_agent"
QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG = "quality_deputy_director_agent"
QUALITY_KPI_AGENT_SLUG = "quality_kpi_agent"

_ENGINEER_POSITION_MARKERS = (
    "инженер по подготовке производства",
    "инженер спп",
)
_DISPATCHER_POSITION_MARKERS = (
    "диспетчер производства",
    "главный диспетчер",
)
_PICKER_POSITION_MARKERS = (
    "кладовщик-комплектовщик",
    "кладовщик комплектовщик",
)
_COMPLEX_CHIEF_EXACT_POSITION = "начальник складского комплекса"
_WAREHOUSE_HEAD_POSITION = "начальник склада"
_WAREHOUSE_COMPLEX_DEPARTMENT_MARKER = "складской комплекс"
_PURCHASE_MANAGER_POSITION_MARKERS = (
    "менеджер по закупкам",
    "менеджер закупок",
)
_OMTO_POSITION_MARKERS = (
    "менеджер по сопровождению омто",
    "менеджер омто",
    "омто",
)
_OTK_HEAD_MARKERS = (
    "начальник отк",
    "начальник отдела технического контроля",
)
_QUALITY_ENGINEER_MARKERS = (
    "инженер по качеству",
    "инженер отк",
    "сотрудник отк",
    "работник отк",
)
_QUALITY_DEPUTY_MARKERS = (
    "заместитель директора по качеству",
    "зам директора по качеству",
    "заместитель технического директора по качеству",
    "зтд по качеству",
    "здк",
)


def _normalize_position(position: str | None) -> str:
    if not position:
        return ""
    return " ".join(position.casefold().replace("ё", "е").split())


def is_production_preparation_engineer_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    return any(marker in normalized for marker in _ENGINEER_POSITION_MARKERS)


def is_production_dispatcher_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    return any(marker in normalized for marker in _DISPATCHER_POSITION_MARKERS)


def is_warehouse_picker_position(position: str | None) -> bool:
    if not position:
        return False
    normalized = " ".join(
        position.casefold().replace("ё", "е").replace("-", " ").split()
    )
    return any(
        marker.replace("-", " ") in normalized for marker in _PICKER_POSITION_MARKERS
    )


def is_warehouse_complex_chief_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    return normalized == _COMPLEX_CHIEF_EXACT_POSITION


def is_warehouse_head_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    if is_warehouse_complex_chief_position(position):
        return False
    return _WAREHOUSE_HEAD_POSITION in normalized


def is_warehouse_complex_department_name(name: str | None) -> bool:
    if not name:
        return False
    normalized = _normalize_position(name)
    return _WAREHOUSE_COMPLEX_DEPARTMENT_MARKER in normalized


async def user_in_warehouse_complex_department(
    db: AsyncSession,
    user: User,
) -> bool:
    if user.department_id is None:
        return False
    department = await db.get(Department, user.department_id)
    if department is None:
        return False
    return is_warehouse_complex_department_name(department.name)


def is_purchase_manager_position(position: str | None) -> bool:
    if not position:
        return False
    normalized = " ".join(
        position.casefold().replace("ё", "е").replace("-", " ").split()
    )
    return any(marker in normalized for marker in _PURCHASE_MANAGER_POSITION_MARKERS)


def is_omto_support_manager_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    if is_production_preparation_engineer_position(normalized):
        return False
    if is_quality_engineer_position(normalized) or is_otk_head_position(normalized):
        return False
    return any(marker in normalized for marker in _OMTO_POSITION_MARKERS)


def is_otk_head_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    return any(marker in normalized for marker in _OTK_HEAD_MARKERS)


def is_quality_engineer_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    if is_production_preparation_engineer_position(normalized):
        return False
    return any(marker in normalized for marker in _QUALITY_ENGINEER_MARKERS)


def is_quality_deputy_director_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    return any(marker in normalized for marker in _QUALITY_DEPUTY_MARKERS)


async def can_access_procurement_orchestrator(db: AsyncSession, user: User) -> bool:
    """Orchestrator UI/API is visible only to the system superuser."""
    _ = db
    return bool(user.is_superuser)


async def can_refresh_procurement_orchestrator(db: AsyncSession, user: User) -> bool:
    return await can_access_procurement_orchestrator(db, user)


async def _can_access_by_slug_or_position(
    db: AsyncSession,
    user: User,
    slug: str,
    position_ok: bool,
) -> bool:
    if user.is_superuser or position_ok:
        return True
    agent = await db.scalar(select(Agent).where(Agent.slug == slug))
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def can_access_production_preparation_engineer(
    db: AsyncSession,
    user: User,
) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG,
        is_production_preparation_engineer_position(user.position),
    )


async def can_access_omto_support_manager(
    db: AsyncSession,
    user: User,
) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        OMTO_SUPPORT_MANAGER_AGENT_SLUG,
        is_omto_support_manager_position(user.position),
    )


async def can_access_otk_head(db: AsyncSession, user: User) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        OTK_HEAD_AGENT_SLUG,
        is_otk_head_position(user.position),
    )


async def can_access_quality_engineer(db: AsyncSession, user: User) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        QUALITY_ENGINEER_AGENT_SLUG,
        is_quality_engineer_position(user.position),
    )


async def can_access_quality_deputy_director(db: AsyncSession, user: User) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG,
        is_quality_deputy_director_position(user.position),
    )


async def can_access_quality_kpi(db: AsyncSession, user: User) -> bool:
    if user.is_superuser or is_quality_deputy_director_position(user.position):
        return True
    return await _can_access_by_slug_or_position(
        db,
        user,
        QUALITY_KPI_AGENT_SLUG,
        False,
    )


async def _append_agent_by_slug(db: AsyncSession, agents: list, slug: str) -> list:
    agent = await db.scalar(select(Agent).where(Agent.slug == slug))
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


async def append_production_preparation_engineer_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_production_preparation_engineer(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG)


async def append_omto_support_manager_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_omto_support_manager(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, OMTO_SUPPORT_MANAGER_AGENT_SLUG)


async def append_otk_head_agent(db: AsyncSession, user: User, agents: list) -> list:
    if not await can_access_otk_head(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, OTK_HEAD_AGENT_SLUG)


async def append_quality_engineer_agent(db: AsyncSession, user: User, agents: list) -> list:
    if not await can_access_quality_engineer(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, QUALITY_ENGINEER_AGENT_SLUG)


async def append_quality_deputy_director_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_quality_deputy_director(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG)


async def append_quality_kpi_agent(db: AsyncSession, user: User, agents: list) -> list:
    if not await can_access_quality_kpi(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, QUALITY_KPI_AGENT_SLUG)


async def can_access_production_dispatcher(
    db: AsyncSession,
    user: User,
) -> bool:
    if user.is_superuser or is_production_dispatcher_position(user.position):
        return True
    agent = await db.scalar(
        select(Agent).where(Agent.slug == PRODUCTION_DISPATCHER_AGENT_SLUG)
    )
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def append_production_dispatcher_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_production_dispatcher(db, user):
        return agents
    agent = await db.scalar(
        select(Agent).where(Agent.slug == PRODUCTION_DISPATCHER_AGENT_SLUG)
    )
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


async def can_access_warehouse_picker(
    db: AsyncSession,
    user: User,
) -> bool:
    if user.is_superuser or is_warehouse_picker_position(user.position):
        return True
    agent = await db.scalar(
        select(Agent).where(Agent.slug == WAREHOUSE_PICKER_AGENT_SLUG)
    )
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def append_warehouse_picker_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_warehouse_picker(db, user):
        return agents
    agent = await db.scalar(
        select(Agent).where(Agent.slug == WAREHOUSE_PICKER_AGENT_SLUG)
    )
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


async def can_access_warehouse_complex_chief(
    db: AsyncSession,
    user: User,
) -> bool:
    if user.is_superuser or is_warehouse_complex_chief_position(user.position):
        return True
    if is_warehouse_head_position(user.position) and await user_in_warehouse_complex_department(
        db, user
    ):
        return True
    agent = await db.scalar(
        select(Agent).where(Agent.slug == WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG)
    )
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def is_warehouse_complex_chief_exclusive_user(
    db: AsyncSession,
    user: User,
) -> bool:
    """Должность начальника склада/комплекса: в каталоге только его агент по закупкам."""
    if user.is_superuser:
        return False
    if is_warehouse_complex_chief_position(user.position):
        return True
    return is_warehouse_head_position(user.position) and await user_in_warehouse_complex_department(
        db, user
    )


async def append_warehouse_complex_chief_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_warehouse_complex_chief(db, user):
        return agents
    agent = await db.scalar(
        select(Agent).where(Agent.slug == WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG)
    )
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


async def can_access_purchase_manager(db: AsyncSession, user: User) -> bool:
    if user.is_superuser or is_purchase_manager_position(user.position):
        return True
    agent = await db.scalar(select(Agent).where(Agent.slug == PURCHASE_MANAGER_AGENT_SLUG))
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def can_access_procurement_manager(db: AsyncSession, user: User) -> bool:
    """Alias for rich manager API imported from Jalko (same slug/permissions)."""
    return await can_access_purchase_manager(db, user)


async def append_purchase_manager_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_purchase_manager(db, user):
        return agents
    agent = await db.scalar(select(Agent).where(Agent.slug == PURCHASE_MANAGER_AGENT_SLUG))
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


__all__ = [
    "OMTO_SUPPORT_MANAGER_AGENT_SLUG",
    "OTK_HEAD_AGENT_SLUG",
    "PRODUCTION_DISPATCHER_AGENT_SLUG",
    "PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG",
    "PURCHASE_MANAGER_AGENT_SLUG",
    "QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG",
    "QUALITY_ENGINEER_AGENT_SLUG",
    "QUALITY_KPI_AGENT_SLUG",
    "WAREHOUSE_COMPLEX_CHIEF_AGENT_SLUG",
    "WAREHOUSE_PICKER_AGENT_SLUG",
    "append_omto_support_manager_agent",
    "append_otk_head_agent",
    "append_production_dispatcher_agent",
    "append_production_preparation_engineer_agent",
    "append_purchase_manager_agent",
    "append_quality_deputy_director_agent",
    "append_quality_engineer_agent",
    "append_quality_kpi_agent",
    "append_warehouse_complex_chief_agent",
    "append_warehouse_picker_agent",
    "can_access_omto_support_manager",
    "can_access_otk_head",
    "can_access_production_dispatcher",
    "can_access_production_preparation_engineer",
    "can_access_procurement_orchestrator",
    "can_access_procurement_manager",
    "can_access_purchase_manager",
    "can_access_quality_deputy_director",
    "can_access_quality_engineer",
    "can_access_quality_kpi",
    "can_access_warehouse_complex_chief",
    "can_access_warehouse_picker",
    "can_refresh_procurement_orchestrator",
    "is_omto_support_manager_position",
    "is_otk_head_position",
    "is_production_dispatcher_position",
    "is_production_preparation_engineer_position",
    "is_purchase_manager_position",
    "is_quality_deputy_director_position",
    "is_quality_engineer_position",
    "is_warehouse_complex_chief_exclusive_user",
    "is_warehouse_complex_chief_position",
    "is_warehouse_complex_department_name",
    "is_warehouse_head_position",
    "is_warehouse_picker_position",
    "user_in_warehouse_complex_department",
]
