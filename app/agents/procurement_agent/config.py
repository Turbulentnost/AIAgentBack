from __future__ import annotations

AGENT_ID = "procurement_logistics_agent"
AGENT_DB_ID = "a480cfde-01c8-48bb-a5ca-a00100000001"
AGENT_NAME = "Агент закупок и логистики"
AGENT_PURPOSE = (
    "Единая пользовательская точка входа для закупочных кейсов КТ1–КТ7. "
    "Внутренние функциональные и ролевые подграфы подключаются поэтапно."
)
AGENT_VERSION = "0.2.0"
GRAPH_VERSION = "0.2.0"
SUPPORTED_AUTONOMY_LEVEL = 0
MAX_LOOP_ITERATIONS = 8
MAX_IDENTICAL_TOOL_CALLS = 2

READ_ONLY_TOOL_NAMES = [
    "onec_get_procurement_need_lines",
    "onec_get_free_stock",
    "onec_get_reservations",
    "onec_get_store_room_stock",
    "onec_get_open_supplier_orders",
    "onec_get_goods_in_transit",
    "onec_get_internal_transfers",
    "onec_get_available_semifinished_goods",
]
