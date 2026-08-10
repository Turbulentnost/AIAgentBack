from __future__ import annotations

import asyncio
import re
import smtplib
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.core.config import settings
from app.services.developer_feedback_email_resources import (
    LOGO_CONTENT_ID,
    hide_outlook_attachment,
    make_attachment_content_id,
    resolve_platform_logo_bytes,
    set_outlook_attachment_content_id,
)
from app.services.developer_feedback_email_template import (
    FeedbackEmailAttachmentView,
    build_feedback_email_bodies,
)
from app.tools.Outlook.outlook_config import OutlookConfig, build_outlook_config

OL_FOLDER_INBOX = 6


@dataclass(frozen=True)
class FeedbackAttachment:
    filename: str
    content: bytes
    content_type: str | None = None


@dataclass(frozen=True)
class PreparedFeedbackAttachment:
    attachment: FeedbackAttachment
    content_id: str


@dataclass(frozen=True)
class PreparedFeedbackEmail:
    plain: str
    html: str
    logo_bytes: bytes | None
    logo_content_id: str | None
    attachments: tuple[PreparedFeedbackAttachment, ...]


class FeedbackEmailError(Exception):
    pass


def _prepare_feedback_email(
    *,
    author_name: str,
    author_email: str,
    message: str,
    attachments: list[FeedbackAttachment],
) -> PreparedFeedbackEmail:
    logo_bytes = resolve_platform_logo_bytes()
    logo_content_id = LOGO_CONTENT_ID if logo_bytes else None

    prepared_attachments: list[PreparedFeedbackAttachment] = []
    attachment_views: list[FeedbackEmailAttachmentView] = []
    for item in attachments:
        if not item.content:
            continue
        content_id = make_attachment_content_id(item.filename)
        prepared_attachments.append(PreparedFeedbackAttachment(attachment=item, content_id=content_id))
        attachment_views.append(
            FeedbackEmailAttachmentView(
                filename=item.filename,
                size_bytes=len(item.content),
                content_id=content_id,
            )
        )

    plain, html = build_feedback_email_bodies(
        author_name=author_name,
        author_email=author_email,
        message=message,
        attachments=attachment_views,
        logo_cid=logo_content_id,
    )
    return PreparedFeedbackEmail(
        plain=plain,
        html=html,
        logo_bytes=logo_bytes,
        logo_content_id=logo_content_id,
        attachments=tuple(prepared_attachments),
    )


def is_smtp_configured(config: OutlookConfig | None = None) -> bool:
    cfg = config or build_outlook_config()
    return bool(cfg.smtp_host and cfg.smtp_from and cfg.email and cfg.password)


def is_ews_configured(config: OutlookConfig | None = None) -> bool:
    cfg = config or build_outlook_config()
    return bool(cfg.email and cfg.password)


def is_outlook_com_available() -> bool:
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return True


@contextmanager
def _outlook_com_thread():
    """COM (Outlook) must be initialized on the thread that uses it."""
    import pythoncom

    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def is_feedback_send_available() -> bool:
    if is_smtp_configured() or is_ews_configured():
        return True
    if not is_outlook_com_available():
        return False
    try:
        with _outlook_com_thread():
            session = _get_outlook_session()
            return _find_outlook_store_for_address(session, settings.FEEDBACK_RECIPIENT_EMAIL) is not None
    except Exception:
        return False


def _get_outlook_application():
    import win32com.client

    try:
        return win32com.client.GetActiveObject("Outlook.Application")
    except Exception:
        return win32com.client.Dispatch("Outlook.Application")


def _get_outlook_session():
    return _get_outlook_application().Session


def _find_outlook_store_for_address(session, email: str):
    target = email.strip().lower()
    if not target:
        return None
    for index in range(1, session.Stores.Count + 1):
        store = session.Stores.Item(index)
        display_name = str(store.DisplayName or "").lower()
        if target in display_name:
            return store
    return None



def _safe_attachment_filename(filename: str) -> str:
    cleaned = Path(filename).name.strip() or "attachment.bin"
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned)
    return cleaned or "attachment.bin"


