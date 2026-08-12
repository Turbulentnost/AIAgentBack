from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass
from datetime import date

from app.core.config import settings
from app.services.developer_feedback_email import (
    FeedbackEmailError,
    is_ews_configured,
    is_outlook_com_available,
    is_smtp_configured,
)
from app.services.shift_completion_email import (
    ShiftCompletionAttachment,
    _deliver_via_outlook_inbox,
    _outlook_com_thread,
    _send_via_ews,
    _send_via_smtp,
)
from app.tools.Outlook.outlook_config import build_outlook_config


@dataclass(frozen=True)
class ShiftStartSummary:
    total: int
    urgent: int
    week: int


def _build_bodies(
    *,
    manager_name: str,
    region_label: str,
    shift_date: date,
    summary: ShiftStartSummary,
    week_period: str,
) -> tuple[str, str]:
    region_line = f" ({region_label})" if region_label else ""
    plain = "\n".join(
        [
            f"Начало смены за {shift_date:%d.%m.%Y}",
            f"Менеджер: {manager_name}{region_line}",
            "",
            f"Всего заданий: {summary.total}",
            f"Срочные / на сегодня: {summary.urgent}",
            f"На неделю: {summary.week}",
            f"Период: {week_period or '—'}",
            "",
            "Менеджер сформировал новое сменное задание и приступил к работе.",
            "Ход выполнения можно отслеживать в дашборде «Результаты» (live-режим).",
        ]
    )

    html_body = f"""
    <!doctype html>
    <html>
      <body style="margin:0;padding:0;background:#0d1117;color:#c9d1d9;font-family:Arial,Helvetica,sans-serif;">
        <div style="max-width:760px;margin:0 auto;padding:28px 18px;">
          <div style="padding:20px;border:1px solid #30363d;border-radius:18px;background:#161b22;">
            <div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#58a6ff;">Avion · Начало смены</div>
            <h1 style="margin:8px 0 4px;font-size:24px;line-height:1.25;color:#f0f6fc;">Смена за {shift_date:%d.%m.%Y}</h1>
            <p style="margin:0;color:#8b949e;">Менеджер: <b style="color:#c9d1d9;">{html.escape(manager_name)}{html.escape(region_line)}</b></p>
            <p style="margin:10px 0 0;color:#8b949e;">{html.escape(week_period or "Период не указан")}</p>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:18px;">
              <tr>
                <td style="padding:14px;border:1px solid #30363d;border-radius:12px;background:#0d1117;">
                  <div style="font-size:12px;color:#8b949e;">Всего заданий</div>
                  <div style="font-size:28px;font-weight:800;color:#f0f6fc;">{summary.total}</div>
                </td>
                <td width="10"></td>
                <td style="padding:14px;border:1px solid #30363d;border-radius:12px;background:#0d1117;">
                  <div style="font-size:12px;color:#8b949e;">Срочные / сегодня</div>
                  <div style="font-size:28px;font-weight:800;color:#f85149;">{summary.urgent}</div>
                </td>
                <td width="10"></td>
                <td style="padding:14px;border:1px solid #30363d;border-radius:12px;background:#0d1117;">
                  <div style="font-size:12px;color:#8b949e;">На неделю</div>
                  <div style="font-size:28px;font-weight:800;color:#58a6ff;">{summary.week}</div>
                </td>
              </tr>
            </table>
            <p style="margin:18px 0 0;color:#c9d1d9;line-height:1.5;">
              Менеджер сформировал новое сменное задание и приступил к работе.
              Ход выполнения можно отслеживать в дашборде «Результаты» в live-режиме до завершения смены.
            </p>
          </div>
        </div>
      </body>
    </html>
    """
    return plain, html_body


def _send_shift_start_email_sync(
    *,
    recipient: str,
    manager_name: str,
    region_label: str,
    shift_date: date,
    summary: ShiftStartSummary,
    week_period: str,
    attachment: ShiftCompletionAttachment | None,
) -> None:
    plain, html_body = _build_bodies(
        manager_name=manager_name,
        region_label=region_label,
        shift_date=shift_date,
        summary=summary,
        week_period=week_period,
    )
    subject = f"Avion: начало смены — {manager_name} · {shift_date:%d.%m.%Y}"
    config = build_outlook_config()
    errors: list[str] = []

    if is_smtp_configured(config):
        try:
            _send_via_smtp(
                recipient=recipient,
                subject=subject,
                plain=plain,
                html_body=html_body,
                attachment=attachment,
            )
            return
        except Exception as exc:
            errors.append(f"SMTP: {exc}")

    if is_ews_configured(config):
        try:
            _send_via_ews(
                recipient=recipient,
                subject=subject,
                html_body=html_body,
                attachment=attachment,
            )
            return
        except Exception as exc:
            errors.append(f"Exchange: {exc}")

    if is_outlook_com_available():
        try:
            with _outlook_com_thread():
                _deliver_via_outlook_inbox(
                    recipient=recipient,
                    subject=subject,
                    html_body=html_body,
                    attachment=attachment,
                )
            return
        except Exception as exc:
            errors.append(f"Outlook: {exc}")

    if errors:
        raise FeedbackEmailError("; ".join(errors))
    raise FeedbackEmailError("Отправка уведомления недоступна: настройте SMTP/EWS или откройте Outlook.")


async def send_shift_start_email(
    *,
    manager_name: str,
    region_label: str,
    shift_date: date,
    summary: ShiftStartSummary,
    week_period: str = "",
    attachment: ShiftCompletionAttachment | None = None,
    recipient: str | None = None,
) -> str:
    target = (
        recipient
        or settings.SHIFT_COMPLETION_RECIPIENT_EMAIL
        or settings.SHIFT_REPORT_RECIPIENT_EMAIL
    ).strip()
    await asyncio.to_thread(
        _send_shift_start_email_sync,
        recipient=target,
        manager_name=manager_name,
        region_label=region_label,
        shift_date=shift_date,
        summary=summary,
        week_period=week_period,
        attachment=attachment,
    )
    return target
