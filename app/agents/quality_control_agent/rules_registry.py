"""Deterministic Rule Registry (СТО-10-095 версия 05, Прил. А–В)."""

from __future__ import annotations

from typing import Any

from app.agents.quality_control_agent.schemas import (
    QualityDocumentRequirement,
    QualityFinding,
    QualitySampleRule,
)

RULES_VERSION = "1.0.0-sto-10-095-v05"

# Mandatory documents by ТМЦ category (Прил. В.3 MVP subset + п. 6.7).
CATEGORY_DOCUMENTS: dict[str, list[dict[str, Any]]] = {
    "electronics": [
        {"doc_type": "origin_confirmation", "label": "Подтверждение происхождения от официального поставщика"},
        {"doc_type": "certificate", "label": "Сертификат"},
        {"doc_type": "applicability", "label": "Сведения о применимости"},
    ],
    "metal": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "fasteners": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "cable": [
        {"doc_type": "passport", "label": "Паспорт"},
        {"doc_type": "marking", "label": "Маркировка / сечение / экраны"},
    ],
    "pipes": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "flanges": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "gaskets": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
    "drawing_parts": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
        {"doc_type": "kd_match", "label": "Соответствие КД"},
    ],
    "other": [
        {"doc_type": "certificate_or_passport", "label": "Сертификат или паспорт качества"},
    ],
}

# Industrial control deadlines in working days from presentation (Прил. В).
CATEGORY_DEADLINES_WD: dict[str, int] = {
    "metal": 2,
    "pipes": 1,
    "flanges": 1,
    "fasteners": 2,
    "gaskets": 2,
    "drawing_parts": 2,
    "cable": 2,
    "electronics": 3,
    "other": 3,
}

SCRAP_THRESHOLD_PCT = 15.0

# Lot-size brackets from Прил. Б header: 0–50 / 51–100 / от 100 шт.
# At exactly 100 шт. use the middle column (51–100).
_LOT_TIER_BOUNDARIES = (50.0, 100.0)


def _lot_tier(qty: float) -> int:
    if qty <= _LOT_TIER_BOUNDARIES[0]:
        return 0
    if qty <= _LOT_TIER_BOUNDARIES[1]:
        return 1
    return 2


# Category → sampling policy from СТО-10-095 v05 Прил. Б (инструментальный /
# выборочный объём) + п. 6.6.3 / 6.7.4.
# tiers: [0–50, 51–100, >100] %; flat_pct: fixed % for all lots.
# sample_from_each_package: п. 6.6.3 (метизы — из каждой коробки).
# allow_max_rating_1pct: п. 6.7.4 (исключение — фланцы и трубы).
_CATEGORY_SAMPLE_POLICY: dict[str, dict[str, Any]] = {
    # Прил. Б п.1 Радиоэлементы — инструментальный контроль параметров
    "electronics": {
        "tiers": (100.0, 50.0, 10.0),
        "allow_max_rating_1pct": True,
        "sto_ref": "Прил. Б п.1 Радиоэлементы",
    },
    # Прил. Б п.3 Детали литьём/мехобработкой (искл. трубы, фланцы)
    "drawing_parts": {
        "tiers": (100.0, 50.0, 10.0),
        "allow_max_rating_1pct": True,
        "sto_ref": "Прил. Б п.3 Детали литьём/мехобработкой",
    },
    # Прил. Б п.4 Резинотехнические изделия
    "gaskets": {
        "tiers": (30.0, 20.0, 10.0),
        "allow_max_rating_1pct": True,
        "sto_ref": "Прил. Б п.4 РТИ",
    },
    # Прил. Б п.5 Металлопрокат, трубы, листовой металл, фланцы — 100%
    "metal": {
        "flat_pct": 100.0,
        "allow_max_rating_1pct": True,
        "sto_ref": "Прил. Б п.5 Металлопрокат",
    },
    "pipes": {
        "flat_pct": 100.0,
        "allow_max_rating_1pct": False,
        "sto_ref": "Прил. Б п.5 Трубы; п. 6.7.4 исключение рейтинга",
    },
    "flanges": {
        "flat_pct": 100.0,
        "allow_max_rating_1pct": False,
        "sto_ref": "Прил. Б п.5 Фланцы; п. 6.7.4 исключение рейтинга",
    },
    # Прил. Б п.7 Метизы + п. 6.6.3 из каждой коробки
    "fasteners": {
        "tiers": (10.0, 5.0, 3.0),
        "allow_max_rating_1pct": True,
        "sample_from_each_package": True,
        "sto_ref": "Прил. Б п.7 Метизы; п. 6.6.3",
    },
    # Кабель не выделен отдельной строкой Прил. Б; п. 6.7.2 — контроль
    # целостности/маркировки/паспорта (как визуальные 100% в Прил. Б).
    "cable": {
        "flat_pct": 100.0,
        "allow_max_rating_1pct": True,
        "sto_ref": "п. 6.7.2 (кабель; % в Прил. Б не задан → 100% целостность)",
    },
    # Прил. Б п.12 СИЗ — 10%; «все остальные ТМЦ» в Прил. Б без % выборки.
    "other": {
        "flat_pct": 10.0,
        "allow_max_rating_1pct": True,
        "sto_ref": "Прил. Б п.12 СИЗ / прочее по умолчанию 10%",
    },
}


