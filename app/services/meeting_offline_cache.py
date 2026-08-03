from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.tools.onec.service_memo_shared import APPROVED_STATUS, UNAPPROVED_STATUS

OFFLINE_CACHE_HISTORY_MARKERS = (
    "offline cache",
    "загружена из excel",
)


def is_offline_cache_detail(detail: dict[str, Any] | None) -> bool:
    """True для СЗ, загруженных в Redis без согласования в 1С."""
    if not detail:
        return False
    cache_source = str(detail.get("cache_source") or "").strip().lower()
    if cache_source in {"excel", "offline", "redis", "seed"}:
        return True
    for item in detail.get("history") or []:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip().lower()
        if any(marker in message for marker in OFFLINE_CACHE_HISTORY_MARKERS):
            return True
    return False


def append_detail_history(detail: dict[str, Any], message: str) -> dict[str, Any]:
    patched = dict(detail)
    history = list(patched.get("history") or [])
    history.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }
    )
    patched["history"] = history
    return patched


def build_offline_approve_result(
    detail: dict[str, Any],
    *,
    ref_key: str,
    approver_fio: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    previous_status = str(detail.get("status") or UNAPPROVED_STATUS)
    number = detail.get("number")
    memo_number = str(number or "").strip() or "?"
    sto_ready = bool(detail.get("sto_ready"))
    sto_issues = list(detail.get("sto_issues") or [])

    if previous_status == APPROVED_STATUS:
        return {
            "ref_key": ref_key,
            "number": number,
            "status": APPROVED_STATUS,
            "previous_status": previous_status,
            "changed": False,
            "already_approved": True,
            "sto_ready": sto_ready,
            "sto_issues": sto_issues,
            "ud_recommendation": detail.get("agent_recommendation"),
            "approver_fio": approver_fio,
            "comment": comment,
            "message": f"Служебная записка №{memo_number} уже согласована.",
        }

    return {
        "ref_key": ref_key,
        "number": number,
        "status": APPROVED_STATUS,
        "previous_status": previous_status,
        "changed": True,
        "already_approved": False,
        "sto_ready": sto_ready,
        "sto_issues": sto_issues,
        "ud_recommendation": detail.get("agent_recommendation"),
        "approver_fio": approver_fio,
        "comment": comment,
        "message": f"Служебная записка №{memo_number} согласована (offline cache).",
    }
