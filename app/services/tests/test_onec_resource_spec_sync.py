from app.services.onec_resource_spec_sync import (
    _filter_importable_spec_headers,
    _is_active_spec_header,
    _is_excluded_spec_header,
)


def test_active_spec_requires_deistvuet_status() -> None:
    assert _is_active_spec_header({"Статус": "Действует", "DeletionMark": False}) is True
    assert _is_active_spec_header({"Статус": "ВРазработке", "DeletionMark": False}) is False
    assert _is_active_spec_header({"Статус": "Действует", "DeletionMark": True}) is False


def test_excluded_spec_by_description() -> None:
    assert (
        _is_excluded_spec_header({"Description": "Колесо под подшипник_Kat_v1"})
        is True
    )
    assert _is_excluded_spec_header({"Description": "Сокол И"}) is False


def test_filter_importable_spec_headers() -> None:
    headers = [
        {"Ref_Key": "1", "Description": "Сокол И", "Статус": "Действует", "DeletionMark": False},
        {"Ref_Key": "2", "Description": "Черновик", "Статус": "ВРазработке", "DeletionMark": False},
        {
            "Ref_Key": "3",
            "Description": "Колесо под подшипник_Kat_v1",
            "Статус": "Действует",
            "DeletionMark": False,
        },
    ]
    filtered = _filter_importable_spec_headers(headers)
    assert [h["Ref_Key"] for h in filtered] == ["1"]
