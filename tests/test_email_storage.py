"""Тесты сохранения email без тела письма в PostgreSQL."""



from __future__ import annotations



import json

import uuid

from datetime import datetime, timezone

from unittest.mock import MagicMock



from agent_pochta.db.models import EmailMessageRow

from agent_pochta.db.repository import EmailRepository

from agent_pochta.schemas import Attachment, EmailMessage, ProcessingStatus, SpamResult

from agent_pochta.state import AgentState





def test_upsert_from_state_strips_body_and_attachment_content():

    session = MagicMock()

    repo = EmailRepository(session)

    repo.get_by_message_id = MagicMock(return_value=None)



    email = EmailMessage(

        message_id="<persist@test>",

        mailbox="info@turbo-don.ru",

        sender_email="a@b.ru",

        subject="Тема",

        body_text="Не должен сохраниться",

        body_html="<p>HTML</p>",

        received_at=datetime.now(timezone.utc),

        attachments=[

            Attachment(

                filename="a.pdf",

                mime_type="application/pdf",

                size_bytes=10,

                content=b"secret",

                extracted_text="OCR текст из вложения для LLM",

            )

        ],

    )

    state: AgentState = {

        "email": email,

        "status": ProcessingStatus.DONE,

        "spam": SpamResult(is_spam=False, confidence=0.1, reason="ok"),

    }



    repo.upsert_from_state(state)

    row = session.add.call_args[0][0]

    payload = json.loads(row.raw_payload_json)



    assert payload["body_text"] == ""

    assert "body_html" not in payload

    assert payload["subject"] == "Тема"

    assert payload["attachments"][0]["filename"] == "a.pdf"

    assert payload["attachments"][0]["has_text"] is True

    assert "OCR текст" in payload["attachments"][0]["text_excerpt"]

    assert "content" not in payload["attachments"][0]

    assert len(row.attachments) == 1

    assert row.attachments[0].extracted_text == "OCR текст из вложения для LLM"

    assert row.attachments[0].filename == "a.pdf"





def test_learning_text_from_row_prefers_summary_when_body_empty():

    row = EmailMessageRow(

        id=uuid.uuid4(),

        message_id="<learn@test>",

        received_at=datetime.now(timezone.utc).replace(tzinfo=None),

        mailbox="info@turbo-don.ru",

        sender_email="a@b.ru",

        summary_ru="Краткое резюме письма",

    )

    email = EmailMessage(

        message_id="<learn@test>",

        mailbox="info@turbo-don.ru",

        sender_email="a@b.ru",

        subject="Тема",

        body_text="",

        received_at=datetime.now(timezone.utc),

    )

    assert EmailRepository.learning_text_from_row(row, email) == "Краткое резюме письма"

