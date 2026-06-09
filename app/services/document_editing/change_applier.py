from __future__ import annotations

import re

from app.models.enums import NdChangeOperationType
from app.services.document_editing.schemas import ChangeOperationDraft


class ChangeApplier:
    def classify(self, change_text: str, old_text: str | None) -> ChangeOperationDraft:
        normalized = (change_text or "").lower()
        new_text = self.extract_new_text(change_text) or change_text
        operation_type = NdChangeOperationType.MANUAL_REVIEW
        requires_review = False

        if "аннулировать" in normalized:
            operation_type = NdChangeOperationType.ANNUL_DOCUMENT
            requires_review = True
        elif "заменить документ" in normalized:
            operation_type = NdChangeOperationType.REPLACE_DOCUMENT
            requires_review = True
        elif "исключить" in normalized:
            operation_type = NdChangeOperationType.DELETE_SECTION
        elif "добавить строк" in normalized:
            operation_type = NdChangeOperationType.ADD_TABLE_ROW
        elif "таблиц" in normalized:
            operation_type = NdChangeOperationType.UPDATE_TABLE
        elif "приложени" in normalized:
            operation_type = NdChangeOperationType.REPLACE_APPENDIX
        elif "ссылк" in normalized:
            operation_type = NdChangeOperationType.UPDATE_REFERENCE
        elif "добавить" in normalized or "дополнить" in normalized:
            operation_type = NdChangeOperationType.INSERT_AFTER
        elif "изложить пункт" in normalized or "пункт" in normalized:
            operation_type = NdChangeOperationType.REPLACE_PARAGRAPH
        elif "изложить раздел" in normalized or "раздел" in normalized:
            operation_type = NdChangeOperationType.REPLACE_SECTION
        elif "заменить" in normalized:
            operation_type = NdChangeOperationType.REPLACE_PARAGRAPH
        else:
            requires_review = True

        return ChangeOperationDraft(
            operation_type=operation_type,
            old_text=old_text,
            new_text=new_text,
            requires_manual_review=requires_review,
        )

    def apply_to_text(self, *, old_text: str | None, new_text: str, operation_type: NdChangeOperationType) -> str:
        if operation_type == NdChangeOperationType.DELETE_SECTION:
            return ""
        if operation_type in {NdChangeOperationType.INSERT_AFTER, NdChangeOperationType.ADD_TABLE_ROW}:
            return f"{old_text or ''}\n\n{new_text}".strip()
        if operation_type == NdChangeOperationType.INSERT_BEFORE:
            return f"{new_text}\n\n{old_text or ''}".strip()
        return new_text

    def extract_new_text(self, change_text: str) -> str | None:
        patterns = [
            r"в следующей редакции[:：]\s*(.+)",
            r"изложить.*?[:：]\s*(.+)",
            r"заменить.*?на\s+[«\"](.+?)[»\"]",
        ]
        for pattern in patterns:
            match = re.search(pattern, change_text or "", flags=re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        return None
