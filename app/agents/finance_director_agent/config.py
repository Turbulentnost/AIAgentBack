from __future__ import annotations

# slug / agent_id — English; display name — Russian (catalog / UI)
FINANCE_DIRECTOR_AGENT_ID = "finance_director_agent"
FINANCE_DIRECTOR_AGENT_NAME = "ИИ-агент финансового директора"
FINANCE_DIRECTOR_AGENT_PURPOSE = (
    "Исключения по лимитам S10/ДС и срочным предоплатам: "
    "рекомендация allow/deny/defer для HITL финансового директора."
)
AGENT_VERSION = "0.1.0"

POSITION_MARKERS = (
    "финансовый директор",
    "финдиректор",
)

__all__ = [
    "AGENT_VERSION",
    "FINANCE_DIRECTOR_AGENT_ID",
    "FINANCE_DIRECTOR_AGENT_NAME",
    "FINANCE_DIRECTOR_AGENT_PURPOSE",
    "POSITION_MARKERS",
]
