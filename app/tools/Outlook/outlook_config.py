from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class OutlookConfig:
    email: str
    password: str
    mailbox: str
    server: str
    web_app_url: str
    timezone: str
    smtp_host: str
    smtp_port: int
    smtp_use_tls: bool
    smtp_from: str
    company_calendar: str


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def build_outlook_config() -> OutlookConfig:
    return OutlookConfig(
        email=settings.OUTLOOK_EMAIL.strip(),
        password=settings.OUTLOOK_PASSWORD,
        mailbox=settings.OUTLOOK_MAILBOX.strip(),
        server=settings.OUTLOOK_SERVER.strip(),
        web_app_url=settings.OUTLOOK_WEB_APP_URL.strip(),
        timezone=settings.OUTLOOK_TIMEZONE.strip() or "Europe/Moscow",
        smtp_host=settings.OUTLOOK_SMTP_HOST.strip(),
        smtp_port=settings.OUTLOOK_SMTP_PORT,
        smtp_use_tls=_parse_bool(settings.OUTLOOK_SMTP_TLS),
        smtp_from=(settings.OUTLOOK_SMTP_FROM or settings.OUTLOOK_EMAIL).strip(),
        company_calendar=settings.OUTLOOK_COMPANY_CALENDAR.strip(),
    )


DEFAULT_CONFIG = build_outlook_config()
