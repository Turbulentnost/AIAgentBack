"""Запуск Celery Beat (IMAP polling по расписанию).

На Windows обязателен отдельный процесс (worker + beat в одном не поддерживается).

Запуск:  python scripts/run_celery_beat.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.chdir(ROOT)
sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")])

from agent_pochta.config import print_startup_config  # noqa: E402
from agent_pochta.workers.celery_app import celery_app  # noqa: E402


def main() -> None:
    print_startup_config()
    celery_app.start(argv=["beat", "--loglevel=info"])


if __name__ == "__main__":
    main()
