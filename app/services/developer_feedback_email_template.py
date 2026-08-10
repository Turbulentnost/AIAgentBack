from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

# Dark theme — tokens from AIAgentFront/src/styles/tokens.css [data-theme="dark"]
_COLOR_BG = "#0d1117"
_COLOR_SURFACE = "#161b22"
_COLOR_SURFACE_ELEVATED = "#21262d"
_COLOR_BORDER = "#30363d"
_COLOR_TEXT = "#c9d1d9"
_COLOR_TEXT_STRONG = "#e6edf3"
_COLOR_TEXT_SECONDARY = "#8b949e"
_COLOR_TEXT_MUTED = "#6e7681"
_COLOR_PRIMARY = "#2563eb"
_COLOR_PRIMARY_GLOW = "#58a6ff"
_COLOR_PRIMARY_HOVER = "#79b8ff"
_COLOR_PRIMARY_SOFT = "#1a2744"
_COLOR_INFO_SOFT = "#152238"
_COLOR_SHADOW = "rgba(0, 0, 0, 0.42)"
_RADIUS_SM = "8px"
_RADIUS_MD = "12px"
_RADIUS_LG = "16px"
_RADIUS_PILL = "999px"


@dataclass(frozen=True)
class FeedbackEmailAttachmentView:
    filename: str
    size_bytes: int
    content_id: str


def _escape(value: str) -> str:
    return html.escape(value.strip() or "—", quote=True)


def _format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} КБ"
    return f"{size_bytes / (1024 * 1024):.1f} МБ"


def _format_message_html(message: str) -> str:
    normalized = message.strip() or "—"
    escaped = html.escape(normalized)
    return escaped.replace("\n", "<br />")


def _initials(name: str) -> str:
    parts = [part for part in name.split() if part.strip()]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:1].upper()
    return (parts[0][:1] + parts[1][:1]).upper()


def _sent_at_label() -> str:
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    return now.strftime("%d.%m.%Y, %H:%M (МСК)")


def _section_label(text: str) -> str:
    return (
        f'<p style="margin:0 0 10px;font-size:11px;font-weight:700;line-height:1.3;'
        f"letter-spacing:0.08em;text-transform:uppercase;color:{_COLOR_TEXT_SECONDARY};"
        f'">{text}</p>'
    )


def _avatar_html(initials: str) -> str:
    initials_safe = _escape(initials)
    return f"""
    <!--[if mso]>
    <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" arcsize="100%" fillcolor="{_COLOR_PRIMARY_SOFT}"
      strokecolor="#58a6ff" strokeweight="1px" style="height:40px;width:40px;v-text-anchor:middle;">
      <center style="color:{_COLOR_PRIMARY_GLOW};font-family:Segoe UI,Arial,sans-serif;font-size:14px;font-weight:700;">
        {initials_safe}
      </center>
    </v:roundrect>
    <![endif]-->
    <!--[if !mso]><!-->
    <div style="width:40px;height:40px;border-radius:50%;background-color:{_COLOR_PRIMARY_SOFT};border:1px solid rgba(88,166,255,0.22);text-align:center;line-height:40px;font-size:14px;font-weight:700;color:{_COLOR_PRIMARY_GLOW};">
      {initials_safe}
    </div>
    <!--<![endif]-->
    """


def _logo_html(logo_cid: str | None) -> str:
    if logo_cid:
        return (
            f'<img src="cid:{logo_cid}" width="44" height="44" alt="AI Platform" '
            f'style="display:block;width:44px;height:44px;border:0;border-radius:{_RADIUS_MD};" />'
        )
    return (
        f'<div style="width:44px;height:44px;border-radius:{_RADIUS_MD};background-color:{_COLOR_PRIMARY_SOFT};'
        f'border:1px solid rgba(88,166,255,0.28);text-align:center;line-height:44px;font-size:18px;font-weight:800;color:{_COLOR_PRIMARY_GLOW};">'
        f"A</div>"
    )


