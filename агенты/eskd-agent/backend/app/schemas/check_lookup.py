from __future__ import annotations

from pydantic import BaseModel


class CheckCacheLookupResponse(BaseModel):
    found: bool
    from_marking: bool = False
    from_check_run: bool = False
    checked_in_kb: bool = False
    display_name: str | None = None
    marked_pages_count: int = 0
    has_ai_check: bool = False
    message: str | None = None
