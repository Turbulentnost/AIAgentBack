from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MemoValidationIssue:
    field: str
    severity: str
    message: str


def validate_meeting_memo_document(document: dict[str, Any] | None) -> list[MemoValidationIssue]:
    """Проверяет структуру служебной записки из 1С перед организацией совещания."""
    if not document:
        return [
            MemoValidationIssue(
                field="memo",
                severity="error",
                message="Служебная записка не загружена",
            )
        ]

    memo = document.get("memo") or {}
    issues: list[MemoValidationIssue] = []

    if memo.get("DeletionMark"):
        issues.append(
            MemoValidationIssue(
                field="DeletionMark",
                severity="error",
                message="Документ помечен на удаление",
            )
        )

    if not memo.get("Ref_Key"):
        issues.append(
            MemoValidationIssue(
                field="Ref_Key",
                severity="error",
                message="Не указан идентификатор документа (Ref_Key)",
            )
        )

    if not memo.get("Number"):
        issues.append(
            MemoValidationIssue(
                field="Number",
                severity="error",
                message="Не указан номер служебной записки",
            )
        )

    if not memo.get("Date"):
        issues.append(
            MemoValidationIssue(
                field="Date",
                severity="warning",
                message="Не указана дата служебной записки",
            )
        )

    participants = document.get("participants") or []
    if not participants and not _participants_in_tabular(document):
        issues.append(
            MemoValidationIssue(
                field="participants",
                severity="error",
                message="Не указаны участники совещания",
            )
        )

    if not memo.get("ТемаСлужебнойЗаписки") and not memo.get("ТемаСлужебнойЗаписки_Key"):
        issues.append(
            MemoValidationIssue(
                field="ТемаСлужебнойЗаписки",
                severity="warning",
                message="Не указана тема служебной записки",
            )
        )

    return [item for item in issues if item.severity == "error"] or issues


def _participants_in_tabular(document: dict[str, Any]) -> bool:
    sections = document.get("tabular_sections") or {}
    for rows in sections.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if any("Участник" in key or "ФИО" in key for key in row):
                return True
    return False
