"""Постановка тестового письма в очередь Celery (только для локальной отладки).

По умолчанию скрипт заблокирован — не запускайте в production.
Для явного запуска:  set ALLOW_DEMO_ENQUEUE=1

Запуск (нужны RabbitMQ + worker):
  docker compose up -d
  celery -A agent_pochta.workers.celery_app worker --loglevel=info
  set ALLOW_DEMO_ENQUEUE=1 && python scripts/enqueue_demo.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.workers.tasks import process_email_task  # noqa: E402


def main() -> None:
    if os.environ.get("ALLOW_DEMO_ENQUEUE", "").strip() not in {"1", "true", "yes"}:
        print(
            "enqueue_demo.py отключён. Для локальной отладки: set ALLOW_DEMO_ENQUEUE=1",
            file=sys.stderr,
        )
        sys.exit(1)

    email = EmailMessage(
        message_id=f"<enqueue-demo-{datetime.now(timezone.utc).timestamp()}@local>",
        mailbox="info@turbo-don.ru",
        sender_email="zakaz@romashka.ru",
        sender_name="ООО Ромашка",
        subject="Заказ на поставку",
        body_text="Просим выставить счёт на поставку продукции.",
        received_at=datetime.now(timezone.utc),
    )
    async_result = process_email_task.delay(email.model_dump(mode="json"))
    print(f"Задача поставлена в очередь: id={async_result.id}")
    print("Результат появится после обработки worker-ом.")


if __name__ == "__main__":
    main()
