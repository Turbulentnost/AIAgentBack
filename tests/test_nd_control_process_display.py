from app.services.nd_process_display_mapper import (
    normalize_action_details,
    owner_status_label,
    systems_preview,
)


def test_owner_status_label_unconfirmed() -> None:
    assert owner_status_label(confirmed=False, candidate="ИТ", pending_relations=0) == "Не подтверждён"


def test_owner_status_label_requires_review_without_candidate() -> None:
    assert owner_status_label(confirmed=False, candidate=None, pending_relations=0) == "Требует проверки"


def test_systems_preview_truncates() -> None:
    assert systems_preview(["1С", "Exchange", "NAS"], ["Акт"], limit=2) == "1С, Exchange + ещё 2"


def test_normalize_action_details_skips_invalid() -> None:
    details = normalize_action_details([{"performer": "X"}, {"action": "Шаг 1"}])
    assert len(details) == 1
    assert details[0]["name"] == "Шаг 1"
