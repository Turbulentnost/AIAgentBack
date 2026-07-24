"""Urgency wave bucketing for multi-case procurement strategy.

Waves (critical → medium → late): earliest ``required_date`` first; missing dates last.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

WaveLabel = Literal["critical", "medium", "late"]
WaveMode = Literal["urgent", "economy"]

WAVE_LABELS_RU: dict[WaveLabel, str] = {
    "critical": "критично",
    "medium": "средне",
    "late": "поздно",
}

DEFAULT_CRITICAL_DAYS = 7
DEFAULT_MEDIUM_DAYS = 21


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def case_required_date(case: Any) -> date | None:
    """Earliest required_date across case positions (case-level fallback)."""
    if isinstance(case, dict):
        case_required = _parse_date(case.get("required_date"))
        positions = case.get("positions") or []
        case_id = str(case.get("id") or case.get("case_id") or "")
    else:
        case_required = _parse_date(getattr(case, "required_date", None))
        positions = getattr(case, "positions", None) or []
        case_id = str(getattr(case, "id", "") or "")

    best: date | None = None
    for position in positions:
        if isinstance(position, dict):
            if position.get("cancelled"):
                continue
            req = _parse_date(position.get("required_date")) or case_required
        else:
            if getattr(position, "cancelled", False):
                continue
            req = _parse_date(getattr(position, "required_date", None)) or case_required
        if req is None:
            continue
        if best is None or req < best:
            best = req
    if best is None:
        best = case_required
    # Touch case_id so empty stubs still participate in sort keys downstream.
    _ = case_id
    return best


def _case_id(case: Any) -> str:
    if isinstance(case, dict):
        return str(case.get("id") or case.get("case_id") or "")
    return str(getattr(case, "id", "") or "")


def classify_urgency(
    required: date | None,
    *,
    today: date | None = None,
    critical_days: int = DEFAULT_CRITICAL_DAYS,
    medium_days: int = DEFAULT_MEDIUM_DAYS,
) -> WaveLabel:
    """Map a deadline to critical / medium / late (null → late)."""
    if required is None:
        return "late"
    as_of = today or date.today()
    delta = (required - as_of).days
    if delta <= critical_days:
        return "critical"
    if delta <= medium_days:
        return "medium"
    return "late"


def wave_mode_for_label(label: WaveLabel) -> WaveMode:
    """Urgent for critical/medium; economy for late (cheaper alternate suppliers)."""
    if label == "late":
        return "economy"
    return "urgent"


def bucket_urgency_waves(
    cases: list[Any],
    *,
    today: date | None = None,
    critical_days: int = DEFAULT_CRITICAL_DAYS,
    medium_days: int = DEFAULT_MEDIUM_DAYS,
) -> dict[str, Any]:
    """
    Deterministic urgency buckets for the manager queue.

    Returns waves ordered critical → medium → late, plus case→wave index.
    """
    as_of = today or date.today()
    rows: list[dict[str, Any]] = []
    for case in cases:
        cid = _case_id(case)
        if not cid:
            continue
        required = case_required_date(case)
        label = classify_urgency(
            required,
            today=as_of,
            critical_days=critical_days,
            medium_days=medium_days,
        )
        rows.append(
            {
                "case_id": cid,
                "required_date": required.isoformat() if required else None,
                "label": label,
                "mode": wave_mode_for_label(label),
                "_sort": (
                    0 if required is not None else 1,
                    required or date.max,
                    cid,
                ),
            }
        )
    rows.sort(key=lambda item: item["_sort"])

    buckets: dict[WaveLabel, list[dict[str, Any]]] = {
        "critical": [],
        "medium": [],
        "late": [],
    }
    for row in rows:
        clean = {k: v for k, v in row.items() if not k.startswith("_")}
        buckets[row["label"]].append(clean)

    waves: list[dict[str, Any]] = []
    case_wave: dict[str, str] = {}
    wave_index = 0
    for label in ("critical", "medium", "late"):
        members = buckets[label]
        if not members:
            continue
        wave_index += 1
        wave_id = f"wave-{wave_index}-{label}"
        mode = wave_mode_for_label(label)
        for member in members:
            member["wave_id"] = wave_id
            case_wave[member["case_id"]] = wave_id
        waves.append(
            {
                "wave_id": wave_id,
                "wave_index": wave_index,
                "label": label,
                "label_ru": WAVE_LABELS_RU[label],
                "mode": mode,
                "case_ids": [m["case_id"] for m in members],
                "cases": members,
                "reason": (
                    f"Срок ≤ {critical_days} дн."
                    if label == "critical"
                    else (
                        f"Срок ≤ {medium_days} дн."
                        if label == "medium"
                        else "Поздний срок или дата не задана"
                    )
                ),
            }
        )

    return {
        "today": as_of.isoformat(),
        "critical_days": critical_days,
        "medium_days": medium_days,
        "waves": waves,
        "case_wave": case_wave,
        "cases": [
            {k: v for k, v in row.items() if not k.startswith("_")} for row in rows
        ],
        "summary": {
            "waves_count": len(waves),
            "cases_count": len(rows),
            "critical_count": len(buckets["critical"]),
            "medium_count": len(buckets["medium"]),
            "late_count": len(buckets["late"]),
        },
        "source": "deterministic",
    }


def allocate_queue_with_waves(
    cases: list[Any],
    *,
    bank: Any | None = None,
    today: date | None = None,
    critical_days: int = DEFAULT_CRITICAL_DAYS,
    medium_days: int = DEFAULT_MEDIUM_DAYS,
) -> dict[str, Any]:
    """Multi-case bank allocate + urgency wave buckets (queue strategy input)."""
    from app.agents.procurement_manager_agent.allocation import allocate_materials_by_deadline

    allocation = allocate_materials_by_deadline(cases, bank=bank)
    waves = bucket_urgency_waves(
        cases,
        today=today,
        critical_days=critical_days,
        medium_days=medium_days,
    )
    # Annotate allocation case rows with wave_id / mode.
    case_wave = waves.get("case_wave") or {}
    wave_meta = {
        w["wave_id"]: w for w in waves.get("waves") or [] if isinstance(w, dict)
    }
    for case_row in allocation.get("cases") or []:
        if not isinstance(case_row, dict):
            continue
        cid = str(case_row.get("case_id") or "")
        wid = case_wave.get(cid)
        case_row["wave_id"] = wid
        meta = wave_meta.get(wid or "") or {}
        case_row["wave_label"] = meta.get("label")
        case_row["wave_mode"] = meta.get("mode")
    for line in allocation.get("lines") or []:
        if not isinstance(line, dict):
            continue
        cid = str(line.get("case_id") or "")
        wid = case_wave.get(cid)
        line["wave_id"] = wid
        meta = wave_meta.get(wid or "") or {}
        line["wave_label"] = meta.get("label")
        line["wave_mode"] = meta.get("mode")

    return {
        **allocation,
        "waves": waves,
        "horizon_end": (as_of_plus(today, medium_days)).isoformat(),
    }


def as_of_plus(today: date | None, days: int) -> date:
    return (today or date.today()) + timedelta(days=int(days))


__all__ = [
    "DEFAULT_CRITICAL_DAYS",
    "DEFAULT_MEDIUM_DAYS",
    "WAVE_LABELS_RU",
    "allocate_queue_with_waves",
    "bucket_urgency_waves",
    "case_required_date",
    "classify_urgency",
    "wave_mode_for_label",
]
