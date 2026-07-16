"""Детерминированная маршрутизация по ТЗ 3658 (правила > LLM)."""

from agent_pochta.routing.engine import RouteEngine, route_email
from agent_pochta.routing.models import ConfidenceLevel, RoutingDecision, ServiceRoute
from agent_pochta.routing.priority import PriorityDecision, select_priority
from agent_pochta.routing.recipients import split_routing_recipients
from agent_pochta.routing.xml_builder import build_xml_document

__all__ = [
    "ConfidenceLevel",
    "PriorityDecision",
    "RouteEngine",
    "RoutingDecision",
    "ServiceRoute",
    "build_xml_document",
    "route_email",
    "select_priority",
    "split_routing_recipients",
]
