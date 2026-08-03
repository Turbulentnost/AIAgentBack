"""Тесты разбора IMAP BODYSTRUCTURE для частичной загрузки вложений."""

from agent_pochta.imap.attachment_parts import list_attachment_parts


def test_list_attachment_parts_mixed_text_and_pdf():
    structure = (
        ("TEXT", "PLAIN", ("CHARSET", "UTF-8"), None, None, "7BIT", 120, 3),
        (
            "APPLICATION",
            "PDF",
            ("NAME", "invoice.pdf"),
            None,
            None,
            "BASE64",
            4096,
        ),
        "MIXED",
    )
    parts = list_attachment_parts(structure)
    assert len(parts) == 1
    assert parts[0].part_id == "2"
    assert parts[0].filename == "invoice.pdf"
    assert parts[0].mime_type == "application/pdf"


def test_list_attachment_parts_image_attachment():
    structure = ("IMAGE", "PNG", ("NAME", "scan.png"), None, None, "BASE64", 2048)
    parts = list_attachment_parts(structure)
    assert len(parts) == 1
    assert parts[0].part_id == "1"
    assert parts[0].filename == "scan.png"
    assert parts[0].mime_type == "image/png"
