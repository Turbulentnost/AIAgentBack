from __future__ import annotations

DESIGN_STAGES: tuple[tuple[str, str], ...] = (
    ("understand_goal", "Понимание цели"),
    ("classify_agent_type", "Определение типа агента"),
    ("ask_clarifying_questions", "Сбор всех требований"),
    ("create_plan", "Планирование"),
    ("execute_plan_step", "Выполнение плана"),
    ("propose_structure", "Формирование blueprint"),
    ("validate_blueprint", "Проверка структуры"),
    ("prepare_preview", "Пробный запуск агента"),
    ("wait_user_review", "Согласование"),
)


def build_design_stages(current_stage: str | None, status: str | None) -> list[dict[str, str]]:
    order = [stage_id for stage_id, _ in DESIGN_STAGES]
    if status == "approved":
        current_index = len(order)
    elif current_stage in order:
        current_index = order.index(current_stage)
    else:
        current_index = -1

    stages: list[dict[str, str]] = []
    for index, (stage_id, label) in enumerate(DESIGN_STAGES):
        if current_index < 0:
            stage_status = "pending"
        elif index < current_index:
            stage_status = "completed"
        elif index == current_index:
            stage_status = "running"
        else:
            stage_status = "pending"
        stages.append({"id": stage_id, "label": label, "status": stage_status})
    return stages
