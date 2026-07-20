from __future__ import annotations

# slug / agent_id — English; display name — Russian (catalog / UI)
EXECUTIVE_DIRECTOR_AGENT_ID = "executive_director_agent"
EXECUTIVE_DIRECTOR_AGENT_NAME = "ИИ-агент исполнительного директора"
EXECUTIVE_DIRECTOR_AGENT_PURPOSE = (
    "Утверждение реестра оплат при согласованиях ЦФО по строкам: "
    "рекомендация approve/return для HITL исполнительного директора."
)
AGENT_VERSION = "0.1.0"

POSITION_MARKERS = (
    "исполнительный директор",
)

__all__ = [
    "AGENT_VERSION",
    "EXECUTIVE_DIRECTOR_AGENT_ID",
    "EXECUTIVE_DIRECTOR_AGENT_NAME",
    "EXECUTIVE_DIRECTOR_AGENT_PURPOSE",
    "POSITION_MARKERS",
]
