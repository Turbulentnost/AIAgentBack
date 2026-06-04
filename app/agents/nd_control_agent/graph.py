from __future__ import annotations
from langgraph.graph import END, START, StateGraph
from app.agents.common.state import BaseAgentState
from app.core.logging import get_logger
logger = get_logger(__name__)

class NDControlState(BaseAgentState, total=False):
    classification: dict
    changes: list[dict]
    relations: list[dict]

async def load_documents(state: NDControlState) -> dict:
    logger.info("nd_control.load_documents", task_id=state.get("task_id"))
    return {"documents": []}
async def classify_documents(state: NDControlState) -> dict:
    logger.info("nd_control.classify_documents")
    return {"classification": {}}
async def check_changes(state: NDControlState) -> dict:
    logger.info("nd_control.check_changes")
    return {"changes": []}
async def check_validity(state: NDControlState) -> dict:
    logger.info("nd_control.check_validity")
    return {}
async def check_relations(state: NDControlState) -> dict:
    logger.info("nd_control.check_relations")
    return {"relations": []}
async def assess_confidence(state: NDControlState) -> dict:
    logger.info("nd_control.assess_confidence")
    return {"data_confidence": "medium", "requires_human_review": False}
async def form_conclusion(state: NDControlState) -> dict:
    logger.info("nd_control.form_conclusion")
    return {"summary": "", "findings": state.get("findings", [])}

NODE_SEQUENCE = [
    ("load_documents", load_documents),
    ("classify_documents", classify_documents),
    ("check_changes", check_changes),
    ("check_validity", check_validity),
    ("check_relations", check_relations),
    ("assess_confidence", assess_confidence),
    ("form_conclusion", form_conclusion),
]

def build_graph():
    graph = StateGraph(NDControlState)
    for name, fn in NODE_SEQUENCE:
        graph.add_node(name, fn)
    graph.add_edge(START, NODE_SEQUENCE[0][0])
    for (current, _), (nxt, _) in zip(NODE_SEQUENCE, NODE_SEQUENCE[1:]):
        graph.add_edge(current, nxt)
    graph.add_edge(NODE_SEQUENCE[-1][0], END)
    return graph.compile()
