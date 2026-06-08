from __future__ import annotations

import json
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.builder.prompts import BUILDER_SYSTEM_PROMPT
from app.agents.builder.tools import list_available_tools_catalog
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.gateway import llm_gateway

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class RequiredElementLLM(BaseModel):
    key: str
    label: str
    question: str
    required: bool = True
    value: str | None = None
    status: str = "pending"


class ClarificationLLMResponse(BaseModel):
    ready_to_plan: bool = False
    assistant_message: str = Field(..., min_length=1)
    extracted_requirements: dict[str, Any] = Field(default_factory=dict)
    required_elements: list[RequiredElementLLM] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)


class ElementAnswersLLMResponse(BaseModel):
    elements: list[RequiredElementLLM] = Field(default_factory=list)
    extracted_requirements: dict[str, Any] = Field(default_factory=dict)


class PreviewSampleLLMResponse(BaseModel):
    output_text: str = Field(..., min_length=1)


class PlanStepLLM(BaseModel):
    title: str
    description: str


class PlanLLMResponse(BaseModel):
    steps: list[PlanStepLLM] = Field(default_factory=list)
    summary: str = ""


class BlueprintLLMResponse(BaseModel):
    agent_name: str
    purpose: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    knowledge_bases: list[str] = Field(default_factory=list)
    workflow_steps: list[str] = Field(default_factory=list)
    human_approval: bool = False
    human_approval_rules: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str = ""
    developer_prompt: str = ""
    test_cases: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    @field_validator("workflow_steps", "tools", "knowledge_bases", "constraints", mode="before")
    @classmethod
    def _normalize_string_lists(cls, value: Any) -> list[str]:
        return normalize_string_list(value)

    @field_validator("human_approval_rules", "test_cases", mode="before")
    @classmethod
    def _normalize_dict_lists(cls, value: Any) -> list[dict[str, Any]]:
        return normalize_dict_list(value)


class BuilderLLMError(Exception):
    pass