def _write_temp_attachment_file(*, filename: str, content: bytes) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="avion-feedback-"))
    temp_path = temp_dir / _safe_attachment_filename(filename)
    temp_path.write_bytes(content)
    return temp_path


def _attach_inline_to_outlook_mail(
    mail,
    *,
    content: bytes,
    filename: str,
    content_id: str,
    hide_from_attachment_bar: bool = False,
) -> Path | None:
    temp_path = _write_temp_attachment_file(filename=filename, content=content)
    outlook_attachment = mail.Attachments.Add(str(temp_path))
    outlook_attachment.DisplayName = _safe_attachment_filename(filename)
    set_outlook_attachment_content_id(outlook_attachment, content_id)
    if hide_from_attachment_bar:
        hide_outlook_attachment(outlook_attachment)
    return temp_path


def _attach_files_to_outlook_mail(
    mail,
    prepared: PreparedFeedbackEmail,
) -> list[Path]:
    temp_paths: list[Path] = []
    if prepared.logo_bytes and prepared.logo_content_id:
        logo_path = _attach_inline_to_outlook_mail(
            mail,
            content=prepared.logo_bytes,
            filename="platform-logo.png",
            content_id=prepared.logo_content_id,
            hide_from_attachment_bar=True,
        )
        if logo_path is not None:
            temp_paths.append(logo_path)

    for item in prepared.attachments:
        temp_path = _attach_inline_to_outlook_mail(
            mail,
            content=item.attachment.content,
            filename=item.attachment.filename,
            content_id=item.content_id,
            hide_from_attachment_bar=True,
        )
        if temp_path is not None:
            temp_paths.append(temp_path)
    return temp_paths


def _cleanup_temp_paths(temp_paths: list[Path]) -> None:
    cleaned_dirs: set[Path] = set()
    for temp_path in temp_paths:
        try:
            temp_path.unlink(missing_ok=True)
            cleaned_dirs.add(temp_path.parent)
        except OSError:
            pass
    for temp_dir in cleaned_dirs:
        try:
            temp_dir.rmdir()
        except OSError:
            pass


def _mime_subtype(content_type: str | None) -> tuple[str, str]:
    if content_type and "/" in content_type:
        return content_type.split("/", 1)
    return "application", "octet-stream"


def _attach_inline_mime(related_part: MIMEMultipart, *, content: bytes, filename: str, content_id: str, content_type: str | None) -> None:
    maintype, subtype = _mime_subtype(content_type)
    if maintype == "image":
        part = MIMEImage(content, _subtype=subtype)
    else:
        part = MIMEBase(maintype, subtype)
        part.set_payload(content)
        encoders.encode_base64(part)
    part.add_header("Content-Disposition", "inline", filename=filename)
    part.add_header("Content-ID", f"<{content_id}>")
    related_part.attach(part)


def _send_via_smtp(
    *,
    config: OutlookConfig,
    recipient: str,
    author_email: str,
    prepared: PreparedFeedbackEmail,
) -> None:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = "Обратная связь по Авиону (Avion)"
    msg["From"] = config.smtp_from
    msg["To"] = recipient
    if author_email:
        msg["Reply-To"] = author_email

    related = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(prepared.plain, "plain", "utf-8"))
    alternative.attach(MIMEText(prepared.html, "html", "utf-8"))
    related.attach(alternative)

    if prepared.logo_bytes and prepared.logo_content_id:
        _attach_inline_mime(
            related,
            content=prepared.logo_bytes,
            filename="platform-logo.png",
            content_id=prepared.logo_content_id,
            content_type="image/png",
        )

    for item in prepared.attachments:
        _attach_inline_mime(
            related,
            content=item.attachment.content,
            filename=item.attachment.filename,
            content_id=item.content_id,
            content_type=item.attachment.content_type,
        )

    msg.attach(related)

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=60) as server:
        if config.smtp_use_tls:
            server.starttls()
        server.login(config.email, config.password)
        server.sendmail(config.smtp_from, [recipient], msg.as_string())


