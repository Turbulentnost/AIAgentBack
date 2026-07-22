"""Общий слой платформы ИИ-агентов — переиспользуемые сервисы для узлов агентов.

ВНИМАНИЕ (архитектура, не подключено): все внешние обращения — заглушки. Реальные
интеграции (1С:ERP через integration-service, RAG-контур, LLM Gateway, оркестратор,
внешние реестры) подключаются позднее в местах, помеченных ``TODO(integration)``.

Экспортируемый API стабилен — узлы всех агентов опираются на него:

    from app.platform_sdk import (
        call_tool, ToolCall,           # шина инструментов (tools.py)
        hybrid_search,                 # RAG-контур (rag.py)
        llm_complete,                  # LLM Gateway (llm.py)
        human_gate,                    # точка HITL (hitl.py)
        source_ref,                    # конструктор source_reference (sources.py)
        assess_confidence,             # правила уверенности (confidence.py)
        audit_event,                   # журнал audit_logs (audit.py)
        BaseAgentState, make_base_state,
    )
"""

from .tools import ToolCall, call_tool, get_tool_calls, reset_tool_calls
from .rag import hybrid_search
from .llm import llm_complete
from .hitl import human_gate, HumanDecision
from .sources import source_ref
from .confidence import assess_confidence, load_confidence_rules
from .audit import audit_event, get_audit_log, reset_audit_log
from .state import BaseAgentState, make_base_state

__all__ = [
    "ToolCall",
    "call_tool",
    "get_tool_calls",
    "reset_tool_calls",
    "hybrid_search",
    "llm_complete",
    "human_gate",
    "HumanDecision",
    "source_ref",
    "assess_confidence",
    "load_confidence_rules",
    "audit_event",
    "get_audit_log",
    "reset_audit_log",
    "BaseAgentState",
    "make_base_state",
]