def _sample_basis_for_pct(pct: float) -> str:
    mapping = {
        3.0: "3pct",
        5.0: "5pct",
        10.0: "10pct",
        15.0: "15pct",
        20.0: "20pct",
        30.0: "30pct",
        50.0: "50pct",
        100.0: "100pct",
    }
    return mapping.get(float(pct), "category_default")


def _base_pct_for_policy(policy: dict[str, Any], qty: float) -> float:
    if "flat_pct" in policy:
        return float(policy["flat_pct"])
    tiers = policy["tiers"]
    return float(tiers[_lot_tier(qty)])


def normalize_category(raw: str | None) -> str:
    if not raw:
        return "other"
    key = str(raw).strip().casefold().replace("ё", "е")
    aliases = {
        "электроника": "electronics",
        "радио": "electronics",
        "electronics": "electronics",
        "металл": "metal",
        "metal": "metal",
        "крепеж": "fasteners",
        "fasteners": "fasteners",
        "кабель": "cable",
        "cable": "cable",
        "трубы": "pipes",
        "pipes": "pipes",
        "фланцы": "flanges",
        "flanges": "flanges",
        "прокладки": "gaskets",
        "gaskets": "gaskets",
        "чертежные": "drawing_parts",
        "drawing_parts": "drawing_parts",
    }
    # Exact key first — avoids accidental substring hits (e.g. metal⊂…).
    if key in aliases:
        return aliases[key]
    if key in CATEGORY_DOCUMENTS:
        return key
    # Longer markers first for free-text like «кабель силовой».
    for marker, category in sorted(aliases.items(), key=lambda item: -len(item[0])):
        if marker in key:
            return category
    return "other"


def build_mandatory_documents(
    category: str | None,
    present_docs: list[str] | None = None,
) -> list[QualityDocumentRequirement]:
    cat = normalize_category(category)
    present = {str(d).strip().casefold() for d in (present_docs or [])}
    result: list[QualityDocumentRequirement] = []
    for spec in CATEGORY_DOCUMENTS.get(cat, CATEGORY_DOCUMENTS["other"]):
        doc_type = str(spec["doc_type"])
        result.append(
            QualityDocumentRequirement(
                doc_type=doc_type,
                label=str(spec["label"]),
                mandatory=True,
                present=doc_type.casefold() in present
                or any(doc_type.casefold() in p or p in doc_type.casefold() for p in present),
            )
        )
    return result


