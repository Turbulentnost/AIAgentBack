from __future__ import annotations

AGENT_ID = "meeting_agent"
AGENT_NAME = "Агент организации совещаний"
AGENT_PURPOSE = (
    "Обрабатывает служебные записки на организацию совещаний: загружает данные из 1С, "
    "проверяет заполнение, подбирает время и переговорные, формирует и отправляет приглашения."
)
AGENT_VERSION = "1.0.0"
DEFAULT_MODEL = "gpt-4.1"
HIGH_CONFIDENCE_THRESHOLD = 0.80
MEDIUM_CONFIDENCE_THRESHOLD = 0.55
