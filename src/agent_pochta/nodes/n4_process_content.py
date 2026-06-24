"""Узел 4. Обработка содержимого письма и вложений — раздел 4, узел 4.

Вложения передаются в Document Service. Извлечённый текст объединяется
с телом письма в единый контекст для узла 5.
"""

from __future__ import annotations

from agent_pochta.services import ServiceContainer
from agent_pochta.state import AgentState


def node_process_content(state: AgentState, container: ServiceContainer) -> AgentState:
    email = state["email"]
    trace = state.get("trace", []) + ["process_content"]

    parts: list[str] = [email.subject, email.body_text]

    for att in email.attachments:
        processed = container.documents.extract(att)
        att.extracted_text = processed.extracted_text
        att.ocr_used = processed.ocr_used
        if processed.extracted_text:
            parts.append(f"[Вложение {att.filename}]\n{processed.extracted_text}")

    combined = "\n\n".join(p for p in parts if p)
    return {"combined_text": combined, "trace": trace}
