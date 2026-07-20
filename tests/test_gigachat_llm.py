"""Тесты GigaChatLLMGateway (OAuth и заголовки без реального API)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx

from agent_pochta.services.gigachat_llm import GigaChatLLMGateway


def test_gigachat_oauth_and_chat_headers():
    gw = GigaChatLLMGateway("dGVzdC1jcmVkZW50aWFscw==", verify_ssl=False)
    oauth_response = MagicMock()
    oauth_response.raise_for_status = MagicMock()
    oauth_response.json.return_value = {"access_token": "tok-123", "expires_at": int(time.time()) + 3600}

    chat_response = MagicMock()
    chat_response.raise_for_status = MagicMock()
    chat_response.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}

    with patch.object(gw._http, "post", side_effect=[oauth_response, chat_response]) as post_mock:
        result = gw._chat_json("system", "user")

    assert result == {"ok": True}
    assert post_mock.call_count == 2
    oauth_call = post_mock.call_args_list[0]
    assert oauth_call.args[0].endswith("/oauth")
    assert oauth_call.kwargs["headers"]["Authorization"] == "Basic dGVzdC1jcmVkZW50aWFscw=="
    assert oauth_call.kwargs["data"]["scope"] == "GIGACHAT_API_PERS"

    chat_call = post_mock.call_args_list[1]
    assert chat_call.kwargs["headers"]["Authorization"] == "Bearer tok-123"
    gw.close()


def test_gigachat_skips_json_response_format():
    gw = GigaChatLLMGateway("dGVzdA==", verify_ssl=False)
    assert gw._use_json_response_format() is False
    gw.close()
