from __future__ import annotations

# slug / agent_id — English; display name — Russian (catalog / UI)
ACCOUNTANT_AGENT_ID = "accountant_agent"
ACCOUNTANT_AGENT_NAME = "ИИ-агент бухгалтера (оплата)"
ACCOUNTANT_AGENT_PURPOSE = (
    "Контроль плана/просрочки/факта оплаты: "
    "рекомендация mark_paid/defer/cancel для HITL бухгалтера."
)
AGENT_VERSION = "0.1.0"

# Match after excluding chief-accountant markers (see permission helper).
POSITION_MARKERS = (
    "бухгалтер по оплат",
    "бухгалтер расчет",
    "бухгалтер расчёт",
    "бухгалтер",
)

CHIEF_EXCLUDE_MARKERS = (
    "главный бухгалтер",
    "главбух",
)

__all__ = [
    "ACCOUNTANT_AGENT_ID",
    "ACCOUNTANT_AGENT_NAME",
    "ACCOUNTANT_AGENT_PURPOSE",
    "AGENT_VERSION",
    "CHIEF_EXCLUDE_MARKERS",
    "POSITION_MARKERS",
]
