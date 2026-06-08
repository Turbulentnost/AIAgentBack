from __future__ import annotations

from app.agents.tools.registry import AgentToolDefinition, register_tool
from app.agents.tools.schemas import (
    FetchPageViaUserBrowserInput,
    FetchPageViaUserBrowserOutput,
    ToolContext,
)
from app.schemas.browser_run import BrowserRunCreate
from app.services.browser_runner_service import BrowserRunnerService


async def fetch_page_via_user_browser(
    payload: FetchPageViaUserBrowserInput,
    context: ToolContext,
) -> FetchPageViaUserBrowserOutput:
    service = BrowserRunnerService(context.db)
    run = await service.create_run(
        BrowserRunCreate(
            url=payload.url,
            extract_mode=payload.extract_mode,
            reason=payload.reason,
            timeout_seconds=payload.timeout_seconds,
            task_id=context.task_id,
            agent_id=context.agent_id,
        ),
        requested_by_user_id=context.user.id,
        requested_by_agent_id=context.agent_id,
        task_id=context.task_id,
    )
    await context.db.commit()

    completed = await service.wait_for_result(run.id, payload.timeout_seconds)
    return FetchPageViaUserBrowserOutput(
        status=completed.status.value,
        url=completed.url,
        title=completed.title,
        text=completed.result_text,
        html=completed.result_html,
        tables=completed.result_tables or [],
        screenshot_document_id=completed.screenshot_object_name,
        error_message=completed.error_message,
        metadata={
            **(completed.metadata_ or {}),
            "extract_mode": completed.extract_mode,
            "browser_run_id": str(completed.id),
            "finished_at": completed.finished_at.isoformat() if completed.finished_at else None,
        },
    )


register_tool(
    AgentToolDefinition(
        name="fetch_page_via_user_browser",
        description="Открывает разрешенный URL через браузер пользователя и возвращает извлеченное содержимое.",
        agent_description=(
            "Инструмент fetch_page_via_user_browser открывает указанную страницу через браузер пользователя "
            "и возвращает извлеченное содержимое страницы. Используй этот инструмент, если информация доступна "
            "только через пользовательский браузер, корпоративную сеть, VPN, внутренний портал, web-интерфейс 1С "
            "или страницу, требующую пользовательской авторизации. Передавай только конкретный URL и цель "
            "извлечения. Не используй инструмент для произвольного обхода сайтов, массового сканирования или "
            "открытия непроверенных ссылок."
        ),
        handler=fetch_page_via_user_browser,
        input_model=FetchPageViaUserBrowserInput,
        output_model=FetchPageViaUserBrowserOutput,
        required_permissions=["browser_runs.create"],
    )
)
