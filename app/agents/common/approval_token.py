"""ApprovalToken stub for Contour4 write-path (ТЗ v1.5 §9.4).

Full writeback lives in agents_contour4; this module is the platform-side
gate so any future write_* helper must validate a token before 1C calls.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ApprovalToken(BaseModel):
    token_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: str
    action_hash: str
    approver_id: str
    approver_role: str
    decision: str
    scope: str
    expires_at: datetime
    signature_ref: str


class ApprovalTokenError(Exception):
    """Raised when write is attempted without a valid ApprovalToken."""


def action_hash_for(
    *,
    role: str,
    decision: str,
    scope: str,
    correlation_id: str,
    task_id: str,
    target_id: str | None,
) -> str:
    payload = {
        "role": role,
        "decision": (decision or "").lower(),
        "scope": scope,
        "correlation_id": correlation_id,
        "task_id": task_id,
        "target_id": target_id or "",
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_approval_token(
    *,
    case_id: str,
    role: str,
    decision: str,
    scope: str,
    correlation_id: str,
    task_id: str,
    target_id: str | None = None,
    approver_id: str | None = None,
    ttl_hours: int = 24,
) -> ApprovalToken:
    decision_norm = (decision or "").lower()
    ahash = action_hash_for(
        role=role,
        decision=decision_norm,
        scope=scope,
        correlation_id=correlation_id,
        task_id=task_id,
        target_id=target_id,
    )
    token_id = str(uuid4())
    return ApprovalToken(
        token_id=token_id,
        case_id=case_id,
        action_hash=ahash,
        approver_id=approver_id or role,
        approver_role=role,
        decision=decision_norm,
        scope=scope,
        expires_at=_utc_now() + timedelta(hours=ttl_hours),
        signature_ref=f"audit://approval/{token_id}",
    )


def parse_approval_token(raw: Any) -> ApprovalToken | None:
    if raw is None:
        return None
    if isinstance(raw, ApprovalToken):
        return raw
    if isinstance(raw, dict):
        try:
            return ApprovalToken.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None
    return None


def validate_approval_token(
    token: ApprovalToken | None,
    *,
    case_id: str,
    role: str,
    decision: str,
    scope: str,
    correlation_id: str,
    task_id: str,
    target_id: str | None = None,
) -> tuple[bool, str | None]:
    if token is None:
        return False, "approval_token отсутствует"
    if token.expires_at.tzinfo is None:
        exp = token.expires_at.replace(tzinfo=UTC)
    else:
        exp = token.expires_at
    if exp < _utc_now():
        return False, "approval_token истёк"
    if token.approver_role != role:
        return False, "approval_token: неверная роль утверждающего"
    if token.case_id != case_id and token.case_id != correlation_id:
        return False, "approval_token: case_id/correlation_id не совпадает"
    if token.scope != scope:
        return False, f"approval_token: scope ожидался {scope}"
    if token.decision != (decision or "").lower():
        return False, "approval_token: decision не совпадает с human_action"
    expected = action_hash_for(
        role=role,
        decision=(decision or "").lower(),
        scope=scope,
        correlation_id=correlation_id,
        task_id=task_id,
        target_id=target_id,
    )
    if token.action_hash != expected:
        return False, "approval_token: action_hash не совпадает"
    return True, None


def assert_write_allowed(
    token: ApprovalToken | None,
    *,
    case_id: str,
    role: str,
    decision: str,
    scope: str,
    correlation_id: str,
    task_id: str,
    target_id: str | None = None,
) -> ApprovalToken:
    """Gate for future write_* tools — raise if token missing/invalid."""
    ok, err = validate_approval_token(
        token,
        case_id=case_id,
        role=role,
        decision=decision,
        scope=scope,
        correlation_id=correlation_id,
        task_id=task_id,
        target_id=target_id,
    )
    if not ok or token is None:
        raise ApprovalTokenError(err or "approval_token отсутствует")
    return token


def deny_write_without_token(
    human_payload: dict[str, Any] | None,
    *,
    case_id: str,
    role: str,
    decision: str,
    scope: str,
    correlation_id: str,
    task_id: str,
    target_id: str | None = None,
) -> dict[str, Any]:
    """Helper for write stubs: returns ok=False when token invalid/missing."""
    token = parse_approval_token((human_payload or {}).get("approval_token"))
    ok, err = validate_approval_token(
        token,
        case_id=case_id,
        role=role,
        decision=decision,
        scope=scope,
        correlation_id=correlation_id,
        task_id=task_id,
        target_id=target_id,
    )
    if ok:
        return {"ok": True, "approval_token": token.model_dump(mode="json") if token else None}
    return {"ok": False, "error": err or "approval_token отсутствует", "attempted": False}


__all__ = [
    "ApprovalToken",
    "ApprovalTokenError",
    "action_hash_for",
    "assert_write_allowed",
    "deny_write_without_token",
    "issue_approval_token",
    "parse_approval_token",
    "validate_approval_token",
]
