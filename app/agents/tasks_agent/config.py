from __future__ import annotations

AGENT_ID = "tasks_agent"
AGENT_NAME = "Агент контроля поручений"
AGENT_PURPOSE = (
    "Работает с поручениями из 1С: загружает список за период, "
    "рассчитывает приоритеты и формирует сводку для пользователя."
)
AGENT_VERSION = "0.1.0"
DEFAULT_MODEL = "gpt-4.1"
HIGH_CONFIDENCE_THRESHOLD = 0.80
MEDIUM_CONFIDENCE_THRESHOLD = 0.55
DEFAULT_PORUCHENIYA_LIMIT = 500
