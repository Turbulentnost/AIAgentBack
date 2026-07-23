from __future__ import annotations

from app.services.department_normative_path_utils import (
    EXCLUDED_FOLDER_SEGMENTS,
    parse_normative_relative_path,
)
from app.services.onec_departments_fetcher import EnterpriseDepartment
from app.services.department_normative_path_utils import match_enterprise_department


def test_parse_department_from_subfolder_path() -> None:
    parsed = parse_normative_relative_path(
        "Нормативные документы по подразделениям/ОТК/СТО-34-003.docx"
    )
    assert parsed.folder_department == "ОТК"
    assert parsed.excluded_reason is None
    assert parsed.scope_parts == ()


def test_parse_scope_from_nested_path() -> None:
    parsed = parse_normative_relative_path(
        "Нормативные документы по подразделениям/ОТК/Инструкции/И-05-010.pdf"
    )
    assert parsed.folder_department == "ОТК"
    assert parsed.scope_parts == ("Инструкции",)


def test_exclude_archive_segment() -> None:
    parsed = parse_normative_relative_path(
        "Нормативные документы по подразделениям/Архив/ОТК/СТО-01.docx"
    )
    assert parsed.folder_department is None
    assert parsed.excluded_reason == "Архив"


def test_welding_folder_is_outside_department_tree() -> None:
    parsed = parse_normative_relative_path("Документы по сварке/СТО-99-001.docx")
    assert parsed.folder_department is None
    assert parsed.excluded_reason is None
    parsed = parse_normative_relative_path(
        "Нормативные документы по подразделениям/Общее/ПП-01-001.docx"
    )
    assert parsed.folder_department is None
    assert parsed.excluded_reason == "Общее"


def test_parse_department_from_direct_path() -> None:
    parsed = parse_normative_relative_path("Коммерческая служба/Торговые политики/file.pdf")
    assert parsed.folder_department == "Коммерческая служба"
    assert parsed.scope_parts == ("Торговые политики",)
    assert parsed.excluded_reason is None


def test_exclude_archive_under_department() -> None:
    parsed = parse_normative_relative_path("ОМТО/Архив/СТО-28-020.docx")
    assert parsed.folder_department is None
    assert parsed.excluded_reason == "Архив"


def test_excluded_segments_are_normalized() -> None:
    assert "архив" in EXCLUDED_FOLDER_SEGMENTS
    assert "общее" in EXCLUDED_FOLDER_SEGMENTS


def test_match_enterprise_department_exact() -> None:
    departments = [
        EnterpriseDepartment(
            external_id="1",
            parent_external_id=None,
            name="ОТК",
            path="Головной офис / ОТК",
        )
    ]
    matched, warning = match_enterprise_department("ОТК", departments)
    assert warning is None
    assert matched is not None
    assert matched.name == "ОТК"
