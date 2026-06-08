from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from app.agents.builder.llm import BuilderLLMError, builder_llm
from app.core.logging import get_logger

logger = get_logger(__name__)

WEATHER_KEYWORDS = ("погод", "weather", "температур", "прогноз", "gismeteo", "метео")


def is_weather_goal(goal: str, requirements: dict[str, Any]) -> bool:
    text = f"{goal} {json.dumps(requirements, ensure_ascii=False)}".lower()
    return any(keyword in text for keyword in WEATHER_KEYWORDS)


def extract_city(goal: str, requirements: dict[str, Any]) -> str | None:
    text_candidates = [goal]
    for item in requirements.get("required_elements") or []:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").lower()
        key = (item.get("key") or "").lower()
        value = (item.get("value") or "").strip()
        if value:
            text_candidates.append(value)
        if not value:
            continue
        if any(token in f"{label} {key}" for token in ("город", "регион", "локац", "city", "location")):
            return _normalize_city(value)
        if any(token in value.lower() for token in ("ростов", "москв", "спб", "санкт", "казан", "екатеринбург")):
            return _normalize_city(value)

    for item in requirements.get("conversation") or []:
        if isinstance(item, dict) and item.get("content"):
            text_candidates.append(str(item["content"]))

    for key in ("inputs", "outputs", "constraints", "workflow_hints"):
        value = requirements.get(key)
        if isinstance(value, str):
            text_candidates.append(value)
        elif isinstance(value, list):
            text_candidates.extend(str(item) for item in value)
        elif isinstance(value, dict):
            text_candidates.append(json.dumps(value, ensure_ascii=False))

    combined = " ".join(text_candidates)
    match = re.search(
        r"(ростов(?:е)?(?:\s|-)+на(?:\s|-)+дону|москв[ае]?|санкт(?:\s|-)+петербург|спб|казан[ьи]|екатеринбург)",
        f"{goal} {combined}".lower(),
    )
    if match:
        return _normalize_city(match.group(1))

    if "ростов" in combined.lower():
        return "Ростов-на-Дону"
    return None


def _normalize_city(value: str) -> str:
    lowered = value.lower()
    if "ростов" in lowered:
        return "Ростов-на-Дону"
    if "москв" in lowered:
        return "Москва"
    if "санкт" in lowered or "спб" in lowered:
        return "Санкт-Петербург"
    if "казан" in lowered:
        return "Казань"
    return value.strip()[:120]


async def fetch_weather(city: str) -> dict[str, Any]:
    url = f"https://wttr.in/{quote(city)}?format=j1&lang=ru"
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "curl/7.88.1"})
        response.raise_for_status()
        return response.json()


def format_weather_text(data: dict[str, Any], city: str) -> str:
    try:
        current = data["current_condition"][0]
        nearest = data.get("nearest_area", [{}])[0]
        area_list = nearest.get("areaName") or []
        area = area_list[0].get("value") if area_list else city
        temp = current.get("temp_C", "?")
        feels = current.get("FeelsLikeC", "?")
        humidity = current.get("humidity", "?")
        wind = current.get("windspeedKmph", "?")
        desc_ru = (current.get("lang_ru") or [{}])[0].get("value")
        desc_en = (current.get("weatherDesc") or [{}])[0].get("value")
        description = desc_ru or desc_en or "без описания"
        return (
            f"Погода в {area} на сегодня\n"
            f"Температура: {temp}°C (ощущается как {feels}°C)\n"
            f"Условия: {description}\n"
            f"Влажность: {humidity}%\n"
            f"Ветер: {wind} км/ч"
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise BuilderLLMError(f"Не удалось разобрать ответ погодного сервиса: {exc}") from exc


async def run_weather_preview(goal: str, requirements: dict[str, Any]) -> dict[str, Any]:
    city = extract_city(goal, requirements)
    if not city:
        return {
            "success": False,
            "preview_type": "weather",
            "error": "Не удалось определить город для пробного запуска",
        }

    try:
        raw = await fetch_weather(city)
        output_text = format_weather_text(raw, city)
        return {
            "success": True,
            "preview_type": "weather",
            "output_text": output_text,
            "city": city,
            "source": "wttr.in",
            "source_url": f"https://wttr.in/{quote(city)}",
        }
    except Exception as exc:
        logger.warning("builder.weather_preview_failed", city=city, error=str(exc))
        return {
            "success": False,
            "preview_type": "weather",
            "city": city,
            "error": f"Не удалось получить погоду: {exc}",
        }


async def run_generic_preview(
    goal: str,
    requirements: dict[str, Any],
    blueprint: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        sample = await builder_llm.generate_preview_sample(
            goal=goal,
            requirements=requirements,
            blueprint=blueprint or {},
        )
        return {
            "success": True,
            "preview_type": "simulated",
            "output_text": sample.output_text,
            "source": "llm_simulation",
        }
    except BuilderLLMError as exc:
        return {
            "success": False,
            "preview_type": "simulated",
            "error": str(exc),
        }


async def run_agent_preview(
    *,
    goal: str,
    requirements: dict[str, Any],
    blueprint: dict[str, Any] | None,
) -> dict[str, Any]:
    if is_weather_goal(goal, requirements):
        return await run_weather_preview(goal, requirements)
    return await run_generic_preview(goal, requirements, blueprint)
