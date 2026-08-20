"""Загрузка окружения для desktop sidecar (PyInstaller + dev)."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DESKTOP_ENV: dict[str, str] = {
    "DESKTOP_MODE": "1",
    "HOST": "0.0.0.0",
    "BACKEND_BIND_HOST": "0.0.0.0",
    # Без PostgreSQL на клиентских ПК: встроенный SQLite.
    "ONEC_DAILY_SYNC_ENABLED": "false",
    "ONEC_INPROCESS_SYNC_ENABLED": "false",
    "DOCUMENT_ANALYSIS_REQUIRE_AUTH": "true",
    # SQLite может отдавать naive datetime — не валим /auth/me из-за сессии.
    "AUTH_ALLOW_JWT_WITHOUT_SESSION": "true",
}

DESKTOP_FORCE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "DESKTOP_MODE",
        "HOST",
        "BACKEND_BIND_HOST",
        "DESKTOP_SQLITE_PATH",
        "ONEC_DAILY_SYNC_ENABLED",
        "ONEC_INPROCESS_SYNC_ENABLED",
        "DOCUMENT_ANALYSIS_REQUIRE_AUTH",
        "AUTH_ALLOW_JWT_WITHOUT_SESSION",
    }
)


def desktop_config_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(local_app_data) / "AveonAgent"


def desktop_config_path() -> Path:
    return desktop_config_dir() / "config.env"


def desktop_sqlite_path() -> Path:
    return desktop_config_dir() / "aveon_desktop.db"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def ensure_desktop_config_file() -> Path:
    path = desktop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_path = desktop_sqlite_path()
    if not path.is_file():
        lines = [
            "# Конфиг desktop backend (Агент закупок Авион)",
            "# Авторизация работает из коробки — PostgreSQL на клиенте НЕ нужен.",
            "",
            "DESKTOP_MODE=1",
            f"DESKTOP_SQLITE_PATH={sqlite_path}",
            "HOST=0.0.0.0",
            "BACKEND_BIND_HOST=0.0.0.0",
            "ONEC_DAILY_SYNC_ENABLED=false",
            "ONEC_INPROCESS_SYNC_ENABLED=false",
            "DOCUMENT_ANALYSIS_REQUIRE_AUTH=true",
            "AUTH_ALLOW_JWT_WITHOUT_SESSION=true",
            "USE_REMOTE_API=0",
            "",
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_desktop_env() -> Path:
    """Применяет config.env и дефолты desktop до импорта Settings."""
    config_path = ensure_desktop_config_file()
    sqlite_path = desktop_sqlite_path()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    merged = {
        **DEFAULT_DESKTOP_ENV,
        "DESKTOP_SQLITE_PATH": str(sqlite_path),
        **_parse_env_file(config_path),
    }
    # Всегда гарантируем SQLite-путь для installer-only режима.
    if not (merged.get("DESKTOP_SQLITE_PATH") or "").strip():
        merged["DESKTOP_SQLITE_PATH"] = str(sqlite_path)

    for key, value in merged.items():
        if key in DESKTOP_FORCE_ENV_KEYS:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)

    os.environ["DESKTOP_MODE"] = "1"
    os.environ["DESKTOP_SQLITE_PATH"] = merged["DESKTOP_SQLITE_PATH"]
    # JWT без жёсткой проверки naive/aware datetime сессий в SQLite.
    os.environ["AUTH_ALLOW_JWT_WITHOUT_SESSION"] = "true"
    return config_path


__all__ = [
    "DEFAULT_DESKTOP_ENV",
    "DESKTOP_FORCE_ENV_KEYS",
    "desktop_config_path",
    "desktop_sqlite_path",
    "ensure_desktop_config_file",
    "load_desktop_env",
]
