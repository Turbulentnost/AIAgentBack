from __future__ import annotations

from app.models.user import User


def can_access_tasks_agent(user: User) -> bool:
    """Проверка доступа к агенту поручений. Будет расширена при подключении RBAC."""
    del user
    return True
