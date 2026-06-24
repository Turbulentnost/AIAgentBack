"""RAG-контур (Qdrant) — коллекции contractors и departments (раздел 5.3, 9 ТЗ).

Права агента: только чтение. Запись — через авторизованных пользователей платформы.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent_pochta.schemas import Contractor, Department


class RAGService(ABC):
    @abstractmethod
    def find_contractor_by_email(self, email: str) -> Contractor | None:
        """Узел 3 — точный поиск контрагента по email-адресу."""

    @abstractmethod
    def search_departments(self, text: str, top_k: int = 3) -> list[Department]:
        """Узел 5 — семантический поиск релевантных отделов по тексту письма."""

    @abstractmethod
    def get_department(self, department_id: str) -> Department | None:
        ...


# Демо-данные (заменяются импортом из 1С / Приложения Г к СТО-34-238)
_DEMO_CONTRACTORS = [
    Contractor(
        contractor_id="C-001",
        name="ООО «Ромашка»",
        emails=["zakaz@romashka.ru"],
        department_codes=["SALES"],
        contractor_type="клиент",
    ),
    Contractor(
        contractor_id="C-GOV-01",
        name="ИФНС России №1",
        emails=["info@nalog.gov.ru"],
        department_codes=["LEGAL", "FINANCE"],
        contractor_type="госорган",
    ),
]

_DEMO_DEPARTMENTS = [
    Department(
        department_id="SALES",
        department_name="Отдел продаж",
        head_name="Иванов И.И.",
        responsibility="Заказы, коммерческие предложения, договоры поставки",
        keywords=["заказ", "поставка", "счёт", "коммерческое предложение", "договор"],
    ),
    Department(
        department_id="LEGAL",
        department_name="Юридический отдел",
        head_name="Петрова П.П.",
        responsibility="Претензии, суды, запросы госорганов",
        keywords=["претензия", "иск", "суд", "фнс", "требование", "запрос"],
    ),
    Department(
        department_id="FINANCE",
        department_name="Финансовый отдел",
        head_name="Сидоров С.С.",
        responsibility="Расчётные документы, акты сверки, платежи",
        keywords=["акт", "сверка", "платёж", "оплата", "бухгалтерия"],
    ),
]


class StubRAGService(RAGService):
    """In-memory заглушка RAG поверх демо-данных (без Qdrant)."""

    def __init__(self) -> None:
        self._contractors = _DEMO_CONTRACTORS
        self._departments = {d.department_id: d for d in _DEMO_DEPARTMENTS}

    def find_contractor_by_email(self, email: str) -> Contractor | None:
        email = email.lower().strip()
        for c in self._contractors:
            if email in [e.lower() for e in c.emails]:
                return c
        return None

    def search_departments(self, text: str, top_k: int = 3) -> list[Department]:
        text_l = text.lower()
        scored: list[tuple[int, Department]] = []
        for d in self._departments.values():
            score = sum(1 for kw in d.keywords if kw in text_l)
            scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        # Если совпадений нет — возвращаем всё (LLM решит), иначе топ по совпадениям
        ranked = [d for score, d in scored if score > 0] or [d for _, d in scored]
        return ranked[:top_k]

    def get_department(self, department_id: str) -> Department | None:
        return self._departments.get(department_id)
