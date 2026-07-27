from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import Department, DepartmentAgent, Role, User, UserAgent, user_roles

MEETING_AGENT_SLUG = "meeting_agent"
MEETING_RUN_PERMISSION = "agents.meeting_agent.run"

_OFFICE_MANAGEMENT_MARKER = "управление делами"
_MEETING_AGENT_POSITIONS = frozenset(
    {
        "руководитель инспекционной группы",
    }
)


def is_office_management_department_name(name: str | None) -> bool:
    if not name or not name.strip():
        return False
    normalized = name.strip().lower()
    if normalized.startswith("(гк.)"):
        return False
    return _OFFICE_MANAGEMENT_MARKER in normalized


def is_meeting_agent_position(position: str | None) -> bool:
    if not position or not position.strip():
        return False
    return position.strip().casefold() in _MEETING_AGENT_POSITIONS


async def is_office_management_user(db: AsyncSession, user: User) -> bool:
    if user.department_id is None:
        return False
    department = await db.get(Department, user.department_id)
    if department is None:
        return False
    return is_office_management_department_name(department.name)


async def is_meeting_agent_auto_access_user(db: AsyncSession, user: User) -> bool:
    if is_meeting_agent_position(user.position):
        return True
    return await is_office_management_user(db, user)


async def user_has_admin_role(db: AsyncSession, user: User) -> bool:
    if user.role is not None and user.role.code == "admin":
        return True
    result = await db.execute(
        select(Role.code)
        .join(user_roles, user_roles.c.role_id == Role.id)
        .where(user_roles.c.user_id == user.id)
    )
    return "admin" in {row[0] for row in result.all()}


async def can_manage_meetings(db: AsyncSession, user: User) -> bool:
    if user.is_superuser:
        return True
    return await user_has_admin_role(db, user)


async def can_access_meeting_agent(db: AsyncSession, user: User) -> bool:
    if user.is_superuser:
        return True
    if await user_has_admin_role(db, user):
        return True
    if await is_meeting_agent_auto_access_user(db, user):
        return True

    from app.models.agent import Agent
    from app.services.permission_service import PermissionService

    agent = await db.scalar(select(Agent).where(Agent.slug == MEETING_AGENT_SLUG))
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def list_meeting_agent_users(db: AsyncSession) -> list[User]:
    """Активные пользователи с доступом к meeting_agent (получатели in-app уведомлений)."""
    from app.models.agent import Agent
    from app.models.user import Permission, role_permissions

    agent = await db.scalar(select(Agent).where(Agent.slug == MEETING_AGENT_SLUG))
    now = datetime.now(timezone.utc)
    candidate_ids: set = set()

    result = await db.execute(
        select(User.id).where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.is_superuser.is_(True),
        )
    )
    candidate_ids.update(result.scalars().all())

    admin_role_ids = select(Role.id).where(Role.code == "admin")
    result = await db.execute(
        select(user_roles.c.user_id).where(user_roles.c.role_id.in_(admin_role_ids))
    )
    candidate_ids.update(result.scalars().all())
    result = await db.execute(
        select(User.id).where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.role_id.in_(admin_role_ids),
        )
    )
    candidate_ids.update(result.scalars().all())

    if agent is not None:
        result = await db.execute(
            select(UserAgent.user_id).where(
                UserAgent.agent_id == agent.id,
                UserAgent.can_run.is_(True),
                or_(UserAgent.expires_at.is_(None), UserAgent.expires_at > now),
            )
        )
        candidate_ids.update(result.scalars().all())

        dept_ids = select(DepartmentAgent.department_id).where(
            DepartmentAgent.agent_id == agent.id,
            DepartmentAgent.can_run.is_(True),
        )
        result = await db.execute(
            select(User.id).where(
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.department_id.in_(dept_ids),
            )
        )
        candidate_ids.update(result.scalars().all())

        perm = await db.scalar(
            select(Permission).where(Permission.code == MEETING_RUN_PERMISSION)
        )
        if perm is not None:
            role_ids = select(role_permissions.c.role_id).where(
                role_permissions.c.permission_id == perm.id
            )
            result = await db.execute(
                select(user_roles.c.user_id).where(user_roles.c.role_id.in_(role_ids))
            )
            candidate_ids.update(result.scalars().all())
            result = await db.execute(
                select(User.id).where(
                    User.deleted_at.is_(None),
                    User.is_active.is_(True),
                    User.role_id.in_(role_ids),
                )
            )
            candidate_ids.update(result.scalars().all())

    office_dept_ids = select(Department.id).where(
        Department.is_active.is_(True),
        Department.name.ilike("%управление делами%"),
        ~Department.name.ilike("(гк.)%"),
    )
    result = await db.execute(
        select(User.id).where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.department_id.in_(office_dept_ids),
        )
    )
    candidate_ids.update(result.scalars().all())

    for position in _MEETING_AGENT_POSITIONS:
        result = await db.execute(
            select(User.id).where(
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.position.ilike(position),
            )
        )
        candidate_ids.update(result.scalars().all())

    if not candidate_ids:
        return []

    result = await db.execute(
        select(User)
        .options(selectinload(User.role), selectinload(User.department))
        .where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.id.in_(candidate_ids),
        )
    )
    users = list(result.scalars().unique().all())
    allowed: list[User] = []
    for user in users:
        if await can_access_meeting_agent(db, user):
            allowed.append(user)
    return allowed


async def append_meeting_agent_for_office_management(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    """Добавляет meeting_agent в каталог без RBAC-роли (УД и разрешённые должности)."""
    from app.models.agent import Agent

    if user.is_superuser or not await is_meeting_agent_auto_access_user(db, user):
        return agents

    agent = await db.scalar(select(Agent).where(Agent.slug == MEETING_AGENT_SLUG))
    if agent is None:
        return agents

    known_ids = {item.id for item in agents}
    if agent.id in known_ids:
        return agents

    return sorted([*agents, agent], key=lambda item: item.name)
