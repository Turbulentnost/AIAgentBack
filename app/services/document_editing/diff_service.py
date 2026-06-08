from __future__ import annotations

import difflib

from app.services.document_editing.schemas import DiffItem


class DiffService:
    def generate(self, *, section_number: str | None, old_text: str | None, new_text: str | None) -> list[DiffItem]:
        return [
            DiffItem(
                section_number=section_number,
                old_text=old_text or "",
                new_text=new_text or "",
            )
        ]

    def unified_text(self, old_text: str | None, new_text: str | None) -> str:
        old_lines = (old_text or "").splitlines()
        new_lines = (new_text or "").splitlines()
        return "\n".join(difflib.unified_diff(old_lines, new_lines, fromfile="было", tofile="стало", lineterm=""))
