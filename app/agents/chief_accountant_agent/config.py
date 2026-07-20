from __future__ import annotations

# slug / agent_id — English; display name — Russian (catalog / UI)
CHIEF_ACCOUNTANT_AGENT_ID = "chief_accountant_agent"
CHIEF_ACCOUNTANT_AGENT_NAME = "ИИ-агент главного бухгалтера"
CHIEF_ACCOUNTANT_AGENT_PURPOSE = (
    "Бухгалтерское заключение по реквизитам, авансам и согласованию ЦФО: "
    "рекомендация approve/return для HITL главного бухгалтера."
)
AGENT_VERSION = "0.1.0"

POSITION_MARKERS = (
    "главный бухгалтер",
    "главбух",
)

__all__ = [
    "AGENT_VERSION",
    "CHIEF_ACCOUNTANT_AGENT_ID",
    "CHIEF_ACCOUNTANT_AGENT_NAME",
    "CHIEF_ACCOUNTANT_AGENT_PURPOSE",
    "POSITION_MARKERS",
]
