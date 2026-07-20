"""Тесты parse_json_object для ChatCompletionsLLMGateway."""

from agent_pochta.services.http_llm import ChatCompletionsLLMGateway, parse_json_object


def test_parse_plain_json():
    assert parse_json_object('{"is_spam": false, "confidence": 0.1}')["is_spam"] is False


def test_parse_fenced_json():
    raw = '```json\n{"summary_ru": "Тест"}\n```'
    assert parse_json_object(raw)["summary_ru"] == "Тест"


def test_parse_embedded_json():
    raw = 'Ответ:\n{"department_id": "SALES", "confidence": 0.9}'
    assert parse_json_object(raw)["department_id"] == "SALES"


def test_lm_studio_skips_json_response_format():
    gw = ChatCompletionsLLMGateway("http://192.168.1.157:1234/v1")
    assert gw._use_json_response_format() is False


def test_openrouter_uses_json_response_format():
    gw = ChatCompletionsLLMGateway("https://openrouter.ai/api/v1")
    assert gw._use_json_response_format() is True
