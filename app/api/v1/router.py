from __future__ import annotations
from fastapi import APIRouter
from app.api.v1.admin import users as admin_users
from app.api.v1.endpoints import agents, auth, departments, documents, health, knowledge_bases, tasks, users
api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin_users.router)
api_router.include_router(users.router)
api_router.include_router(departments.router)
api_router.include_router(agents.router)
api_router.include_router(tasks.router)
api_router.include_router(documents.router)
api_router.include_router(knowledge_bases.router)
