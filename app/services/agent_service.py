from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.enums import AgentStatus
from app.schemas.agent import AgentCreate, AgentUpdate


class AgentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, limit: int = 50, offset: int = 0) -> list[Agent]:
        result = await self.db.execute(
            select(Agent)
            .where(Agent.status != AgentStatus.ARCHIVED)
            .order_by(Agent.name)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get(self, agent_id: uuid.UUID) -> Agent | None:
        return await self.db.get(Agent, agent_id)

    async def create(self, data: AgentCreate) -> Agent:
        agent = Agent(**data.model_dump())
        self.db.add(agent)
        await self.db.flush()
        return agent

    async def update(self, agent: Agent, data: AgentUpdate) -> Agent:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(agent, key, value)
        await self.db.flush()
        return agent
