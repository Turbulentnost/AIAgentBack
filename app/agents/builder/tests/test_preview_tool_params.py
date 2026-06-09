from __future__ import annotations

from app.agents.builder.preview_tool_params import (
    extract_location,
    has_substantive_preview_data,
    infer_preview_tool_params,
)


def test_extract_location_from_conversation():
    requirements = {
        "conversation": [
            {"role": "user", "content": "Любые. нужно выводить погоду в ростове на дону"},
        ],
    }
    assert extract_location("погода", requirements) == "Ростов-на-Дону"


def test_infer_fetch_page_params_from_url():
    params = infer_preview_tool_params(
        "fetch_page_via_user_browser",
        "Открой https://example.com/weather и покажи прогноз",
        {},
    )
    assert params is not None
    assert params["url"] == "https://example.com/weather"


def test_infer_fetch_page_without_url_returns_none():
    assert (
        infer_preview_tool_params(
            "fetch_page_via_user_browser",
            "Нужно просмотреть в браузере сайты для погоды",
            {},
        )
        is None
    )


def test_has_substantive_preview_data_only_date():
    assert (
        has_substantive_preview_data(
            {"get_current_date": {"date_iso": "2026-06-09"}},
        )
        is False
    )


def test_has_substantive_preview_data_with_page_text():
    assert has_substantive_preview_data(
        {
            "get_current_date": {"date_iso": "2026-06-09"},
            "fetch_page_via_user_browser": {"text": "Температура 10°C"},
        }
    )
