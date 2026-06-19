from __future__ import annotations

from app.services.onec_departments_fetcher import (
    EnterpriseDepartment,
    fetch_all_departments_from_1c,
    filter_departments,
    format_departments_text,
)


def test_filter_departments_by_query() -> None:
    rows = [
        EnterpriseDepartment(
            external_id="a",
            parent_external_id=None,
            name="Отдел качества",
            path="Головной офис / Отдел качества",
        ),
        EnterpriseDepartment(
            external_id="b",
            parent_external_id="a",
            name="ОТК",
            path="Головной офис / Отдел качества / ОТК",
        ),
        EnterpriseDepartment(
            external_id="c",
            parent_external_id=None,
            name="Производство",
            path="Производство",
        ),
    ]
    filtered = filter_departments(rows, query="отк")
    assert len(filtered) == 1
    assert filtered[0].name == "ОТК"


def test_format_departments_text() -> None:
    text = format_departments_text(
        [
            EnterpriseDepartment(
                external_id="a",
                parent_external_id=None,
                name="ОТК",
                path="ОТК",
            )
        ]
    )
    assert "Найдено подразделений: 1" in text
    assert "ОТК [a]" in text


def test_fetch_all_departments_from_1c_uses_session(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSession:
        pass

    def fake_build(session):
        captured["session"] = session
        return [
            {
                "external_id": "111",
                "parent_external_id": None,
                "name": "Отдел качества",
                "path": "Отдел качества",
            }
        ]

    monkeypatch.setattr(
        "app.services.onec_departments_fetcher.onec.build_enterprise_departments",
        fake_build,
    )
    rows = fetch_all_departments_from_1c(FakeSession())
    assert len(rows) == 1
    assert rows[0].name == "Отдел качества"
    assert isinstance(captured["session"], FakeSession)
