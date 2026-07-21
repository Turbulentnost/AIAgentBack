"""Агент менеджера по закупкам (procurement_manager_agent), контур №3.

Основной исполнительный ролевой агент: проверка спецификации → поиск/оценка поставщика →
срок и альтернативы → RFQ → сравнение КП → проекты заказа/договора/претензии.
Уровень автономности У1. Архитектура (скелет): внешние обращения — заглушки platform_sdk.
"""

from pathlib import Path

AGENT_ID = "procurement_manager_agent"
AGENT_DIR = Path(__file__).resolve().parent

__all__ = ["AGENT_ID", "AGENT_DIR"]
