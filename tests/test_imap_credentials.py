"""Тесты resolve_imap_credentials (per-mailbox IMAP_USER_* / IMAP_PASSWORD_*)."""

from __future__ import annotations

from agent_pochta.imap.client import resolve_imap_credentials
from agent_pochta.services.vault import StubVaultClient, _dotenv_file_values


def test_resolve_imap_credentials_uses_per_mailbox_env(monkeypatch):
    monkeypatch.delenv("IMAP_USERNAME", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    monkeypatch.setenv("IMAP_USER_INFO_TURBO_DON_RU", "info_login")
    monkeypatch.setenv("IMAP_PASSWORD_INFO_TURBO_DON_RU", "info_secret")

    creds = resolve_imap_credentials("info@turbo-don.ru", StubVaultClient())

    assert creds.username == "info_login"
    assert creds.password == "info_secret"


def test_resolve_imap_credentials_falls_back_to_global(monkeypatch):
    monkeypatch.delenv("IMAP_USER_INFO_TURBO_DON_RU", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD_INFO_TURBO_DON_RU", raising=False)
    monkeypatch.setenv("IMAP_USERNAME", "testii")
    monkeypatch.setenv("IMAP_PASSWORD", "shared_pass")

    creds = resolve_imap_credentials("info@turbo-don.ru", StubVaultClient())

    assert creds.username == "testii"
    assert creds.password == "shared_pass"


def test_stub_vault_reads_per_mailbox_keys_from_dotenv_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "IMAP_USER_INFO_TURBO_DON_RU=from_dotenv\n"
        "IMAP_PASSWORD_INFO_TURBO_DON_RU=dotenv_pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent_pochta.services.vault.ENV_FILE", env_file)
    _dotenv_file_values.cache_clear()
    monkeypatch.delenv("IMAP_USER_INFO_TURBO_DON_RU", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD_INFO_TURBO_DON_RU", raising=False)

    creds = resolve_imap_credentials("info@turbo-don.ru", StubVaultClient())

    assert creds.username == "from_dotenv"
    assert creds.password == "dotenv_pass"
