"""Alembic migrate (Windows/cmd-safe). Запуск: python scripts/run_migrate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    from alembic.config import Config
    from alembic import command

    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    print("Alembic: upgrade head — OK")


if __name__ == "__main__":
    main()
