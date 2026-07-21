"""Применение правил уверенности и эскалации (``confidence.yaml``, п. 4.7 ТЗ-ПЛАТФ-001).

Узел ``assess_confidence`` каждого агента вызывает ``assess_confidence`` из этого модуля:
по накопленным findings/errors рассчитывается ``data_confidence`` и флаг
``requires_human_review``. Проверка T4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # pyyaml опционален
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

_RANK = {"high": 3, "medium": 2, "low": 1}
_UNRANK = {3: "high", 2: "medium", 1: "low"}


def load_confidence_rules(agent_dir: str | Path) -> dict[str, Any]:
    """Загружает ``confidence.yaml`` агента (или пустой словарь, если pyyaml недоступен)."""

    path = Path(agent_dir) / "confidence.yaml"
    if yaml is None or not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def assess_confidence(
    state: dict[str, Any],
    *,
    base: str = "high",
) -> dict[str, Any]:
    """Рассчитывает ``data_confidence`` и ``requires_human_review`` по состоянию.

    Общая логика платформы (детализируется правилами confidence.yaml агента):
      * есть ошибки/недостающие поля  → low + human_review;
      * есть critical-finding         → не выше medium + human_review;
      * противоречие источников        → low + human_review.

    Возвращает частичное обновление состояния (data_confidence, requires_human_review).
    """

    rank = _RANK.get(base, 3)
    requires_review = bool(state.get("requires_human_review", False))

    errors = state.get("errors") or []
    findings = state.get("findings") or []

    if any(e.get("type") in ("missing_field", "source_conflict") for e in errors) or errors:
        rank = min(rank, _RANK["low"])
        requires_review = True

    if any(f.get("severity") == "critical" for f in findings):
        rank = min(rank, _RANK["medium"])
        requires_review = True

    return {
        "data_confidence": _UNRANK[rank],
        "requires_human_review": requires_review,
    }
