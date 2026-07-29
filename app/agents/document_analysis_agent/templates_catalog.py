"""Каталог шаблонов Excel для агента анализа документов (Авион)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class AveonTemplate:
    key: str
    role: str
    title: str
    filename: str
    description: str

    @property
    def path(self) -> Path:
        return _TEMPLATES_DIR / self.filename


AVEON_TEMPLATES: tuple[AveonTemplate, ...] = (
    AveonTemplate(
        key="production_schedule",
        role="production_schedule",
        title="График производства",
        filename="шаблон_график_производства.xlsx",
        description="Помесячный план/факт: Заказ / Опытные / Склад",
    ),
    AveonTemplate(
        key="detailed_production_schedule",
        role="detailed_production_schedule",
        title="Детальный график производства",
        filename="шаблон_детальный_график_производства.xlsx",
        description="Отчёт план/факт по дням: стадии П/ф · ОТК · Склад (в расчёт — только П/ф)",
    ),
    AveonTemplate(
        key="shipment_schedule",
        role="shipment_schedule",
        title="График отгрузок",
        filename="шаблон_график_отгрузок.xlsx",
        description="Номенклатура, логистика и даты поставок в Москву",
    ),
    AveonTemplate(
        key="stock",
        role="stock",
        title="Остатки",
        filename="шаблон_остатки.xlsx",
        description="Номенклатура, заказано и остаток на дату",
    ),
)

_BY_KEY = {item.key: item for item in AVEON_TEMPLATES}


def list_aveon_templates() -> list[AveonTemplate]:
    return [item for item in AVEON_TEMPLATES if item.path.is_file()]


def get_aveon_template(key: str) -> AveonTemplate | None:
    item = _BY_KEY.get(key)
    if item is None or not item.path.is_file():
        return None
    return item


def templates_dir() -> Path:
    return _TEMPLATES_DIR
