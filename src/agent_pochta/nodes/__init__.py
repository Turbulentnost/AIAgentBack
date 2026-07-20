"""Узлы графа агента (1–8). Каждый узел: (state, container) -> частичное обновление state."""

from agent_pochta.nodes.n1_imap_listener import node_imap_listener
from agent_pochta.nodes.n2_spam_filter import node_spam_filter
from agent_pochta.nodes.n3_identify_sender import node_identify_sender
from agent_pochta.nodes.n4_process_content import node_process_content
from agent_pochta.nodes.n5_route_department import node_route_department
from agent_pochta.nodes.n6_summarize import node_summarize
from agent_pochta.nodes.n7_create_erp_task import node_create_erp_task
from agent_pochta.nodes.n8_finalize import node_finalize

__all__ = [
    "node_imap_listener",
    "node_spam_filter",
    "node_identify_sender",
    "node_process_content",
    "node_route_department",
    "node_summarize",
    "node_create_erp_task",
    "node_finalize",
]
