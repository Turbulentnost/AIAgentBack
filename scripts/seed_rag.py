"""Начальное наполнение RAG-коллекций Qdrant (раздел 9 ТЗ).

Демо-режим: печатает данные, которые будут загружены. Реальная загрузка в Qdrant —
TODO (Фаза 3): contractors (импорт из 1С), departments (Приложение Г к СТО-34-238).

Запуск:  python scripts/seed_rag.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows может быть cp1251

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_pochta.services.rag import _DEMO_CONTRACTORS, _DEMO_DEPARTMENTS  # noqa: E402


def main() -> None:
    print("Коллекция contractors:")
    for c in _DEMO_CONTRACTORS:
        print(f"  • {c.contractor_id}: {c.name} {c.emails} → отделы {c.department_codes}")
    print("\nКоллекция departments:")
    for d in _DEMO_DEPARTMENTS:
        print(f"  • {d.department_id}: {d.department_name} (рук. {d.head_name}) — {d.keywords}")
    print("\n[Демо] Реальная загрузка в Qdrant будет добавлена в Фазе 3.")


if __name__ == "__main__":
    main()
