"""Канонические названия должностей из узлов структуры предприятия 1С."""

from app.utils.department_classification import normalize_position_name

STRUCTURE_POSITION_NAME_OVERRIDES: dict[str, str] = {
    "6a367bba-2246-11eb-8474-ac1f6b05524d": "Заместитель технического директора по качеству",
    "f9ba8b0b-f524-11f0-9784-6cb31113810e": "Заместитель директора по производству",
    "4668a583-6eb1-11e2-afce-001e67112509": "Заместитель директора по экономической безопасности",
    "98812712-b43b-11ee-9475-6cb31113810e": "Заместитель коммерческого директора по развитию продаж",
    "5d0c35d2-b007-11f0-9723-6cb31113810c": "Заместитель технического директора по сервису",
    "de515771-a574-11ee-9462-6cb31113810e": "Заместитель операционного директора — директор по производству",
    "1bf933b4-f53d-11f0-9784-6cb31113810e": "Заместитель директора по перспективным проектам",
}


def position_display_name(*, external_id: str | None, name: str) -> str:
    if external_id and external_id in STRUCTURE_POSITION_NAME_OVERRIDES:
        return STRUCTURE_POSITION_NAME_OVERRIDES[external_id]
    return normalize_position_name(name)
