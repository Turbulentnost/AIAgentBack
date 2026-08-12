import uuid

from app.services.aveon_shipment_schedule_service import shipment_change_idempotency_key


def test_shipment_change_idempotency_key_is_stable() -> None:
    version_id = uuid.uuid4()

    first = shipment_change_idempotency_key(
        task_key="task-1",
        manager_result="Новая дата 20.08.2026",
        active_version_id=version_id,
        nomenclature="Деталь А",
    )
    second = shipment_change_idempotency_key(
        task_key="task-1",
        manager_result="Новая дата 20.08.2026",
        active_version_id=version_id,
        nomenclature="деталь а",
    )

    assert first == second


def test_shipment_change_idempotency_key_depends_on_active_version() -> None:
    first = shipment_change_idempotency_key(
        task_key="task-1",
        manager_result="Новая дата 20.08.2026",
        active_version_id=uuid.uuid4(),
        nomenclature="Деталь А",
    )
    second = shipment_change_idempotency_key(
        task_key="task-1",
        manager_result="Новая дата 20.08.2026",
        active_version_id=uuid.uuid4(),
        nomenclature="Деталь А",
    )

    assert first != second
