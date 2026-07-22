"""Ролевые агенты ОМТО (контур №3 «Закупка и выбор поставщика»).

Адаптирует внешние LangGraph-агенты (менеджер по закупкам, начальник ОМТО, его
заместитель, инженер КБ/ГСПП, сотрудник СБ) под платформу: регистрация в реестре,
единый контракт вход/выход, вычисление KPI из реальной истории запусков.
"""

from app.agents.omto_role_agents.catalog import (
    OMTO_AGENT_SLUGS,
    OMTO_AGENTS,
    OmtoAgentSpec,
    get_spec,
)

__all__ = ["OMTO_AGENTS", "OMTO_AGENT_SLUGS", "OmtoAgentSpec", "get_spec"]
