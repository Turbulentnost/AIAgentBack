from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).resolve().parent / "tasks_llm_dataset_correct_simplified_result.json"


def load_dataset(path: Path | None = None) -> list[dict[str, Any]]:
    dataset_path = path or DATASET_PATH
    with dataset_path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"Ожидался JSON-массив в {dataset_path}")
    return data


def extract_comment_text(execution_result: Any) -> str:
    if execution_result is None:
        return ""
    if isinstance(execution_result, dict):
        raw = execution_result.get("raw")
        if raw is None:
            return ""
        return str(raw).strip()
    return str(execution_result).strip()


def record_to_agent_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(record.get("id", "")),
        "task_name": str(record.get("task_name", "")).strip(),
        "execution_result": record.get("execution_result"),
        "document_ids": [],
    }


def get_records(
    *,
    path: Path | None = None,
    record_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    records = load_dataset(path)
    if record_ids:
        wanted = set(record_ids)
        records = [item for item in records if item.get("id") in wanted]
    if limit is not None:
        records = records[:limit]
    return records
