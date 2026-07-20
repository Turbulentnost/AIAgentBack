from __future__ import annotations

# slug / agent_id — English; display name — Russian (catalog / UI)
CFO_HEAD_AGENT_ID = "cfo_head_agent"
CFO_HEAD_AGENT_NAME = "ИИ-агент руководителя ЦФО"
CFO_HEAD_AGENT_PURPOSE = (
    "Утверждение заявки на расходование ДС по ЦФО: проверка лимита, "
    "режима оплаты и подготовка решения руководителя ЦФО."
)
AGENT_VERSION = "0.1.0"

# Markers matched against user.position (HR / 1C), normalized casefold
POSITION_MARKERS = (
    "руководитель цфо",
    "руководитель центра финансовой ответственности",
    "начальник цфо",
)

__all__ = [
    "AGENT_VERSION",
    "CFO_HEAD_AGENT_ID",
    "CFO_HEAD_AGENT_NAME",
    "CFO_HEAD_AGENT_PURPOSE",
    "POSITION_MARKERS",
]
