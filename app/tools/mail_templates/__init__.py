"""Plain-text шаблоны писем и приглашений (→ HTML через outlook_html_body)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parent
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


@lru_cache(maxsize=64)
def _read_template(name: str) -> str:
    path = _TEMPLATES_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Mail template not found: {path}")
    return path.read_text(encoding="utf-8")


def invite_agent_footer() -> str:
    return _read_template("invite_footer").strip()


# Обратная совместимость с существующими импортами.
INVITE_AGENT_FOOTER = invite_agent_footer()


def default_reschedule_comment() -> str:
    return _read_template("reschedule_comment").strip()


def render_mail_template(name: str, **context: str) -> str:
    """Подставляет {{key}} в .txt-шаблон и убирает лишние пустые строки."""
    text = _read_template(name)
    for key, value in context.items():
        text = text.replace(f"{{{{{key}}}}}", value or "")
    text = _PLACEHOLDER_RE.sub("", text)
    lines = text.splitlines()
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                collapsed.append("")
            continue
        blank_run = 0
        collapsed.append(line.rstrip())
    return "\n".join(collapsed).strip()


__all__ = [
    "INVITE_AGENT_FOOTER",
    "default_reschedule_comment",
    "invite_agent_footer",
    "render_mail_template",
]
