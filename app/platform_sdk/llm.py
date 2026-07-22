"""LLM Gateway платформы для узлов агентов.

Боевой режим: если привязан :class:`AgentRuntime` с LLM-шлюзом, вызов идёт в
``llm_gateway.chat(...)`` (OpenAI-совместимый) через мост sync→async.
Резервный режим (runtime не привязан / шлюз недоступен): детерминированный мок
с пустым содержимым — графы проходят end-to-end без боевого LLM.
"""

from __future__ import annotations

import json
from typing import Any


def _mock(prompt: str, system: str, mask_pii: bool, json_mode: bool) -> dict[str, Any]:
    return {
        "_mock": True,
        "content": "",
        "json": {} if json_mode else None,
        "pii_masked": mask_pii,
        "prompt_chars": len(prompt),
        "system_chars": len(system),
    }


def llm_complete(
    prompt: str,
    *,
    system: str = "",
    mask_pii: bool = True,
    json_mode: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Вызов LLM. Боевой шлюз при наличии runtime, иначе мок.

    ``mask_pii`` — намерение маскировать ПДн. В платформе маскирование пока не
    реализовано, поэтому для боевого вызова ``pii_masked=False`` (честно).
    """

    try:
        from app.agents.omto_role_agents.runtime_context import current_runtime, run_async
    except Exception:  # noqa: BLE001 — контекст недоступен
        return _mock(prompt, system, mask_pii, json_mode)

    runtime = current_runtime()
    if runtime is None or runtime.llm is None:
        return _mock(prompt, system, mask_pii, json_mode)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    call_kwargs: dict[str, Any] = {}
    if json_mode:
        call_kwargs["response_format"] = {"type": "json_object"}
    call_kwargs.update({k: v for k, v in kwargs.items() if k in {"temperature", "max_tokens"}})

    try:
        response = run_async(
            runtime.llm.chat(messages, model=runtime.default_model, **call_kwargs)
        )
        content = str(response["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001 — сбой LLM не роняет граф
        return {
            "_error": str(exc),
            "unavailable": True,
            "content": "",
            "json": {} if json_mode else None,
            "pii_masked": False,
        }

    parsed: Any = None
    if json_mode:
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            parsed = None

    return {
        "_real": True,
        "content": content,
        "json": parsed,
        "pii_masked": False,  # маскирование ПДн в платформе не реализовано
        "prompt_chars": len(prompt),
        "system_chars": len(system),
    }
