from __future__ import annotations

from app.services.eskd_registration_service import EskdRegistrationService


def test_normalize_designation_from_explicit_value() -> None:
    service = EskdRegistrationService(db=None)  # type: ignore[arg-type]
    assert service._normalize_designation("ABVG.123456.001", None) == "ABVG.123456.001"


def test_normalize_designation_from_filename() -> None:
    service = EskdRegistrationService(db=None)  # type: ignore[arg-type]
    assert service._normalize_designation(None, "ABVG.123456.001.pdf") == "ABVG.123456.001"


def test_normalize_designation_invalid_raises() -> None:
    service = EskdRegistrationService(db=None)  # type: ignore[arg-type]
    try:
        service._normalize_designation("bad designation!", None)
    except ValueError as exc:
        assert "Некорректное обозначение" in str(exc)
    else:
        raise AssertionError("expected ValueError")
