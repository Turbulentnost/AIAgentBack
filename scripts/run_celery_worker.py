"""Запуск Celery worker (Windows/cmd-safe).

На Windows Beat запускается отдельно: python scripts/run_celery_beat.py

Запуск:  python scripts/run_celery_worker.py
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
    if sys.platform == "win32":
        argv = ["worker", "--loglevel=info", "--pool=solo"]
    else:
        argv = ["worker", "--beat", "--loglevel=info"]

    celery_app.worker_main(argv=argv)


if __name__ == "__main__":
    main()