class BuilderLLM:
    """LLM для конструктора агентов.

    Не использует VISION_LM_STUDIO_MODEL: OCR остаётся на qwen/qwen3.5-9b
    (см. document_processing/parsers/pdf_parser.py и imageparser.py).
    """

    @property
    def configured(self) -> bool:
        return bool(self._claude_configured() or self._fallback_configured())

    def _claude_configured(self) -> bool:
        return bool(settings.OPENAI_API_KEY_CLAUDE and settings.AGENT_BUILDER_CLAUDE_MODEL)

    def _fallback_configured(self) -> bool:
        base_url = settings.AGENT_BUILDER_FALLBACK_BASE_URL or settings.LLM_GATEWAY_BASE_URL
        return bool(base_url and settings.AGENT_BUILDER_FALLBACK_MODEL)

    async def clarify(
        self,
        *,
        goal: str,
        conversation: list[dict[str, str]],
        requirements: dict[str, Any],
    ) -> ClarificationLLMResponse:
        tools_preview = [item["name"] for item in list_available_tools_catalog() if item["implemented"]][:20]
        user_prompt = {
            "task": "clarify_requirements",
            "goal": goal,
            "known_requirements": requirements,
            "conversation": conversation,
            "available_tools": tools_preview,
            "instructions": (
                "Сначала определи полный список required_elements — обязательных элементов для проектирования агента. "
                "Каждый элемент: key, label, question, required, value, status (pending|filled). "
                "Внимательно прочитай ПОСЛЕДНЕЕ сообщение пользователя в conversation: "
                "если там есть ответ на вопрос — заполни value и поставь status=filled для соответствующего элемента. "
                "НЕ задавай повторно вопросы, на которые пользователь уже ответил. "
                "В clarifying_questions включай ТОЛЬКО вопросы по элементам со status=pending. "
                "В assistant_message кратко перечисли, что уже понятно, и что ещё нужно (если есть). "
                "ready_to_plan=true ТОЛЬКО если все required элементы заполнены (status=filled). "
                "До этого момента НЕ переходи к планированию и анализу. "
                "В extracted_requirements обновляй подтверждённые поля: "
                "inputs, outputs, human_approval, knowledge_bases, constraints, roles, workflow_hints."
            ),
            "response_schema": {
                "ready_to_plan": "boolean",
                "assistant_message": "string",
                "extracted_requirements": "object",
                "required_elements": [
                    {
                        "key": "string",
                        "label": "string",
                        "question": "string",
                        "required": True,
                        "value": "string|null",
                        "status": "pending|filled",
                    }
                ],
                "clarifying_questions": ["string"],
            },
        }
        return await self._chat_json(
            ClarificationLLMResponse,
            user_content=json.dumps(user_prompt, ensure_ascii=False),
        )

    async def extract_element_answers(
        self,
        *,
        goal: str,
        user_message: str,
        required_elements: list[dict[str, Any]],
        conversation: list[dict[str, str]],
    ) -> ElementAnswersLLMResponse:
        user_prompt = {
            "task": "extract_requirement_values",
            "goal": goal,
            "user_message": user_message,
            "required_elements": required_elements,
            "conversation": conversation[-8:],
            "instructions": (
                "Пользователь ответил на уточняющие вопросы. "
                "Для каждого required_element: если ответ есть в user_message или в conversation, "
                "заполни value конкретным значением из ответа и поставь status=filled. "
                "Сохраняй key и label без изменений. "
                "Если ответа нет — оставь status=pending и value=null. "
                "Пример: «любые сайты погоды в Ростове в текстовом виде» заполняет "
                "элементы про сайты (value: любые сайты погоды) и формат (value: текстовый). "
                "В extracted_requirements продублируй ключевые поля: inputs, outputs, constraints."
            ),
            "response_schema": {
                "elements": [
                    {
                        "key": "string",
                        "label": "string",
                        "question": "string",
                        "required": True,
                        "value": "string|null",
                        "status": "pending|filled",
                    }
                ],
                "extracted_requirements": "object",
            },
        }
        return await self._chat_json(
            ElementAnswersLLMResponse,
            user_content=json.dumps(user_prompt, ensure_ascii=False),
        )

    async def generate_preview_sample(
        self,
        *,
        goal: str,
        requirements: dict[str, Any],
        blueprint: dict[str, Any],
    ) -> PreviewSampleLLMResponse:
        user_prompt = {
            "task": "generate_preview_sample",
            "goal": goal,
            "requirements": requirements,
            "blueprint": blueprint,
            "instructions": (
                "Сгенерируй пример реального результата работы будущего агента в текстовом виде. "
                "Это пробный запуск для пользователя перед сохранением blueprint. "
                "Используй конкретные данные из requirements, не пиши общие фразы."
            ),
            "response_schema": {"output_text": "string"},
        }
        return await self._chat_json(
            PreviewSampleLLMResponse,
            user_content=json.dumps(user_prompt, ensure_ascii=False),
        )

    async def generate_plan(
        self,
        *,
        goal: str,
        requirements: dict[str, Any],
    ) -> PlanLLMResponse:
        user_prompt = {
            "task": "create_design_plan",
            "goal": goal,
            "requirements": requirements,
            "instructions": (
                "Составь практичный план проектирования агента из 4-7 шагов. "
                "Каждый шаг должен быть выполнимым и вести к готовому blueprint. "
                "Верни ТОЛЬКО JSON, без markdown-заголовков и пояснений вне JSON."
            ),
            "response_schema": {
                "steps": [{"title": "string", "description": "string"}],
                "summary": "string",
            },
            "example_response": {
                "steps": [
                    {"title": "Сбор требований", "description": "Уточнить входы и выходы"},
                    {"title": "Подбор инструментов", "description": "Выбрать tools платформы"},
                ],
                "summary": "Краткое описание плана",
            },
        }
        return await self._chat_json(
            PlanLLMResponse,
            user_content=json.dumps(user_prompt, ensure_ascii=False),
        )

    async def generate_blueprint(
        self,
        *,
        goal: str,
        requirements: dict[str, Any],
        plan_steps: list[dict[str, str]],
    ) -> BlueprintLLMResponse:
        tools_preview = [
            {"name": item["name"], "description": item["description"]}
            for item in list_available_tools_catalog()
            if item["implemented"]
        ]
        user_prompt = {
            "task": "propose_agent_blueprint",
            "goal": goal,
            "requirements": requirements,
            "plan_steps": plan_steps,
            "available_tools": tools_preview,
            "instructions": (
                "Спроектируй структуру агента. tools — только имена из available_tools. "
                "workflow_steps — 3-8 этапов workflow. system_prompt — рабочий системный промпт агента."
            ),
            "response_schema": {
                "agent_name": "string",
                "purpose": "string",
                "input_schema": "object",
                "output_schema": "object",
                "tools": ["string"],
                "knowledge_bases": ["string"],
                "workflow_steps": ["string"],
                "human_approval": "boolean",
                "human_approval_rules": ["object"],
                "system_prompt": "string",
                "developer_prompt": "string",
                "test_cases": ["object"],
                "constraints": ["string"],
            },
        }
        return await self._chat_json(
            BlueprintLLMResponse,
            user_content=json.dumps(user_prompt, ensure_ascii=False),
        )

    async def _chat_json(self, model: type[T], *, user_content: str) -> T:
        if not self.configured:
            raise BuilderLLMError(
                "LLM не настроен: задайте OPENAI_API_KEY_CLAUDE или "
                "AGENT_BUILDER_FALLBACK_BASE_URL + AGENT_BUILDER_FALLBACK_MODEL"
            )

        messages = [
            {
                "role": "system",
                "content": (
                    f"{BUILDER_SYSTEM_PROMPT}\n\n"
                    "Отвечай только валидным JSON-объектом без markdown и текста вне JSON."
                ),
            },
            {"role": "user", "content": user_content},
        ]
        try:
            content = await self._request_content(messages)
            return await self._coerce_response(model, content, user_content)
        except BuilderLLMError:
            raise
        except Exception as exc:
            logger.exception("builder.llm.request_failed")
            raise BuilderLLMError(f"Ошибка вызова LLM: {exc}") from exc

    async def _coerce_response(self, model: type[T], content: str, schema_hint: str) -> T:
        try:
            data = parse_json_content(content)
            return model.model_validate(data)
        except (ValidationError, json.JSONDecodeError) as exc:
            fallback = coerce_markdown_fallback(model, content)
            if fallback is not None:
                logger.info("builder.llm.markdown_fallback", model=model.__name__)
                return fallback
            logger.warning("builder.llm.parse_failed", model=model.__name__, error=str(exc))
            try:
                return await self._repair_json(model, content, schema_hint)
            except Exception as repair_exc:
                raise BuilderLLMError(f"Не удалось разобрать ответ модели: {exc}") from repair_exc

    async def _repair_json(self, model: type[T], raw_content: str, schema_hint: str) -> T:
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "Преобразуй текст в валидный JSON-объект. "
                    "Ответь только JSON без markdown и комментариев."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Схема:\n{schema_hint}\n\n"
                    f"Текст для преобразования:\n{raw_content[:12000]}"
                ),
            },
        ]
        logger.info("builder.llm.repair_json", model=model.__name__)
        if self._fallback_configured():
            content = await self._request_lm_studio_fallback(repair_messages)
        else:
            content = await self._request_claude(repair_messages)
        data = parse_json_content(content)
        return model.model_validate(data)

    async def _request_content(self, messages: list[dict[str, str]]) -> str:
        errors: list[str] = []

        if self._claude_configured():
            try:
                content = await self._request_claude(messages)
                logger.info("builder.llm.used_provider", provider="claude")
                return content
            except Exception as exc:
                detail = str(exc)
                errors.append(f"Claude: {detail}")
                logger.warning("builder.llm.claude_failed", error=detail)

        if self._fallback_configured():
            try:
                content = await self._request_lm_studio_fallback(messages)
                logger.info(
                    "builder.llm.used_provider",
                    provider="lm_studio",
                    model=settings.AGENT_BUILDER_FALLBACK_MODEL,
                )
                return content
            except Exception as exc:
                errors.append(f"LM Studio: {exc}")
                logger.warning("builder.llm.fallback_failed", error=str(exc))

        if not errors:
            raise BuilderLLMError("LLM не настроен для конструктора агентов")
        raise BuilderLLMError("Не удалось получить ответ модели: " + "; ".join(errors))

    async def _request_claude(self, messages: list[dict[str, str]]) -> str:
        system_parts = [item["content"] for item in messages if item["role"] == "system"]
        chat_messages = [
            {"role": item["role"], "content": item["content"]}
            for item in messages
            if item["role"] in {"user", "assistant"}
        ]
        if not chat_messages:
            raise BuilderLLMError("Пустой запрос к Claude")

        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.OPENAI_API_KEY_CLAUDE or "",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.AGENT_BUILDER_CLAUDE_MODEL,
                    "max_tokens": 4096,
                    "temperature": 0.2,
                    "system": "\n\n".join(system_parts),
                    "messages": chat_messages,
                },
            )
            if response.is_error:
                raise BuilderLLMError(_http_error_detail(response))
            data = response.json()
            blocks = data.get("content") or []
            text_parts = [block.get("text", "") for block in blocks if block.get("type") == "text"]
            content = "\n".join(part for part in text_parts if part).strip()
            if not content:
                raise BuilderLLMError("Claude вернул пустой ответ")
            return content

    async def _request_lm_studio_fallback(self, messages: list[dict[str, str]]) -> str:
        base_url = (settings.AGENT_BUILDER_FALLBACK_BASE_URL or settings.LLM_GATEWAY_BASE_URL).rstrip("/")
        model = settings.AGENT_BUILDER_FALLBACK_MODEL
        headers = {"Content-Type": "application/json"}
        if settings.LLM_GATEWAY_API_KEY:
            headers["Authorization"] = f"Bearer {settings.LLM_GATEWAY_API_KEY}"

        last_error = "неизвестная ошибка"
        async with httpx.AsyncClient(timeout=300) as client:
            for payload in (
                {"temperature": 0.2, "response_format": {"type": "json_object"}},
                {"temperature": 0.2},
            ):
                try:
                    response = await client.post(
                        f"{base_url}/chat/completions",
                        headers=headers,
                        json={"model": model, "messages": messages, **payload},
                    )
                    if response.is_error:
                        last_error = _http_error_detail(response)
                        if response.status_code not in {400, 422}:
                            break
                        continue
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
                except (KeyError, IndexError) as exc:
                    last_error = str(exc)
                    break
        raise BuilderLLMError(last_error)


