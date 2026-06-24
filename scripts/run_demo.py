"""Демо-прогон графа на нескольких тестовых письмах (на заглушках).

Запуск:  python scripts/run_demo.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows может быть cp1251

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_pochta.graph import build_graph  # noqa: E402
from agent_pochta.schemas import Attachment, EmailMessage  # noqa: E402

SAMPLES = [
    EmailMessage(
        message_id="<demo-1@romashka.ru>",
        mailbox="info@turbo-don.ru",
        sender_email="zakaz@romashka.ru",
        sender_name="ООО Ромашка",
        subject="Заказ на поставку и счёт",
        body_text="Просим выставить счёт на поставку продукции по договору.",
        received_at=datetime.now(timezone.utc),
        attachments=[Attachment(filename="zakaz.pdf", mime_type="application/pdf", size_bytes=12000)],
    ),
    EmailMessage(
        message_id="<demo-2@nalog.gov.ru>",
        mailbox="info@turbo-don.ru",
        sender_email="info@nalog.gov.ru",
        sender_name="ИФНС",
        subject="Требование о предоставлении документов",
        body_text="Направляем требование. Претензия по срокам подачи отчётности.",
        received_at=datetime.now(timezone.utc),
    ),
    EmailMessage(
        message_id="<demo-3@spam.example>",
        mailbox="pereadres@turbo-don.ru",
        sender_email="promo@spam.example",
        sender_name="Промо",
        subject="Только сегодня! Выгодное предложение и распродажа",
        body_text="Реклама! Акция! Спешите!",
        received_at=datetime.now(timezone.utc),
    ),
]


def main() -> None:
    app = build_graph()
    for email in SAMPLES:
        result = app.invoke({"email": email})
        print("=" * 70)
        print(f"Письмо: {email.subject!r} от {email.sender_email}")
        print(f"  Статус:    {result.get('status')}")
        spam = result.get("spam")
        if spam:
            print(f"  Спам:      is_spam={spam.is_spam} conf={spam.confidence:.2f} ({spam.reason})")
        routing = result.get("routing")
        if routing:
            print(f"  Отдел:     {routing.department_id} / {routing.department_name} "
                  f"(conf={routing.confidence:.2f}, приоритет={routing.priority})")
        if result.get("summary_ru"):
            print(f"  Обзор:     {result['summary_ru'][:120]}…")
        erp = result.get("erp")
        if erp and erp.success:
            print(f"  1С:        документ {erp.erp_document_number}, задача {erp.erp_task_id}")
        if result.get("human_review"):
            print(f"  ⚠ Human:   {result.get('escalation_reason')}")
        print(f"  Путь:      {' → '.join(result.get('trace', []))}")


if __name__ == "__main__":
    main()