def build_feedback_email_bodies(
    *,
    author_name: str,
    author_email: str,
    message: str,
    attachments: list[FeedbackEmailAttachmentView] | None = None,
    logo_cid: str | None = None,
) -> tuple[str, str]:
    """Return (plain_text, html) bodies for developer feedback email."""
    manager_line = author_name.strip() or "—"
    email_line = author_email.strip() or "—"
    message_line = message.strip() or "—"
    attachment_items = attachments or []

    plain_attachments = ""
    if attachment_items:
        lines = []
        for item in attachment_items:
            size = _format_file_size(item.size_bytes)
            lines.append(f"  • {item.filename} ({size})")
        plain_attachments = "\n\nВложения:\n" + "\n".join(lines)

    plain = (
        "Обратная связь по агенту закупок Авион\n"
        f"Отправлено: {_sent_at_label()}\n\n"
        f"Менеджер: {manager_line}\n"
        f"Email: {email_line}\n\n"
        "Сообщение:\n"
        f"{message_line}\n"
        f"{plain_attachments}\n"
        "\n—\n"
        "AI Platform · Агент закупок Авион"
    ).strip()

    html_body = _build_html_body(
        manager_line=manager_line,
        email_line=email_line,
        message_html=_format_message_html(message),
        attachment_items=attachment_items,
        logo_cid=logo_cid,
    )
    return plain, html_body


def _build_attachment_rows(attachment_items: list[FeedbackEmailAttachmentView]) -> str:
    rows: list[str] = []
    for index, item in enumerate(attachment_items):
        name = _escape(item.filename)
        size = _escape(_format_file_size(item.size_bytes))
        cid = html.escape(item.content_id, quote=True)
        cid_href = f"cid:{cid}"
        bottom_pad = "0" if index == len(attachment_items) - 1 else "10px"
        rows.append(
            f"""
            <tr>
              <td style="padding:0 0 {bottom_pad};">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                  style="border-collapse:separate;table-layout:fixed;background-color:{_COLOR_SURFACE};border:1px solid {_COLOR_BORDER};border-radius:{_RADIUS_SM};">
                  <tr>
                    <td width="52" style="width:52px;min-width:52px;max-width:52px;padding:12px 0 12px 14px;vertical-align:top;">
                      <table role="presentation" width="32" height="32" cellpadding="0" cellspacing="0" border="0"
                        style="width:32px;height:32px;border-collapse:separate;background-color:{_COLOR_INFO_SOFT};border:1px solid {_COLOR_BORDER};border-radius:{_RADIUS_SM};">
                        <tr>
                          <td width="32" height="32" align="center" valign="middle"
                            style="width:32px;height:32px;font-size:14px;line-height:32px;color:{_COLOR_PRIMARY_GLOW};">
                            &#128206;
                          </td>
                        </tr>
                      </table>
                    </td>
                    <td style="padding:12px 14px 12px 0;vertical-align:top;">
                      <a href="{cid_href}" style="display:block;text-decoration:none;color:inherit;">
                        <span style="display:block;font-size:13px;font-weight:600;line-height:1.45;color:{_COLOR_TEXT_STRONG};word-break:normal;overflow-wrap:break-word;mso-line-height-rule:exactly;">
                          {name}
                        </span>
                        <span style="display:block;margin-top:4px;font-size:12px;line-height:1.45;color:{_COLOR_TEXT_MUTED};white-space:nowrap;mso-line-height-rule:exactly;">
                          {size} · <span style="color:{_COLOR_PRIMARY_HOVER};font-weight:600;text-decoration:underline;">Открыть файл</span>
                          <span style="color:{_COLOR_PRIMARY_GLOW};margin-left:4px;">&#8594;</span>
                        </span>
                      </a>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            """
        )
    return "".join(rows)


