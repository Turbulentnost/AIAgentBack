"""Проверка: desktop bootstrap перезаписывает чужой POSTGRES_* из process env."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_BOOT = ROOT / "app_desktop"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Имитация «чужого» окружения другой машины / старого Settings default
os.environ["POSTGRES_HOST"] = "192.168.1.157"
os.environ["POSTGRES_PASSWORD"] = "1234"
os.environ["POSTGRES_USER"] = "postgres"
os.environ["POSTGRES_DB"] = "wrong_db"

from app_desktop.bootstrap_env import DEFAULT_DESKTOP_ENV, load_desktop_env

config_path = load_desktop_env()
print("config:", config_path)
print("POSTGRES_HOST=", os.environ.get("POSTGRES_HOST"))
print("POSTGRES_PASSWORD=", os.environ.get("POSTGRES_PASSWORD"))
print("POSTGRES_DB=", os.environ.get("POSTGRES_DB"))
print("DESKTOP_MODE=", os.environ.get("DESKTOP_MODE"))

assert os.environ["POSTGRES_HOST"] == DEFAULT_DESKTOP_ENV["POSTGRES_HOST"] or Path(
    os.environ.get("LOCALAPPDATA", ""), "AveonAgent", "config.env"
).is_file()

# После load значения должны совпасть с config/default, а не с 192.168.1.157
assert os.environ["POSTGRES_HOST"] != "192.168.1.157", "force overwrite failed for HOST"
assert os.environ["POSTGRES_PASSWORD"] != "1234" or Path(
    config_path
).read_text(encoding="utf-8").find("POSTGRES_PASSWORD=1234") >= 0
assert os.environ["DESKTOP_MODE"] == "1"
print("ENV FORCE OVERWRITE OK")
