"""HTML-оформление писем и календарных приглашений Outlook (шрифт Arial)."""

from __future__ import annotations

import html

from exchangelib.properties import HTMLBody

INVITE_FONT_FAMILY = "Arial, sans-serif"
INVITE_FONT_SIZE = "11pt"


def _escape_html_line(text: str) -> str:
    return html.escape(text, quote=False)


def _plain_text_to_html_fragment(text: str) -> str:
    blocks = text.strip().split("\n\n")
    html_blocks: list[str] = []
    for block in blocks:
        lines = block.split("\n")
        html_blocks.append(
            "<p>" + "<br>".join(_escape_html_line(line) for line in lines) + "</p>"
        )
    return "".join(html_blocks)


def wrap_html_fragment(fragment: str) -> str:
    return (
        f'<div style="font-family: {INVITE_FONT_FAMILY}; font-size: {INVITE_FONT_SIZE};">'
        f"{fragment}</div>"
    )


def plain_text_to_html(text: str) -> HTMLBody:
    """Преобразует plain text в HTMLBody с Arial для Outlook."""
    return HTMLBody(wrap_html_fragment(_plain_text_to_html_fragment(text)))


def append_plain_text_to_html(existing: str, addition: str) -> HTMLBody:
    """Добавляет текст к существующему HTML-телу, новый блок — в Arial."""
    addition_html = wrap_html_fragment(_plain_text_to_html_fragment(addition))
    existing_text = existing.strip()
    if existing_text:
        return HTMLBody(f"{existing_text}<br><br>{addition_html}")
    return HTMLBody(addition_html)
