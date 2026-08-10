from __future__ import annotations

import asyncio
import html
import smtplib
from dataclasses import dataclass
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings
from app.services.developer_feedback_email import (
    FeedbackEmailError,
    _find_outlook_store_for_address,
    _cleanup_temp_paths,
    _get_outlook_application,
    _get_outlook_session,
    _outlook_com_thread,
    _write_temp_attachment_file,
    is_ews_configured,
    is_outlook_com_available,
    is_smtp_configured,
)
from app.tools.Outlook.outlook_config import build_outlook_config

OL_FOLDER_INBOX = 6


@dataclass(frozen=True)
class ShiftCompletionTaskView:
    task_type: str
    nomenclature: str
    priority: str
    deadline: str
    deficit: str
    status: str
    result_text: str
    reason: str


@dataclass(frozen=True)
class ShiftCompletionStats:
    total: int
    resolved: int
    incomplete: int
    partial: int
    not_resolved: int
    active: int


@dataclass(frozen=True)
class ShiftCompletionAttachment:
    filename: str
    content: bytes
    content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _status_label(status: str) -> str:
    return {
        "resolved": "Выполнено",
        "partial": "Частично",
        "not_resolved": "Не выполнено",
        "active": "Активно",
    }.get(status, status or "—")


def _priority_label(priority: str) -> str:
    return {
        "urgent": "Срочно",
        "today": "Сегодня",
        "week": "Неделя",
    }.get(priority, priority or "—")


def _task_plain(task: ShiftCompletionTaskView) -> str:
    detail = " · ".join(
        part
        for part in (
            task.task_type,
            task.nomenclature,
            _priority_label(task.priority),
            task.deadline,
            f"дефицит {task.deficit}" if task.deficit else "",
        )
        if part
    )
    reason = f"\n  Основание: {task.reason}" if task.reason else ""
    result = f"\n  Результат: {task.result_text}" if task.result_text else ""
    return f"- {detail} [{_status_label(task.status)}]{result}{reason}"


def _task_html(task: ShiftCompletionTaskView) -> str:
    reason = (
        f"<p style='margin:8px 0 0;color:#fca5a5;'><b>Основание:</b> {html.escape(task.reason)}</p>"
        if task.reason
        else ""
    )
    result = (
        f"<p style='margin:8px 0 0;color:#cbd5e1;'><b>Результат:</b> {html.escape(task.result_text)}</p>"
        if task.result_text
        else ""
    )
    meta = " · ".join(
        html.escape(part)
        for part in (
            task.task_type,
            _priority_label(task.priority),
            task.deadline,
            f"дефицит {task.deficit}" if task.deficit else "",
        )
        if part
    )
    return f"""
      <tr>
        <td style="padding:12px 14px;border:1px solid #30363d;border-radius:12px;background:#161b22;">
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:#58a6ff;">{meta}</div>
          <div style="margin-top:4px;font-size:15px;font-weight:700;color:#f0f6fc;">{html.escape(task.nomenclature)}</div>
          <div style="margin-top:4px;font-size:12px;color:#8b949e;">{html.escape(_status_label(task.status))}</div>
          {result}
          {reason}
        </td>
      </tr>
    """


def _build_bodies(
    *,
    manager_name: str,
    report_date: date,
    stats: ShiftCompletionStats,
    tasks: list[ShiftCompletionTaskView],
) -> tuple[str, str]:
    resolved = [task for task in tasks if task.status == "resolved"]
    incomplete = [task for task in tasks if task.status != "resolved"]

    plain = "\n".join(
        [
            f"Отчёт завершения смены за {report_date:%d.%m.%Y}",
            f"Менеджер: {manager_name}",
            "",
            f"Всего: {stats.total}",
            f"Выполнено: {stats.resolved}",
            f"Не выполнено: {stats.incomplete}",
            "",
            "Выполнено:",
            *(["- нет"] if not resolved else [_task_plain(task) for task in resolved]),
            "",
            "Не выполнено:",
            *(["- нет"] if not incomplete else [_task_plain(task) for task in incomplete]),
        ]
    )

    resolved_rows = "".join(_task_html(task) for task in resolved) or (
        "<tr><td style='padding:12px;color:#8b949e;'>Закрытых заданий нет.</td></tr>"
    )
    incomplete_rows = "".join(_task_html(task) for task in incomplete) or (
        "<tr><td style='padding:12px;color:#8b949e;'>Невыполненных заданий нет.</td></tr>"
    )
    html_body = f"""
    <!doctype html>
    <html>
      <body style="margin:0;padding:0;background:#0d1117;color:#c9d1d9;font-family:Arial,Helvetica,sans-serif;">
        <div style="max-width:760px;margin:0 auto;padding:28px 18px;">
          <div style="padding:20px;border:1px solid #30363d;border-radius:18px;background:#161b22;">
            <div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#58a6ff;">Avion · Завершение смены</div>
            <h1 style="margin:8px 0 4px;font-size:24px;line-height:1.25;color:#f0f6fc;">Отчёт за {report_date:%d.%m.%Y}</h1>
            <p style="margin:0;color:#8b949e;">Менеджер: <b style="color:#c9d1d9;">{html.escape(manager_name)}</b></p>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:18px;">
              <tr>
                <td style="padding:14px;border:1px solid #30363d;border-radius:12px;background:#0d1117;">
                  <div style="font-size:12px;color:#8b949e;">Всего сегодня</div>
                  <div style="font-size:28px;font-weight:800;color:#f0f6fc;">{stats.total}</div>
                </td>
                <td width="10"></td>
                <td style="padding:14px;border:1px solid #30363d;border-radius:12px;background:#0d1117;">
                  <div style="font-size:12px;color:#8b949e;">Выполнено</div>
                  <div style="font-size:28px;font-weight:800;color:#3fb950;">{stats.resolved}</div>
                </td>
                <td width="10"></td>
                <td style="padding:14px;border:1px solid #30363d;border-radius:12px;background:#0d1117;">
                  <div style="font-size:12px;color:#8b949e;">Не выполнено</div>
                  <div style="font-size:28px;font-weight:800;color:#f85149;">{stats.incomplete}</div>
                </td>
              </tr>
            </table>
            <h2 style="margin:22px 0 10px;font-size:16px;color:#f0f6fc;">Выполнено</h2>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-spacing:0 8px;">{resolved_rows}</table>
            <h2 style="margin:22px 0 10px;font-size:16px;color:#f0f6fc;">Не выполнено</h2>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-spacing:0 8px;">{incomplete_rows}</table>
          </div>
        </div>
      </body>
    </html>
    """
    return plain, html_body


