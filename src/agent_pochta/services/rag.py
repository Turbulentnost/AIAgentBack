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
    def search_departments(
        self,
        text: str,
        top_k: int = 3,
        *,
        recipient: str | None = None,
    ) -> list[Department]:
        """Узел 5 — keyword-поиск отделов по тексту письма и адресу получателя."""

    @abstractmethod
    def get_department(self, department_id: str) -> Department | None:
        ...


# Демо-данные (заменяются импортом из 1С / Приложения Г к СТО-34-238)
_DEMO_CONTRACTORS = [
    Contractor(
        contractor_id="C-001",
        name="ООО «Ромашка»",
        emails=["zakaz@romashka.ru"],
        department_codes=["00-000155"],
        contractor_type="клиент",
    ),
    Contractor(
        contractor_id="C-GOV-01",
        name="ИФНС России №1",
        emails=["info@nalog.gov.ru"],
        department_codes=["00-000044", "00-000002"],
        contractor_type="госорган",
    ),
]

_DEMO_DEPARTMENTS = [
    Department(
        department_id="00-000155",
        department_name="Отдел дилерских продаж",
        head_name="Иванов И.И.",
        responsibility="Заказы, коммерческие предложения, договоры поставки",
        keywords=["заказ", "поставка", "счёт", "коммерческое предложение", "договор"],
    ),
    Department(
        department_id="00-000044",
        department_name="Юридический отдел",
        head_name="Петрова П.П.",
        responsibility="Претензии, суды, запросы госорганов",
        keywords=["претензия", "иск", "суд", "фнс", "требование", "запрос"],
    ),
    Department(
        department_id="00-000002",
        department_name="Бухгалтерия",
        head_name="Сидоров С.С.",
        responsibility="Расчётные документы, акты сверки, платежи",
        keywords=["акт", "сверка", "платёж", "оплата", "бухгалтерия"],
    ),
]

_RECIPIENT_KEYWORD_BOOST = 3
_RECIPIENT_BOOST_SKIP = frozenset({"info", "info@turbo-don.ru"})
# Стоп-токены и слишком короткие keywords искажают substring-scoring
# (например "-" есть в каждом @turbo-don.ru, "и"/"по" — почти в любом русском тексте,
# "turbo" есть в домене и поднимает ОТП на каждом письме на @turbo-don.ru).
_KEYWORD_SCORE_STOPWORDS = frozenset({
    "-",
    "—",
    "и",
    "в",
    "на",
    "по",
    "с",
    "к",
    "у",
    "о",
    "от",
    "для",
    "из",
    "или",
    "the",
    "a",
    "an",
    "of",
    "to",
    "info",
    "info@turbo-don.ru",
    "turbo",
    "turbo-don",
    "turbo-don.ru",
})
_MIN_KEYWORD_SCORE_LEN = 3


def _is_scorable_keyword(keyword: str) -> bool:
    kw = keyword.lower().strip()
    if not kw or kw in _KEYWORD_SCORE_STOPWORDS or kw in _RECIPIENT_BOOST_SKIP:
        return False
    if len(kw) < _MIN_KEYWORD_SCORE_LEN:
        return False
    # Одиночная пунктуация / номера вроде "№1" почти никогда не различают отделы
    if all(not ch.isalnum() for ch in kw):
        return False
    return True


def _content_without_recipient_prefix(text_l: str, recipient_l: str, local: str) -> str:
    """Убирает префикс получателя из search-текста, чтобы домен не давал ложных hits."""
    content = text_l
    if recipient_l and content.startswith(recipient_l):
        content = content[len(recipient_l) :].lstrip()
    if local and content.startswith(local):
        content = content[len(local) :].lstrip()
    return content


def score_department_keywords(
    department: Department,
    text: str,
    *,
    recipient: str | None = None,
    recipient_boost: int = _RECIPIENT_KEYWORD_BOOST,
) -> int:
    """Считает совпадения keywords; local-part получателя весит выше (ТЗ прилож. D)."""
    text_l = text.lower()
    recipient_l = (recipient or "").lower().strip()
    local = recipient_l.split("@", 1)[0] if "@" in recipient_l else recipient_l
    content_l = _content_without_recipient_prefix(text_l, recipient_l, local)
    score = 0
    for keyword in department.keywords:
        kw = keyword.lower().strip()
        if not _is_scorable_keyword(kw):
            continue
        if kw in content_l:
            score += 1
        # Boost только по local-part (jurist, uk_omto11) — не по домену @turbo-don.ru
        if local and len(local) >= _MIN_KEYWORD_SCORE_LEN and kw in local:
            score += recipient_boost
    return score


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

    def search_departments(
        self,
        text: str,
        top_k: int = 3,
        *,
        recipient: str | None = None,
    ) -> list[Department]:
        scored: list[tuple[int, Department]] = []
        for d in self._departments.values():
            score = score_department_keywords(d, text, recipient=recipient)
            scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        # Только положительный score: иначе один и тот же «порядок словаря» Qdrant/Stub
        ranked = [d for score, d in scored if score > 0]
        return ranked[:top_k]

    def get_department(self, department_id: str) -> Department | None:
        return self._departments.get(department_id)
