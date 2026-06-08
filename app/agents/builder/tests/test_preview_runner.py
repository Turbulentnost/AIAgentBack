from __future__ import annotations

from app.agents.builder.preview_runner import extract_city, format_weather_text, is_weather_goal


def test_is_weather_goal():
    assert is_weather_goal("Нужна погода на сегодня", {}) is True


def test_extract_city_from_requirements():
    requirements = {
        "required_elements": [
            {
                "key": "sites",
                "label": "Сайты",
                "value": "любые сайты для погоды в Ростове-на-Дону в текстовом виде",
            }
        ]
    }
    assert extract_city("погода", requirements) == "Ростов-на-Дону"


def test_extract_city_from_conversation_with_spaces():
    requirements = {
        "required_elements": [
            {"key": "sites", "label": "Сайты", "value": "Яндекс Погода, Gismeteo"},
        ],
        "conversation": [
            {"role": "user", "content": "Любые. нужно выводить погоду в ростове на дону"},
        ],
    }
    assert extract_city("Нужно просмотреть в браузере сайты для погоды", requirements) == "Ростов-на-Дону"


def test_format_weather_text():
    data = {
        "nearest_area": [{"areaName": [{"value": "Rostov-on-Don"}]}],
        "current_condition": [
            {
                "temp_C": "5",
                "FeelsLikeC": "2",
                "humidity": "70",
                "windspeedKmph": "12",
                "lang_ru": [{"value": "Облачно"}],
            }
        ],
    }
    text = format_weather_text(data, "Ростов-на-Дону")
    assert "5°C" in text
    assert "Облачно" in text
