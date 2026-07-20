from __future__ import annotations

# slug / agent_id — English; display name — Russian (catalog / UI)
LEGAL_SPECIALIST_AGENT_ID = "legal_specialist_agent"
LEGAL_SPECIALIST_AGENT_NAME = "ИИ-агент юридической службы"
LEGAL_SPECIALIST_AGENT_PURPOSE = (
    "Претензии по незакрытым авансам: черновик/утверждение/пакет иска "
    "для HITL юридической службы."
)
AGENT_VERSION = "0.1.0"

POSITION_MARKERS = (
    "юрисконсульт",
    "правовая служба",
    "юридическ",
    "юрист",
)

__all__ = [
    "AGENT_VERSION",
    "LEGAL_SPECIALIST_AGENT_ID",
    "LEGAL_SPECIALIST_AGENT_NAME",
    "LEGAL_SPECIALIST_AGENT_PURPOSE",
    "POSITION_MARKERS",
]
