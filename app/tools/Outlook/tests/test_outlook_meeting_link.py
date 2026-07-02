from __future__ import annotations

from types import SimpleNamespace

from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.outlook_meeting_link import build_outlook_meeting_url, outlook_web_app_base


def _config(**overrides: object) -> OutlookConfig:
    defaults = {
        "email": "svc@turbo-don.ru",
        "password": "secret",
        "mailbox": "",
        "server": "mail.turbo-don.ru",
        "web_app_url": "",
        "timezone": "Europe/Moscow",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_from": "",
    }
    defaults.update(overrides)
    return OutlookConfig(**defaults)  # type: ignore[arg-type]


def test_outlook_web_app_base_from_server() -> None:
    assert outlook_web_app_base(_config()) == "https://mail.turbo-don.ru/owa/"


def test_outlook_web_app_base_from_explicit_url() -> None:
    assert (
        outlook_web_app_base(_config(web_app_url="https://outlook.company.ru/owa"))
        == "https://outlook.company.ru/owa/"
    )


def test_build_outlook_meeting_url_from_query_string() -> None:
    item = SimpleNamespace(
        web_client_read_form_query_string="?ItemID=abc123&exvsurl=1",
        id="abc123",
    )
    url = build_outlook_meeting_url(_config(), item)
    assert url == "https://mail.turbo-don.ru/owa/?ItemID=abc123&exvsurl=1"


def test_build_outlook_meeting_url_from_absolute_query() -> None:
    item = SimpleNamespace(
        web_client_read_form_query_string="https://mail.turbo-don.ru/owa/?ItemID=abc123&exvsurl=1",
        id="abc123",
    )
    url = build_outlook_meeting_url(_config(), item)
    assert url == "https://mail.turbo-don.ru/owa/?ItemID=abc123&exvsurl=1"


def test_build_outlook_meeting_url_fallback_to_item_id() -> None:
    item = SimpleNamespace(web_client_read_form_query_string=None, id="AQMkAD/test+id")
    url = build_outlook_meeting_url(_config(), item)
    assert url == "https://mail.turbo-don.ru/owa/?ItemID=AQMkAD%2Ftest%2Bid&exvsurl=1"
