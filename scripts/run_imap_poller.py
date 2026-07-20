"""Один цикл IMAP polling (без Celery Beat).

Запуск:  python scripts/run_imap_poller.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.imap.poller import poll_mailboxes  # noqa: E402


def main() -> None:
    settings = get_settings()
    print(f"IMAP poller started (interval={settings.imap_poll_interval_sec}s)")
    while True:
        result = poll_mailboxes()
        print(f"enqueued={result['enqueued']} errors={result['errors']}")
        time.sleep(settings.imap_poll_interval_sec)


if __name__ == "__main__":
    main()
