from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.enums import AgentStatus
from app.models.user import User, UserAgent

DOCUMENT_ANALYSIS_AGENT_SLUG = "document_analysis_agent"
DOCUMENT_ANALYSIS_AGENT_NAME = "Агент закупок (Авион)"
DOCUMENT_ANALYSIS_AGENT_PURPOSE = (
    "Принимает файлы по закупкам, анализирует содержимое через LM Studio "
    "и формирует выводы для дальнейшей обработки."
)

AVION_ONLY_USER_EMAILS: frozenset[str] = frozenset(
    {
        "rodionov.pavel@local.dev",
        "tishchenko.nadezhda@local.dev",
        "aksinin.leonid@local.dev",
    }
)

AVION_ONLY_USER_FULL_NAMES: frozenset[str] = frozenset(
    {
        "Родионов Павел",
        "Тищенко Надежда",
        "Аксинин Леонид",
    }
)


@dataclass(frozen=True)
class AvionOnlyUserSpec:
    email: str
    full_name: str


AVION_ONLY_PLATFORM_USERS: tuple[AvionOnlyUserSpec, ...] = (
    AvionOnlyUserSpec(email="rodionov.pavel@local.dev", full_name="Родионов Павел"),
    AvionOnlyUserSpec(email="tishchenko.nadezhda@local.dev", full_name="Тищенко Надежда"),
    AvionOnlyUserSpec(email="aksinin.leonid@local.dev", full_name="Аксинин Леонид"),
)


def is_avion_only_platform_user(user: User | None) -> bool:
    if user is None or user.is_superuser:
        return False

    normalized_email = (user.email or "").strip().casefold()
    if normalized_email and normalized_email in AVION_ONLY_USER_EMAILS:
        return True

    normalized_name = (user.full_name or "").strip()
    return normalized_name in AVION_ONLY_USER_FULL_NAMES


async def get_document_analysis_agent(db: AsyncSession) -> Agent | None:
    return await db.scalar(select(Agent).where(Agent.slug == DOCUMENT_ANALYSIS_AGENT_SLUG))


async def ensure_document_analysis_agent(db: AsyncSession) -> Agent:
    agent = await get_document_analysis_agent(db)
    if agent is None:
        agent = Agent(
            name=DOCUMENT_ANALYSIS_AGENT_NAME,
            slug=DOCUMENT_ANALYSIS_AGENT_SLUG,
            purpose=DOCUMENT_ANALYSIS_AGENT_PURPOSE,
            status=AgentStatus.ACTIVE,
        )
        db.add(agent)
        await db.flush()
        return agent

    agent.name = DOCUMENT_ANALYSIS_AGENT_NAME
    agent.purpose = DOCUMENT_ANALYSIS_AGENT_PURPOSE
    if agent.status != AgentStatus.ACTIVE:
        agent.status = AgentStatus.ACTIVE
    return agent


async def ensure_avion_only_user_agent_grant(db: AsyncSession, user: User) -> bool:
    if not is_avion_only_platform_user(user):
        return False

    agent = await ensure_document_analysis_agent(db)
    grant = await db.scalar(
        select(UserAgent).where(
            UserAgent.user_id == user.id,
            UserAgent.agent_id == agent.id,
        )
    )
    if grant is None:
        db.add(
            UserAgent(
                user_id=user.id,
                agent_id=agent.id,
                access_level="run",
                can_run=True,
                can_view_results=True,
            )
        )
        return True

    changed = False
    if not grant.can_run:
        grant.can_run = True
        changed = True
    if not grant.can_view_results:
        grant.can_view_results = True
        changed = True
    return changed


async def list_agents_for_avion_only_user(db: AsyncSession) -> list[Agent]:
    agent = await ensure_document_analysis_agent(db)
    return [agent]


async def filter_available_agents_for_avion_only_user(
    db: AsyncSession,
    user: User,
    agents: list[Agent],
) -> list[Agent]:
    if not is_avion_only_platform_user(user):
        return agents
    return await list_agents_for_avion_only_user(db)


__all__ = [
    "AVION_ONLY_PLATFORM_USERS",
    "DOCUMENT_ANALYSIS_AGENT_SLUG",
    "ensure_avion_only_user_agent_grant",
    "ensure_document_analysis_agent",
    "filter_available_agents_for_avion_only_user",
    "is_avion_only_platform_user",
    "list_agents_for_avion_only_user",
]
