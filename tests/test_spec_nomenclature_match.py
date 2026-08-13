from __future__ import annotations

from app.services.spec_nomenclature_match import (
    EMPTY_GUID,
    SpecNomenclatureIndex,
    normalize_nomenclature_text,
    stock_row_matches_spec,
)


class _Row:
    def __init__(self, *, nomenclature_key: str = "", code: str = "", name: str = "") -> None:
        self.nomenclature_key = nomenclature_key
        self.code = code
        self.name = name


def test_matches_by_nomenclature_key() -> None:
    index = SpecNomenclatureIndex(
        keys=frozenset({"guid-1"}),
        codes=frozenset(),
        names=frozenset(),
        materials_count=1,
    )
    assert stock_row_matches_spec(_Row(nomenclature_key="guid-1", code="X", name="Y"), index)


def test_matches_by_code_when_key_empty() -> None:
    index = SpecNomenclatureIndex(
        keys=frozenset(),
        codes=frozenset({normalize_nomenclature_text("ЦБ-00001234")}),
        names=frozenset(),
        materials_count=1,
    )
    row = _Row(nomenclature_key=EMPTY_GUID, code="цб-00001234", name="Корпус")
    assert stock_row_matches_spec(row, index)


def test_matches_by_name_when_code_differs() -> None:
    index = SpecNomenclatureIndex(
        keys=frozenset(),
        codes=frozenset(),
        names=frozenset({normalize_nomenclature_text("Конденсатор 100мкФ")}),
        materials_count=1,
    )
    row = _Row(nomenclature_key="", code="OTHER", name="Конденсатор  100мкФ")
    assert stock_row_matches_spec(row, index)


def test_does_not_match_unrelated_stock() -> None:
    index = SpecNomenclatureIndex(
        keys=frozenset({"guid-1"}),
        codes=frozenset({normalize_nomenclature_text("ЦБ-00000001")}),
        names=frozenset({normalize_nomenclature_text("Винт M3")}),
        materials_count=3,
    )
    row = _Row(nomenclature_key="other-guid", code="ЦБ-99999999", name="Гайка")
    assert not stock_row_matches_spec(row, index)
