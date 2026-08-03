from __future__ import annotations

from app.tools.onec.exchange_gal_lookup import (
    _name_matches_query,
    _suggestion_score,
    pick_exact_exchange_gal_user,
)


def test_name_matches_query_by_partial_surname() -> None:
    assert _name_matches_query(
        "комарькова",
        "Комарькова Анастасия Эдуардовна",
    )


def test_name_matches_query_by_full_fio() -> None:
    assert _name_matches_query(
        "Комарькова Анастасия Эдуардовна",
        "Комарькова Анастасия Эдуардовна",
    )


def test_name_matches_query_rejects_unrelated_name() -> None:
    assert not _name_matches_query(
        "комарькова",
        "Иванов Иван Иванович",
    )


def test_suggestion_score_prefers_exact_match() -> None:
    exact = _suggestion_score(
        "Комарькова Анастасия Эдуардовна",
        "Комарькова Анастасия Эдуардовна",
    )
    partial = _suggestion_score("комарькова", "Комарькова Анастасия Эдуардовна")
    assert exact > partial


def test_pick_exact_exchange_gal_user_returns_single_strict_match() -> None:
    candidates = [
        {"fio": "Комарькова Анастасия Эдуардовна", "email": "a@turbo-don.ru"},
        {"fio": "Комарькова Мария Сергеевна", "email": "m@turbo-don.ru"},
    ]
    picked = pick_exact_exchange_gal_user(
        "Комарькова Анастасия Эдуардовна",
        candidates,
    )
    assert picked == candidates[0]


def test_pick_exact_exchange_gal_user_returns_single_unique_candidate() -> None:
    candidates = [
        {"fio": "Уставицкий Сергей Владимирович", "email": "s@turbo-don.ru"},
    ]
    picked = pick_exact_exchange_gal_user("уставицкий", candidates)
    assert picked == candidates[0]


def test_pick_exact_exchange_gal_user_returns_none_for_ambiguous_partial_query() -> None:
    candidates = [
        {"fio": "Комарькова Анастасия Эдуардовна", "email": "a@turbo-don.ru"},
        {"fio": "Комарькова Мария Сергеевна", "email": "m@turbo-don.ru"},
    ]
    assert pick_exact_exchange_gal_user("комарькова", candidates) is None


def test_search_result_message_for_unique_and_ambiguous_matches() -> None:
    from app.tools.onec.exchange_gal_lookup import search_result_message

    assert search_result_message(found=True, already_added=False, suggestions_count=1) is None
    assert search_result_message(found=False, already_added=False, suggestions_count=2) == (
        "Выберите участника из списка"
    )
    assert search_result_message(found=False, already_added=False, suggestions_count=0) == (
        "Не найден в Outlook"
    )
