"""Запуск Celery Beat (расписание фоновых задач).

На Windows Beat запускается отдельно от worker.

Запуск из каталога AIAgentBack:
  python scripts/run_celery_beat.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONPATH", str(ROOT))

from app.workers.celery_app import celery_app  # noqa: E402


def main() -> None:
    celery_app.start(argv=["beat", "--loglevel=info"])


if __name__ == "__main__":
    main()
