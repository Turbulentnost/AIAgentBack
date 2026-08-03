from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin import users as admin_users
from app.api.v1.endpoints import (
    agent_builder,
    agents,
    auth,
    browser_runs,
    departments,
    document_cards,
    documents,
    health,
    knowledge_base_indexing_ws,
    knowledge_bases,
    meetings,
    nd_change_requests,
    nd_control,
    otk,
    notifications,
    porucheniya,
    positions,
    procurement,
    procurement_manager,
    roles,
    tasks,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin_users.router)
api_router.include_router(users.router)
api_router.include_router(departments.router)
api_router.include_router(positions.router)
api_router.include_router(agents.router)
api_router.include_router(tasks.router)
api_router.include_router(documents.router)
api_router.include_router(document_cards.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(meetings.router)
api_router.include_router(notifications.router)
api_router.include_router(porucheniya.router)
# Static manager routes must be registered before parameterized /role-agents/{agent_id}.
api_router.include_router(procurement_manager.router)
api_router.include_router(procurement_manager.operations_router)
api_router.include_router(procurement.router)
api_router.include_router(otk.router)
api_router.include_router(knowledge_base_indexing_ws.router)
api_router.include_router(roles.router)
api_router.include_router(browser_runs.router)
api_router.include_router(nd_change_requests.router)
api_router.include_router(nd_control.router)
api_router.include_router(agent_builder.router)