def evaluate_document_completeness(
    category: str | None,
    present_docs: list[str] | None,
    record_id: str,
) -> list[QualityFinding]:
    docs = build_mandatory_documents(category, present_docs)
    findings: list[QualityFinding] = []
    for doc in docs:
        if doc.present:
            continue
        findings.append(
            QualityFinding(
                field=doc.doc_type,
                rule_id=f"QC.DOC.{normalize_category(category).upper()}.{doc.doc_type.upper()}",
                source_ref=f"presentation:{record_id}",
                message=f"Отсутствует обязательный документ: {doc.label}",
                severity="critical",
                suggested_fix=f"Приложить «{doc.label}»",
                current_value=None,
            )
        )
    return findings


def _is_max_supplier_rating(rating: str | float | int | None) -> bool:
    """п. 6.7.4: макс. рейтинг 40 → выборка 1% (кроме труб/фланцев)."""
    if rating is None or rating == "":
        return False
    try:
        return float(rating) >= 40
    except (TypeError, ValueError):
        normalized = str(rating).casefold()
        return normalized in {"max", "максимальный", "maximum", "40"}


def build_sample_rule(
    category: str | None,
    *,
    lot_qty: float | int | None = None,
    analog_in_nomenclature: bool | None = True,
    presentation_ref: str | None = None,
    nomenclature_ref: str | None = None,
    supplier_ref: str | None = None,
    supplier_quality_rating: str | float | int | None = None,
    require_second_sample: bool = False,
) -> QualitySampleRule:
    """Рассчитать объём выборки для конкретной поставки (СТО-10-095 Прил. Б)."""
    cat = normalize_category(category)
    policy = _CATEGORY_SAMPLE_POLICY.get(cat, _CATEGORY_SAMPLE_POLICY["other"])
    sto_ref = str(policy.get("sto_ref", "СТО-10-095"))
    note_parts = [f"Правила выборки для группы «{cat}» ({sto_ref} / СТО-10-095 v05)."]
    sample_size: int | None = None
    sample_pct: float | None = None
    sample_basis: str | None = "category_default"
    qty: float | None = None

    if presentation_ref:
        note_parts.append(f"Поставка / предъявление: {presentation_ref}.")
    if nomenclature_ref:
        note_parts.append(f"Номенклатура: {nomenclature_ref}.")
    if supplier_ref:
        note_parts.append(f"Поставщик: {supplier_ref}.")

    if lot_qty is not None:
        try:
            qty = float(lot_qty)
        except (TypeError, ValueError):
            qty = None
    if qty is not None and qty > 0:
        note_parts.append(f"Объём партии: {qty:g} шт.")
    else:
        note_parts.append(
            "Объём партии не указан — для числовой выборки задайте lot_qty / quantity поставки."
        )

    if qty is not None and qty > 0:
        allow_1pct = bool(policy.get("allow_max_rating_1pct", True))
        if allow_1pct and _is_max_supplier_rating(supplier_quality_rating):
            sample_pct = 1.0
            sample_basis = "1pct_rating"
            sample_size = max(1, int(round(qty * 0.01)))
            note_parts.append(
                f"Максимальный рейтинг поставщика — выборка 1% партии ({sample_size} шт.)."
            )
        else:
            base_pct = _base_pct_for_policy(policy, qty)
            sample_pct = base_pct
            sample_basis = _sample_basis_for_pct(base_pct)
            if base_pct >= 100.0:
                sample_size = max(1, int(round(qty)))
            else:
                sample_size = max(1, int(round(qty * (base_pct / 100.0))))
            if "tiers" in policy:
                note_parts.append(
                    f"Выборка по Прил. Б (партия → ступень 0–50/51–100/>100): "
                    f"{sample_pct:g}% ({sample_size} шт.)."
                )
            else:
                note_parts.append(
                    f"Базовая выборка {sample_pct:g}% партии ({sample_size} шт.)."
                )

    if policy.get("sample_from_each_package"):
        note_parts.append(
            "Метизы: при нескольких коробках выборку проводить из каждой коробки (п. 6.6.3)."
        )
        if sample_basis not in {"1pct_rating", "second_sample"} and sample_pct is None:
            sample_basis = "per_package"

    if analog_in_nomenclature is False:
        note_parts.append("Аналог отсутствует в номенклатуре → партия в брак.")

    second_sample_size: int | None = None
    if require_second_sample:
        sample_basis = "second_sample"
        second_sample_size = sample_size
        if second_sample_size:
            note_parts.append(
                f"Брак < {SCRAP_THRESHOLD_PCT}% — вторая выборка {second_sample_size} шт. и решение ЗДК."
            )
        else:
            note_parts.append(
                f"Брак < {SCRAP_THRESHOLD_PCT}% — требуется вторая выборка и решение ЗДК."
            )

    return QualitySampleRule(
        rule_id=f"QC.SAMPLE.{cat.upper()}",
        category=cat,
        sample_size=sample_size,
        sample_note=" ".join(note_parts),
        scrap_threshold_pct=SCRAP_THRESHOLD_PCT,
        lot_qty=qty,
        presentation_ref=presentation_ref,
        nomenclature_ref=nomenclature_ref,
        supplier_ref=supplier_ref,
        supplier_quality_rating=supplier_quality_rating,
        sample_pct=sample_pct,
        sample_basis=sample_basis,  # type: ignore[arg-type]
        require_second_sample=require_second_sample,
        second_sample_size=second_sample_size,
    )


