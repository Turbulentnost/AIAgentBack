"""Загрузка окружения для desktop sidecar (PyInstaller + dev)."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DESKTOP_ENV: dict[str, str] = {
    "DESKTOP_MODE": "1",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "postgres",
    "POSTGRES_DB": "ai_agents",
    "DOCUMENT_ANALYSIS_REQUIRE_AUTH": "true",
}


def desktop_config_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(local_app_data) / "AveonAgent"


def desktop_config_path() -> Path:
    return desktop_config_dir() / "config.env"


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
    if not path.is_file():
        lines = [
            "# Конфиг desktop backend (Агент закупок Авион)",
            "# При необходимости измените параметры PostgreSQL ниже.",
            "",
        ]
        lines.extend(f"{key}={value}" for key, value in DEFAULT_DESKTOP_ENV.items())
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_desktop_env() -> Path:
    """Применяет config.env и дефолты desktop до импорта Settings."""
    config_path = ensure_desktop_config_file()
    merged = {**DEFAULT_DESKTOP_ENV, **_parse_env_file(config_path)}
    for key, value in merged.items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("DESKTOP_MODE", "1")
    return config_path


__all__ = ["DEFAULT_DESKTOP_ENV", "desktop_config_path", "ensure_desktop_config_file", "load_desktop_env"]