def _attach_regular_mime(msg: MIMEMultipart, attachment: ShiftCompletionAttachment) -> None:
    maintype, subtype = (
        attachment.content_type.split("/", 1)
        if "/" in attachment.content_type
        else ("application", "octet-stream")
    )
    part = MIMEBase(maintype, subtype)
    part.set_payload(attachment.content)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=attachment.filename)
    msg.attach(part)


def _send_via_smtp(
    *,
    recipient: str,
    subject: str,
    plain: str,
    html_body: str,
    attachment: ShiftCompletionAttachment | None,
) -> None:
    config = build_outlook_config()
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = config.smtp_from
    msg["To"] = recipient
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(plain, "plain", "utf-8"))
    alternative.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alternative)
    if attachment and attachment.content:
        _attach_regular_mime(msg, attachment)
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=60) as server:
        if config.smtp_use_tls:
            server.starttls()
        server.login(config.email, config.password)
        server.sendmail(config.smtp_from, [recipient], msg.as_string())


def _send_via_ews(
    *,
    recipient: str,
    subject: str,
    html_body: str,
    attachment: ShiftCompletionAttachment | None,
) -> None:
    from exchangelib import FileAttachment, HTMLBody, Mailbox, Message

    from app.tools.Outlook.send_meeting_invite import connect_account, load_config

    account = connect_account(load_config(), verify_mailbox=False)
    item = Message(
        account=account,
        subject=subject,
        body=HTMLBody(html_body),
        to_recipients=[Mailbox(email_address=recipient)],
    )
    if attachment and attachment.content:
        item.attach(
            FileAttachment(
                name=attachment.filename,
                content=attachment.content,
                is_inline=False,
            )
        )
    item.send()


def _deliver_via_outlook_inbox(
    *,
    recipient: str,
    subject: str,
    html_body: str,
    attachment: ShiftCompletionAttachment | None,
) -> None:
    session = _get_outlook_session()
    store = _find_outlook_store_for_address(session, recipient)
    if store is None:
        raise FeedbackEmailError(f"В Outlook не найден ящик {recipient}.")
    inbox = store.GetDefaultFolder(OL_FOLDER_INBOX)
    mail = _get_outlook_application().CreateItem(0)
    mail.Subject = subject
    mail.Importance = 2
    mail.BodyFormat = 2
    mail.HTMLBody = html_body
    temp_paths = []
    try:
        if attachment and attachment.content:
            temp_path = _write_temp_attachment_file(
                filename=attachment.filename,
                content=attachment.content,
            )
            temp_paths.append(temp_path)
            mail.Attachments.Add(str(temp_path))
        mail.Save()
        mail.Move(inbox)
    finally:
        _cleanup_temp_paths(temp_paths)


def _send_shift_completion_email_sync(
    *,
    recipient: str,
    manager_name: str,
    report_date: date,
    stats: ShiftCompletionStats,
    tasks: list[ShiftCompletionTaskView],
    attachment: ShiftCompletionAttachment | None,
) -> None:
    plain, html_body = _build_bodies(
        manager_name=manager_name,
        report_date=report_date,
        stats=stats,
        tasks=tasks,
    )
    subject = f"Avion: отчёт завершения смены {manager_name} за {report_date:%d.%m.%Y}"
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
    raise FeedbackEmailError("Отправка отчёта недоступна: настройте SMTP/EWS или откройте Outlook.")


async def send_shift_completion_email(
    *,
    manager_name: str,
    report_date: date,
    stats: ShiftCompletionStats,
    tasks: list[ShiftCompletionTaskView],
    attachment: ShiftCompletionAttachment | None = None,
    recipient: str | None = None,
) -> str:
    target = (
        recipient
        or settings.SHIFT_COMPLETION_RECIPIENT_EMAIL
        or settings.SHIFT_REPORT_RECIPIENT_EMAIL
    ).strip()
    await asyncio.to_thread(
        _send_shift_completion_email_sync,
        recipient=target,
        manager_name=manager_name,
        report_date=report_date,
        stats=stats,
        tasks=tasks,
        attachment=attachment,
    )
    return target
