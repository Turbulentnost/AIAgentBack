"""Запуск Celery worker.

На Windows Beat запускается отдельно: python scripts/run_celery_beat.py

Запуск из каталога AIAgentBack:
  python scripts/run_celery_worker.py
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
    if sys.platform == "win32":
        argv = ["worker", "--loglevel=info", "--pool=solo", "-Q", "default,indexing,agents,documents,reports,procurement_poll"]
    else:
        argv = ["worker", "--loglevel=info", "-Q", "default,indexing,agents,documents,reports,procurement_poll"]

    celery_app.worker_main(argv=argv)


if __name__ == "__main__":
    main()
