from __future__ import annotations

from typing import Any


def normalize_action_names(actions: list | None) -> list[str]:
    if not actions:
        return []
    names: list[str] = []
    for item in actions:
        if isinstance(item, dict):
            label = item.get("name") or item.get("action") or item.get("title")
            if label:
                names.append(str(label).strip())
            continue
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
    return names


def evidence_label(evidence: dict | None) -> str | None:
    if not evidence or not isinstance(evidence, dict):
        return None
    parts: list[str] = []
    if evidence.get("document_code"):
        parts.append(str(evidence["document_code"]))
    if evidence.get("section"):
        parts.append(f"раздел {evidence['section']}")
    if evidence.get("quote"):
        quote = str(evidence["quote"]).strip()
        parts.append(quote[:120] + ("…" if len(quote) > 120 else ""))
    return ", ".join(parts) if parts else None


def normalize_action_details(actions: list | None) -> list[dict[str, Any]]:
    if not actions:
        return []
    details: list[dict[str, Any]] = []
    for item in actions:
        if isinstance(item, dict):
            name = item.get("action") or item.get("name") or item.get("title")
            if not name:
                continue
            details.append(
                {
                    "name": str(name).strip(),
                    "performer": item.get("performer"),
                    "controller": item.get("controller"),
                    "system_or_resource": item.get("system_or_resource"),
                    "evidence_label": evidence_label(item.get("evidence")),
                }
            )
            continue
        if isinstance(item, str) and item.strip():
            details.append(
                {
                    "name": item.strip(),
                    "performer": None,
                    "controller": None,
                    "system_or_resource": None,
                    "evidence_label": None,
                }
            )
    return details


def systems_preview(systems: list[str], forms: list[str], *, limit: int = 2) -> str:
    combined = [*systems, *forms]
    if not combined:
        return "—"
    shown = combined[:limit]
    text = ", ".join(shown)
    remaining = len(combined) - len(shown)
    if remaining > 0:
        text += f" + ещё {remaining}"
    return text


def owner_status_label(*, confirmed: bool, candidate: str | None, pending_relations: int) -> str:
    if confirmed:
        return "Подтверждён"
    if not candidate or pending_relations > 0:
        return "Требует проверки"
    return "Не подтверждён"


EXTRACTION_STATUS_LABELS: dict[str, str] = {
    "pending": "Ожидает обработки",
    "processing": "Обрабатывается",
    "completed": "Готово",
    "failed": "Ошибка",
    "needs_review": "Требует проверки",
}


def confidence_sort_key(value: str | None) -> int:
    order = {"high": 0, "medium": 1, "low": 2}
    return order.get(value or "", 3)


def process_matches_query(item: dict[str, Any], query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    haystacks = [
        item.get("name") or "",
        item.get("goal") or "",
        item.get("description") or "",
        item.get("owner", {}).get("candidate") or "",
        *item.get("systems", []),
        *item.get("forms", []),
        *item.get("resources", []),
        *[doc.get("display_name") or "" for doc in item.get("source_documents", [])],
    ]
    return any(q in value.lower() for value in haystacks if value)
