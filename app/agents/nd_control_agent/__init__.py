"""Агент контроля нормативной документации."""

from typing import Any

__all__ = [
    "EnterpriseDepartment",
    "fetch_all_departments_from_1c",
    "filter_departments",
    "format_departments_text",
    "save_departments_report",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from app.services import onec_departments_fetcher

        return getattr(onec_departments_fetcher, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