def evaluate_scrap_decision(
    scrap_pct: float | None,
    *,
    analog_in_nomenclature: bool | None = True,
) -> dict[str, Any]:
    """Return disposition recommendation from scrap rules."""
    if analog_in_nomenclature is False:
        return {
            "disposition": "forbid",
            "rule_id": "QC.SCRAP.NO_ANALOG",
            "message": "Аналог отсутствует в номенклатуре — партия в брак.",
            "require_second_sample": False,
            "require_zdk": True,
        }
    if scrap_pct is None:
        return {
            "disposition": None,
            "rule_id": "QC.SCRAP.UNKNOWN",
            "message": "Процент брака не указан.",
            "require_second_sample": False,
            "require_zdk": False,
        }
    if scrap_pct >= SCRAP_THRESHOLD_PCT:
        return {
            "disposition": "forbid",
            "rule_id": "QC.SCRAP.GE15",
            "message": f"Брак {scrap_pct}% ≥ {SCRAP_THRESHOLD_PCT}% — браковать всю партию.",
            "require_second_sample": False,
            "require_zdk": True,
        }
    if scrap_pct > 0:
        return {
            "disposition": "commission",
            "rule_id": "QC.SCRAP.LT15",
            "message": (
                f"Брак {scrap_pct}% < {SCRAP_THRESHOLD_PCT}% — "
                "вторая выборка и решение ЗДК."
            ),
            "require_second_sample": True,
            "require_zdk": True,
        }
    return {
        "disposition": "post_and_use",
        "rule_id": "QC.SCRAP.ZERO",
        "message": "Брак 0% — разрешающий статус при полном комплекте доказательств.",
        "require_second_sample": False,
        "require_zdk": False,
    }


def control_deadline_wd(category: str | None) -> int:
    return CATEGORY_DEADLINES_WD.get(normalize_category(category), 3)


__all__ = [
    "CATEGORY_DEADLINES_WD",
    "CATEGORY_DOCUMENTS",
    "RULES_VERSION",
    "SCRAP_THRESHOLD_PCT",
    "build_mandatory_documents",
    "build_sample_rule",
    "control_deadline_wd",
    "evaluate_document_completeness",
    "evaluate_scrap_decision",
    "normalize_category",
]
