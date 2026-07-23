from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.agents.procurement_manager_agent.schemas import (
    PurchaseOrderDraft,
    PurchaseOrderLine,
    RFQDraft,
    RFQDraftRequest,
    Supplier,
)


def render_rfq_draft(
    request: RFQDraftRequest,
    suppliers: list[Supplier],
    *,
    case_number: str,
) -> RFQDraft:
    supplier_names = ", ".join(item.name for item in suppliers) or ", ".join(
        request.supplier_ids
    )
    rows = [
        (
            f"{index}. {line.description}: {line.quantity} {line.unit}; "
            "требуемая дата: "
            f"{line.required_date.isoformat() if line.required_date else 'уточнить'}"
        )
        for index, line in enumerate(request.lines, start=1)
    ]
    terms = "\n".join(f"- {term}" for term in request.terms) or "- Указать цену и срок поставки"
    body = (
        f"Получатели: {supplier_names}\n"
        f"Просим предоставить коммерческое предложение по кейсу {case_number}.\n\n"
        + "\n".join(rows)
        + f"\n\nУсловия:\n{terms}\n"
        "Документ является проектом и не создаёт обязательств до утверждения человеком."
    )
    return RFQDraft(
        rfq_id=str(uuid4()),
        supplier_ids=request.supplier_ids,
        lines=request.lines,
        subject=f"Запрос котировок по кейсу {case_number}",
        body=body,
        created_at=datetime.now(UTC),
    )


def render_purchase_order_draft(
    *,
    supplier_id: str,
    supplier_name: str,
    lines: list[PurchaseOrderLine],
    case_number: str,
    source_quote_id: str | None = None,
) -> PurchaseOrderDraft:
    total = sum((line.quantity * line.unit_price for line in lines), Decimal("0"))
    rows = [
        (
            f"{index}. {line.description} ({line.nomenclature_id}): "
            f"{line.quantity} {line.unit} × {line.unit_price} = "
            f"{line.quantity * line.unit_price}; срок {line.delivery_days} дн."
        )
        for index, line in enumerate(lines, start=1)
    ]
    body = (
        f"Поставщик: {supplier_name} ({supplier_id})\n"
        f"Кейс: {case_number}\n"
        f"Источник КП: {source_quote_id or '—'}\n\n"
        + "\n".join(rows)
        + f"\n\nИтого: {total} RUB\n"
        "Черновик заказа поставщику. Не отправляется и не проводится в 1С "
        "без подтверждения человеком. Оплата запрещена агенту."
    )
    return PurchaseOrderDraft(
        po_id=str(uuid4()),
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        lines=lines,
        total=total,
        source_quote_id=source_quote_id,
        subject=f"Заказ поставщику по кейсу {case_number}",
        body=body,
        created_at=datetime.now(UTC),
    )


__all__ = ["render_purchase_order_draft", "render_rfq_draft"]