def _http_error_detail(exc: httpx.Response | httpx.HTTPStatusError) -> str:
    response = exc.response if isinstance(exc, httpx.HTTPStatusError) else exc
    try:
        body = response.json()
        if isinstance(body, dict):
            if body.get("error"):
                error = body["error"]
                if isinstance(error, dict):
                    return str(error.get("message") or error)
                return str(error)
            if body.get("message"):
                return str(body["message"])
    except Exception:
        pass
    return f"HTTP {response.status_code}: {response.text[:300]}"


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                result.append(text)
        elif isinstance(item, dict):
            title = item.get("title") or item.get("name") or item.get("label")
            desc = item.get("description") or item.get("desc")
            if title and desc:
                result.append(f"{title}: {desc}")
            elif title:
                result.append(str(title).strip())
        elif item is not None:
            text = str(item).strip()
            if text:
                result.append(text)
    return result


def normalize_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item]


def merge_required_elements(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {
        str(item["key"]): dict(item) for item in existing if item.get("key")
    }
    for item in incoming:
        key = item.get("key")
        if not key:
            continue
        key = str(key)
        current = by_key.get(key, {})
        merged = {**current, **item}
        if current.get("status") == "filled" and current.get("value") and not item.get("value"):
            merged["value"] = current["value"]
            merged["status"] = "filled"
        if merged.get("value"):
            merged["status"] = "filled"
        elif not merged.get("status"):
            merged["status"] = "pending"
        by_key[key] = merged
    return list(by_key.values())


def _element_has_value(item: dict[str, Any]) -> bool:
    value = item.get("value")
    return bool(value and str(value).strip())


def apply_heuristic_element_answers(
    user_message: str,
    elements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not user_message.strip() or not elements:
        return elements

    message = user_message.strip()
    lowered = message.lower()
    updated: list[dict[str, Any]] = []

    for item in elements:
        current = dict(item)
        if _element_has_value(current):
            current["status"] = "filled"
            updated.append(current)
            continue

        label = (current.get("label") or "").lower()
        key = (current.get("key") or "").lower()
        question = (current.get("question") or "").lower()
        context = f"{label} {key} {question}"

        if any(token in context for token in ("сайт", "site", "источник", "url")):
            if any(token in lowered for token in ("люб", "любые", "любой", "все сайт", "gismeteo", "яндекс")):
                current["value"] = message[:500]
                current["status"] = "filled"
        elif any(token in context for token in ("формат", "format", "вид", "вывод")):
            if "текст" in lowered:
                current["value"] = "текстовый"
                current["status"] = "filled"
            elif "таблиц" in lowered:
                current["value"] = "таблица"
                current["status"] = "filled"
        elif any(token in context for token in ("город", "регион", "локац", "city", "location")):
            if any(city in lowered for city in ("ростов", "москв", "спб", "санкт")):
                current["value"] = message[:500]
                current["status"] = "filled"

        updated.append(current)
    return updated


def pending_questions_for_elements(elements: list[dict[str, Any]]) -> list[str]:
    questions: list[str] = []
    for item in elements:
        if not item.get("required", True):
            continue
        if _element_has_value(item):
            continue
        question = (item.get("question") or item.get("label") or "").strip()
        if question and question not in questions:
            questions.append(question)
    return questions


def finalize_requirements(requirements: dict[str, Any]) -> dict[str, Any]:
    updated = dict(requirements)
    validation = None
    from app.agents.builder.validators import validate_required_elements

    validation = validate_required_elements(updated)
    updated["requirements_validation"] = validation
    if validation["valid"]:
        updated["pending_questions"] = []
    return updated


def summarize_filled_elements(elements: list[dict[str, Any]]) -> str:
    filled = [
        f"{item.get('label')}: {item.get('value')}"
        for item in elements
        if _element_has_value(item)
    ]
    if not filled:
        return ""
    return "Учтено: " + "; ".join(filled[:8]) + "."


def parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("JSON object not found", text, 0)


def coerce_markdown_fallback(model: type[BaseModel], content: str) -> BaseModel | None:
    if model is PlanLLMResponse:
        data = parse_plan_markdown(content)
        if data is not None:
            return PlanLLMResponse.model_validate(data)
    if model is ClarificationLLMResponse:
        data = parse_clarification_text(content)
        if data is not None:
            return ClarificationLLMResponse.model_validate(data)
    return None


def parse_plan_markdown(text: str) -> dict[str, Any] | None:
    steps: list[dict[str, str]] = []
    current_title: str | None = None
    current_desc: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        header = re.match(r"^#{1,4}\s+(.+)$", stripped)
        bullet = re.match(r"^(?:\d+[\.\)]\s*|[-*]\s+)(.+)$", stripped)
        if header:
            if current_title:
                steps.append(
                    {
                        "title": current_title[:120],
                        "description": (" ".join(current_desc) or current_title)[:500],
                    }
                )
            current_title = header.group(1).strip()
            current_desc = []
        elif bullet:
            title = bullet.group(1).strip()
            if title:
                steps.append({"title": title[:120], "description": title[:500]})
        elif current_title:
            current_desc.append(stripped)

    if current_title:
        steps.append(
            {
                "title": current_title[:120],
                "description": (" ".join(current_desc) or current_title)[:500],
            }
        )

    if len(steps) < 2:
        return None

    summary = next((line.strip() for line in text.splitlines() if line.strip()), "План проектирования сформирован")
    return {"steps": steps[:10], "summary": summary[:500]}


def parse_clarification_text(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    if "?" in cleaned or cleaned.lower().startswith(("уточн", "какие", "какой", "нужно ли", "укажите")):
        return {
            "ready_to_plan": False,
            "assistant_message": cleaned[:2000],
            "extracted_requirements": {},
        }
    return None


def merge_requirements(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            if key in merged:
                continue
        merged[key] = value
    return merged


def append_conversation(conversation: list[dict[str, str]], role: str, content: str) -> list[dict[str, str]]:
    updated = list(conversation)
    updated.append({"role": role, "content": content})
    return updated


builder_llm = BuilderLLM()
