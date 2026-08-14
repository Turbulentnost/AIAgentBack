from __future__ import annotations

from app.services.onec_resource_spec_sync import (
    SPEC_ALLOWED_SUBFOLDERS,
    _collect_specs_from_allowed_subfolders,
)


class _FakeHttp:
    pass


def test_collect_specs_only_from_allowed_subfolders(monkeypatch) -> None:
    production_key = "prod-2"
    kat_folder = "folder-kat"
    nsu_folder = "folder-nsu"
    other_folder = "folder-other"
    spec_kat = "spec-kat"
    spec_nsu = "spec-nsu"
    spec_other = "spec-other"

    def fake_fetch_all(http, entity, extra_query=""):
        if f"Parent_Key eq guid'{production_key}'" in extra_query:
            return [
                {"Ref_Key": kat_folder, "Description": "Катапульта", "IsFolder": True},
                {"Ref_Key": nsu_folder, "Description": "НСУ действующие", "IsFolder": True},
                {"Ref_Key": other_folder, "Description": "Прочее", "IsFolder": True},
            ]
        if f"Parent_Key eq guid'{kat_folder}'" in extra_query:
            return [{"Ref_Key": spec_kat, "IsFolder": False}]
        if f"Parent_Key eq guid'{nsu_folder}'" in extra_query:
            return [{"Ref_Key": spec_nsu, "IsFolder": False}]
        if f"Parent_Key eq guid'{other_folder}'" in extra_query:
            return [{"Ref_Key": spec_other, "IsFolder": False}]
        return []

    def fake_get_json(http, url):
        values = []
        for spec_key, description in (
            (spec_kat, "Kat spec"),
            (spec_nsu, "NSU spec"),
            (spec_other, "Other spec"),
        ):
            if spec_key in url:
                values.append(
                    {
                        "Ref_Key": spec_key,
                        "IsFolder": False,
                        "Description": description,
                        "Code": spec_key,
                        "DeletionMark": False,
                        "Статус": "Действует",
                    }
                )
        return {"value": values}

    monkeypatch.setattr("app.services.onec_resource_spec_sync._fetch_all", fake_fetch_all)
    monkeypatch.setattr("app.services.onec_resource_spec_sync.get_json", fake_get_json)

    headers, matched, _available = _collect_specs_from_allowed_subfolders(_FakeHttp(), production_key)

    assert matched == ["Катапульта", "НСУ действующие"]
    assert {header["Ref_Key"] for header in headers} == {spec_kat, spec_nsu}
    assert len(SPEC_ALLOWED_SUBFOLDERS) == 3
