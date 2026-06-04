from __future__ import annotations

import json
import re
from typing import Any

_ASSESSMENT_KEYS = frozenset({"status", "conclusion", "comment_presence"})

ASSESSMENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "comment_presence": {"type": "string"},
        "detected_attachment_reference": {"type": "boolean"},
        "requires_file_lookup": {"type": "boolean"},
        "status": {"type": "string"},
        "score": {"type": ["number", "null"]},
        "conclusion": {"type": "string"},
        "missing_parts": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "comment_presence",
        "detected_attachment_reference",
        "requires_file_lookup",
        "status",
        "conclusion",
        "missing_parts",
        "evidence",
    ],
    "additionalProperties": False,
}

LM_STUDIO_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "task_completing_assessment",
        "strict": True,
        "schema": ASSESSMENT_JSON_SCHEMA,
    },
}


def _loads_relaxed(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", text)
        payload = json.loads(fixed)
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("Expected JSON object", text, 0)
    return payload


def _is_assessment_payload(payload: dict[str, Any]) -> bool:
    return _ASSESSMENT_KEYS.issubset(payload.keys())


def _brace_objects(text: str) -> list[str]:
    objects: list[str] = []
    for match in re.finditer(r"\{", text):
        start = match.start()
        depth = 0
        in_string = False
        escape = False
        quote = ""
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == quote:
                    in_string = False
                continue
            if char in {'"', "'"}:
                in_string = True
                quote = char
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    objects.append(text[start : index + 1])
                    break
    return objects


def _candidate_strings(text: str) -> list[str]:
    stripped = text.strip()
    candidates: list[str] = []
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL):
        candidates.append(block.strip())
    candidates.extend(_brace_objects(stripped))
    return candidates


def extract_json_payload(text: str) -> dict[str, Any]:
    """Извлекает JSON оценки; предпочитает последний валидный объект с полями агента."""
    if not text.strip():
        raise json.JSONDecodeError("Empty LLM response", text, 0)

    valid: list[dict[str, Any]] = []
    last_error: json.JSONDecodeError | None = None
    for candidate in _candidate_strings(text):
        try:
            payload = _loads_relaxed(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if _is_assessment_payload(payload):
            valid.append(payload)

    if valid:
        return valid[-1]

    for candidate in reversed(_candidate_strings(text)):
        try:
            return _loads_relaxed(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("No JSON object found", text, 0)


def extract_assessment_from_llm_text(*parts: str | None) -> dict[str, Any]:
    combined = "\n".join(part.strip() for part in parts if part and part.strip())
    return extract_json_payload(combined)