def _send_via_ews(
    *,
    recipient: str,
    author_email: str,
    prepared: PreparedFeedbackEmail,
) -> None:
    from exchangelib import FileAttachment, HTMLBody, Mailbox, Message

    from app.tools.Outlook.send_meeting_invite import connect_account, load_config

    config = load_config()
    account = connect_account(config, verify_mailbox=False)
    item = Message(
        account=account,
        subject="Обратная связь по Авиону (Avion)",
        body=HTMLBody(prepared.html),
        to_recipients=[Mailbox(email_address=recipient)],
    )
    if author_email:
        item.reply_to = [Mailbox(email_address=author_email)]

    if prepared.logo_bytes and prepared.logo_content_id:
        item.attach(
            FileAttachment(
                name="platform-logo.png",
                content=prepared.logo_bytes,
                content_id=prepared.logo_content_id,
                is_inline=True,
            )
        )

    for attachment_item in prepared.attachments:
        item.attach(
            FileAttachment(
                name=attachment_item.attachment.filename,
                content=attachment_item.attachment.content,
                content_id=attachment_item.content_id,
                is_inline=True,
            )
        )

    item.send()


def _deliver_via_outlook_inbox(
    *,
    recipient: str,
    prepared: PreparedFeedbackEmail,
) -> None:
    session = _get_outlook_session()
    store = _find_outlook_store_for_address(session, recipient)
    if store is None:
        raise FeedbackEmailError(
            f"В Outlook не найден ящик {recipient}. "
            "Добавьте OUTLOOK_EMAIL и OUTLOOK_PASSWORD в .env бэкенда."
        )

    inbox = store.GetDefaultFolder(OL_FOLDER_INBOX)
    outlook = _get_outlook_application()
    mail = outlook.CreateItem(0)
    mail.Subject = "Обратная связь по Авиону (Avion)"
    mail.Importance = 2

    temp_paths = _attach_files_to_outlook_mail(mail, prepared)
    try:
        mail.BodyFormat = 2  # olFormatHTML
        mail.HTMLBody = prepared.html
        mail.Save()
        mail.Move(inbox)
    finally:
        _cleanup_temp_paths(temp_paths)


def _send_feedback_email_sync(
    *,
    recipient: str,
    author_name: str,
    author_email: str,
    message: str,
    attachments: list[FeedbackAttachment],
) -> None:
    prepared = _prepare_feedback_email(
        author_name=author_name,
        author_email=author_email,
        message=message,
        attachments=attachments,
    )
    config = build_outlook_config()
    errors: list[str] = []

    if is_smtp_configured(config):
        try:
            _send_via_smtp(
                config=config,
                recipient=recipient,
                author_email=author_email,
                prepared=prepared,
            )
            return
        except Exception as exc:
            errors.append(f"SMTP: {exc}")

    if is_ews_configured(config):
        try:
            _send_via_ews(
                recipient=recipient,
                author_email=author_email,
                prepared=prepared,
            )
            return
        except Exception as exc:
            errors.append(f"Exchange: {exc}")

    if is_outlook_com_available():
        try:
            with _outlook_com_thread():
                _deliver_via_outlook_inbox(
                    recipient=recipient,
                    prepared=prepared,
                )
            return
        except Exception as exc:
            errors.append(f"Outlook: {exc}")

    if errors:
        raise FeedbackEmailError("; ".join(errors))
    raise FeedbackEmailError(
        "Отправка недоступна: откройте Outlook с ящиком получателя "
        "или укажите OUTLOOK_EMAIL и OUTLOOK_PASSWORD в .env."
    )


async def send_developer_feedback_email(
    *,
    author_name: str,
    author_email: str,
    message: str,
    attachments: list[FeedbackAttachment] | None = None,
) -> None:
    recipient = settings.FEEDBACK_RECIPIENT_EMAIL.strip()
    await asyncio.to_thread(
        _send_feedback_email_sync,
        recipient=recipient,
        author_name=author_name,
        author_email=author_email,
        message=message,
        attachments=attachments or [],
    )