def _build_html_body(
    *,
    manager_line: str,
    email_line: str,
    message_html: str,
    attachment_items: list[FeedbackEmailAttachmentView],
    logo_cid: str | None,
) -> str:
    manager_safe = _escape(manager_line)
    email_safe = _escape(email_line)
    initials = _initials(manager_line)
    sent_at = _escape(_sent_at_label())
    attachment_count = len(attachment_items)
    logo_block = _logo_html(logo_cid)
    avatar_block = _avatar_html(initials)

    attachments_block = ""
    if attachment_items:
        attachments_block = f"""
        <tr>
          <td style="padding:0 22px 18px;">
            {_section_label(f"Вложения · {attachment_count}")}
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              {_build_attachment_rows(attachment_items)}
            </table>
          </td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="ru" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="color-scheme" content="dark" />
  <meta name="supported-color-schemes" content="dark" />
  <title>Обратная связь по Авиону</title>
  <!--[if mso]>
  <style type="text/css">
    body, table, td {{ font-family: Segoe UI, Arial, sans-serif !important; }}
  </style>
  <![endif]-->
</head>
<body bgcolor="{_COLOR_BG}" style="margin:0;padding:0;background-color:{_COLOR_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;color:{_COLOR_TEXT};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_COLOR_BG}" style="background-color:{_COLOR_BG};">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <!--[if mso]>
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="{_COLOR_SURFACE_ELEVATED}" style="border:1px solid {_COLOR_BORDER};">
        <![endif]-->
        <!--[if !mso]><!-->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_COLOR_SURFACE_ELEVATED}"
          style="max-width:600px;border-collapse:separate;background-color:{_COLOR_SURFACE_ELEVATED};border:1px solid {_COLOR_BORDER};border-radius:{_RADIUS_LG};overflow:hidden;box-shadow:0 24px 64px {_COLOR_SHADOW};">
        <!--<![endif]-->

          <!-- Header -->
          <tr>
            <td bgcolor="{_COLOR_SURFACE}" style="padding:0;background-color:{_COLOR_SURFACE};border-bottom:1px solid {_COLOR_BORDER};">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td width="4" bgcolor="{_COLOR_PRIMARY}" style="width:4px;background-color:{_COLOR_PRIMARY};font-size:0;line-height:0;">&nbsp;</td>
                  <td style="padding:20px 22px 18px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td style="padding:0 14px 0 0;vertical-align:middle;">
                          {logo_block}
                        </td>
                        <td style="vertical-align:middle;">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                            <tr>
                              <td style="padding-bottom:8px;">
                                <span style="display:inline-block;padding:4px 10px;background-color:{_COLOR_INFO_SOFT};border:1px solid rgba(88,166,255,0.22);border-radius:{_RADIUS_PILL};font-size:11px;font-weight:700;line-height:1.3;letter-spacing:0.06em;text-transform:uppercase;color:{_COLOR_PRIMARY_HOVER};">
                                  AI Platform · Avion
                                </span>
                              </td>
                            </tr>
                            <tr>
                              <td>
                                <h1 style="margin:0;font-size:20px;font-weight:700;line-height:1.3;color:{_COLOR_TEXT_STRONG};">
                                  Обратная связь
                                </h1>
                              </td>
                            </tr>
                            <tr>
                              <td style="padding-top:4px;">
                                <p style="margin:0;font-size:13px;line-height:1.45;color:{_COLOR_TEXT_SECONDARY};">
                                  Агент закупок Авион
                                </p>
                              </td>
                            </tr>
                          </table>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Meta -->
          <tr>
            <td bgcolor="{_COLOR_INFO_SOFT}" style="padding:12px 22px;background-color:{_COLOR_INFO_SOFT};border-bottom:1px solid {_COLOR_BORDER};">
              <p style="margin:0;font-size:12px;line-height:1.45;color:{_COLOR_PRIMARY_HOVER};">
                <span style="font-weight:700;color:{_COLOR_TEXT_STRONG};">Получено:</span> {sent_at}
              </p>
            </td>
          </tr>

          <!-- Manager -->
          <tr>
            <td style="padding:18px 22px 0;">
              {_section_label("От менеджера")}
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_COLOR_SURFACE}"
                style="border-collapse:separate;background-color:{_COLOR_SURFACE};border:1px solid {_COLOR_BORDER};border-radius:{_RADIUS_MD};">
                <tr>
                  <td style="padding:14px 16px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td style="padding:0 12px 0 0;vertical-align:middle;">
                          {avatar_block}
                        </td>
                        <td style="vertical-align:middle;">
                          <p style="margin:0 0 3px;font-size:15px;font-weight:700;line-height:1.35;color:{_COLOR_TEXT_STRONG};">
                            {manager_safe}
                          </p>
                          <p style="margin:0;font-size:13px;line-height:1.45;">
                            <a href="mailto:{email_safe}" style="color:{_COLOR_PRIMARY_GLOW};text-decoration:none;font-weight:600;">{email_safe}</a>
                          </p>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Message -->
          <tr>
            <td style="padding:16px 22px 0;">
              {_section_label("Сообщение")}
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{_COLOR_BG}"
                style="border-collapse:separate;background-color:{_COLOR_BG};border:1px solid {_COLOR_BORDER};border-radius:{_RADIUS_MD};">
                <tr>
                  <td style="padding:14px 16px;font-size:14px;line-height:1.65;color:{_COLOR_TEXT};word-break:break-word;">
                    {message_html}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          {attachments_block}

          <!-- Footer -->
          <tr>
            <td style="padding:18px 22px 22px;border-top:1px solid {_COLOR_BORDER};">
              <p style="margin:0 0 6px;font-size:12px;line-height:1.55;color:{_COLOR_TEXT_MUTED};">
                Письмо отправлено через виджет обратной связи на странице агента Авион.
              </p>
              <p style="margin:0;font-size:12px;line-height:1.55;color:{_COLOR_TEXT_MUTED};">
                Ответить менеджеру:
                <a href="mailto:{email_safe}" style="color:{_COLOR_PRIMARY_GLOW};text-decoration:none;font-weight:600;">{email_safe}</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
