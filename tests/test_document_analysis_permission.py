from types import SimpleNamespace

from app.services.document_analysis_permission import is_avion_only_platform_user


def test_is_avion_only_platform_user_by_email():
    user = SimpleNamespace(
        email="rodionov.pavel@local.dev",
        full_name="Родионов Павел",
        is_superuser=False,
    )
    assert is_avion_only_platform_user(user) is True


def test_is_avion_only_platform_user_superuser_excluded():
    user = SimpleNamespace(
        email="rodionov.pavel@local.dev",
        full_name="Родионов Павел",
        is_superuser=True,
    )
    assert is_avion_only_platform_user(user) is False


def test_is_avion_only_platform_user_other_user():
    user = SimpleNamespace(
        email="other@local.dev",
        full_name="Другой Пользователь",
        is_superuser=False,
    )
    assert is_avion_only_platform_user(user) is False
