"""HTTP-адаптер LLM Gateway — OpenAI-compatible /chat/completions (как agent_nd)."""

from __future__ import annotations

import json
import re

import httpx

from agent_pochta.config import get_settings
from agent_pochta.rules.spam_context import build_spam_llm_messages
from agent_pochta.schemas import EmailMessage, RoutingResult, SenderIdentity, SpamResult
from agent_pochta.services.llm_analyze import (
    IncomingEmailAnalysis,
    build_analyze_messages,
    parse_analyze_response,
)
from agent_pochta.services.llm_gateway import LLMGateway
from agent_pochta.services.summary import (
    build_summary_context,
    clamp_summary,
    summary_ru_system_rules,
)


def parse_json_object(text: str) -> dict:
    """Извлекает JSON-объект из ответа LLM."""
    raw = text.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


class ChatCompletionsLLMGateway(LLMGateway):
    """Реальный LLM через LM Studio / vLLM / OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        model: str = "qwen/qwen3.5-9b",
        timeout_sec: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_sec
        self._http = httpx.Client(timeout=self._timeout)

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # OpenRouter: рекомендуемые заголовки для free-моделей
        if "openrouter.ai" in self._base_url:
            headers.setdefault("HTTP-Referer", "http://localhost:8080")
            headers.setdefault("X-Title", "agent-pochta")
        return headers

    def _use_json_response_format(self) -> bool:
        """LM Studio / локальный vLLM часто отвечает 400 на response_format."""
        host = self._base_url.lower()
        if "openrouter.ai" in host or "api.openai.com" in host or "groq.com" in host:
            return True
        return False

    def classify_spam(self, email: EmailMessage) -> SpamResult:
        settings = get_settings()
        system, user = build_spam_llm_messages(email, settings)
        data = self._chat_json(system, user)
        return SpamResult(
            is_spam=bool(data.get("is_spam")),
            confidence=float(data.get("confidence", 0)),
            reason=str(data.get("reason") or ""),
        )

    def choose_department(self, email_text: str, candidates: list[dict]) -> dict:
        system = (
            "Выбери ровно один отдел из candidates для входящего письма. "
            "email_text содержит тему, тело и извлечённый текст вложений "
            "(блоки [Вложение имя_файла]). Учитывай содержимое вложений при выборе отдела. "
            'JSON: {"department_id","department_name","confidence":0..1,"reasoning"}'
        )
        user = json.dumps(
            {"email_text": email_text[:8000], "candidates": candidates},
            ensure_ascii=False,
        )
        data = self._chat_json(system, user)
        if not data and candidates:
            top = candidates[0]
            return {
                "department_id": top.get("department_id", ""),
                "department_name": top.get("department_name", ""),
                "confidence": 0.0,
                "reasoning": "LLM не вернул JSON, выбран первый кандидат",
            }
        return data

    def summarize_ru(
        self,
        email: EmailMessage,
        combined_text: str,
        *,
        routing: RoutingResult | None = None,
        sender: SenderIdentity | None = None,
        attachments_text: str = "",
    ) -> str:
        from agent_pochta.services.summary import build_summary_context, clamp_summary

        settings = get_settings()
        ctx = build_summary_context(
            email,
            combined_text,
            routing=routing,
            sender=sender,
            attachments_text=attachments_text,
            settings=settings,
        )
        min_sent = min(3, settings.summary_max_sentences)
        rules = summary_ru_system_rules(
            min_sent=min_sent, max_sent=settings.summary_max_sentences
        )
        system = (
            "Ты внутренний классификатор входящей почты НПО «Турбулентность-ДОН». "
            "Аудитория — офис-менеджер, не отправитель письма.\n"
            f"{rules}\n"
            'Ответь строго JSON: {"summary_ru": "текст обзора"}'
        )
        user = json.dumps(ctx, ensure_ascii=False)
        data = self._chat_json(system, user)
        summary = str(data.get("summary_ru") or data.get("text") or "").strip()
        if not summary:
            summary = self._chat_plain(
                f"{rules}\nКонтекст письма (JSON) ниже. Верни только текст обзора, без JSON.",
                user,
            )
        return clamp_summary(
            summary,
            max_sentences=settings.summary_max_sentences,
            max_chars=settings.summary_max_chars,
        )

    def analyze_incoming(
        self,
        email: EmailMessage,
        combined_text: str,
        candidates: list[dict],
        *,
        sender: SenderIdentity | None = None,
        skip_spam_check: bool = False,
        attachments_text: str = "",
        claim: bool = False,
    ) -> IncomingEmailAnalysis:
        settings = get_settings()
        system, user = build_analyze_messages(
            email,
            combined_text,
            candidates,
            sender=sender,
            skip_spam_check=skip_spam_check,
            attachments_text=attachments_text,
            settings=settings,
        )
        data = self._chat_json(system, user)
        analysis = parse_analyze_response(
            data,
            candidates=candidates,
            skip_spam_check=skip_spam_check,
            settings=settings,
            subject=email.subject or "",
            combined_text=combined_text,
            claim=claim,
        )
        if not analysis.summary_ru:
            ctx = build_summary_context(
                email,
                combined_text,
                routing=analysis.routing,
                sender=sender,
                attachments_text=attachments_text,
                settings=settings,
            )
            min_sent = min(3, settings.summary_max_sentences)
            rules = summary_ru_system_rules(
                min_sent=min_sent, max_sent=settings.summary_max_sentences
            )
            fallback = self._chat_plain(
                f"{rules}\nВерни только текст обзора, без JSON и без ответа отправителю.",
                json.dumps(ctx, ensure_ascii=False),
            )
            analysis = IncomingEmailAnalysis(
                spam=analysis.spam,
                routing=analysis.routing,
                summary_ru=clamp_summary(
                    fallback,
                    max_sentences=settings.summary_max_sentences,
                    max_chars=settings.summary_max_chars,
                ),
                xml_theme=analysis.xml_theme,
                partner_name=analysis.partner_name,
                process_type=analysis.process_type,
            )
        return analysis

    def _chat_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        if self._use_json_response_format():
            payload["response_format"] = {"type": "json_object"}
        try:
            content = self._post_chat(payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400 or "response_format" not in payload:
                raise
            payload.pop("response_format", None)
            content = self._post_chat(payload)
        return parse_json_object(content)

    def _chat_plain(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        return self._post_chat(payload).strip()

    def _post_chat(self, payload: dict) -> str:
        response = self._http.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=self._headers(),
        )
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"])


# Обратная совместимость имён
HttpLLMGateway = ChatCompletionsLLMGateway
